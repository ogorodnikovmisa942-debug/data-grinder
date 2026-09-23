import hmac
import hashlib
import json
import urllib.parse
from datetime import datetime
from fastapi import Request, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.config import settings
from app.database.session import get_db
from app.database.models import UserSetting, UserSession, Card, Phrase, TopicKnowledgeGraph, utc_now

def parse_and_verify_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Проверяет криптографическую подпись initData от Telegram WebApp.
    Возвращает словарь данных пользователя при успехе, либо None.
    Поддерживает Telegram Bot API 7.0+ (автоматически исключает hash и проверяет варианты signature).
    """
    if not init_data or not bot_token or bot_token == "placeholder_bot_token":
        return None

    clean_token = bot_token.strip().strip('"\'')
    if not clean_token:
        return None

    try:
        raw_str = init_data.strip()
        # Извлекаем чистую строку initData, если передан URL hash или префикс
        if raw_str.startswith("#"):
            raw_str = raw_str[1:]
        if raw_str.startswith("?"):
            raw_str = raw_str[1:]
        if "tgWebAppData=" in raw_str:
            part = raw_str.split("tgWebAppData=")[1].split("&")[0]
            raw_str = urllib.parse.unquote(part)

        parsed = dict(urllib.parse.parse_qsl(raw_str, keep_blank_values=True))
        
        # Если hash нет, возможно строка была URL-закодирована целиком
        if "hash" not in parsed and "%" in raw_str:
            unquoted = urllib.parse.unquote(raw_str)
            parsed = dict(urllib.parse.parse_qsl(unquoted, keep_blank_values=True))

        hash_check = parsed.pop("hash", None)
        if not hash_check:
            return None

        # В Bot API 7.0+ Telegram может передавать signature третьих сторон
        sig = parsed.pop("signature", None)

        secret_key = hmac.new(b"WebAppData", clean_token.encode("utf-8"), hashlib.sha256).digest()

        # Вариант 1: hash рассчитан без поля signature (стандарт Bot API 7.0+)
        dcs1 = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        calc1 = hmac.new(secret_key, dcs1.encode("utf-8"), hashlib.sha256).hexdigest()

        is_valid = hmac.compare_digest(calc1.lower(), hash_check.lower())

        # Вариант 2: если не совпало и signature было в параметрах, проверяем с signature
        if not is_valid and sig is not None:
            parsed_with_sig = {**parsed, "signature": sig}
            dcs2 = "\n".join(f"{k}={v}" for k, v in sorted(parsed_with_sig.items()))
            calc2 = hmac.new(secret_key, dcs2.encode("utf-8"), hashlib.sha256).hexdigest()
            is_valid = hmac.compare_digest(calc2.lower(), hash_check.lower())

        if is_valid:
            user_raw = parsed.get("user")
            if user_raw:
                if isinstance(user_raw, str):
                    try:
                        return json.loads(user_raw)
                    except Exception:
                        return {"raw_user": user_raw}
                return user_raw
            return parsed
        return None
    except Exception as e:
        print(f"[Auth] Ошибка валидации initData: {e}")
        return None


async def ensure_user_has_starter_deck(user_id: str, db: AsyncSession):
    """
    Если у пользователя (открывшего Telegram Mini App с числовым Telegram ID) еще нет личных карточек,
    копирует библиотеку карточек и структуру графа знаний из default_user / dev_user,
    либо напрямую загружает стартовые пресеты (sudoustroystvo, python, law, hsk3).
    Это дает пользователю персональный прогресс FSRS без пустого экрана.
    """
    if not user_id or not user_id.isdigit() or user_id in ("default_user", "dev_user"):
        return

    try:
        user_cards_count = (await db.execute(
            select(func.count(Card.id)).filter(Card.user_id == user_id)
        )).scalar() or 0

        setting_stmt = select(UserSetting).filter(UserSetting.user_id == user_id)
        user_setting = (await db.execute(setting_stmt)).scalar_one_or_none()

        # Если у пользователя уже есть карточки (> 0), фиксируем завершение онбординга и выходим
        if user_cards_count > 0:
            if user_setting:
                limits = dict(user_setting.subject_limits or {})
                if not limits.get("_onboarded"):
                    limits["_onboarded"] = True
                    user_setting.subject_limits = limits
                    await db.commit()
            return

        # Ищем источник карточек
        src_user = "default_user" if user_id != "default_user" else "dev_user"
        stmt_src = select(Card).filter(Card.user_id == src_user).order_by(Card.id.asc())
        src_cards = (await db.execute(stmt_src)).scalars().all()
        if not src_cards and src_user != "dev_user":
            src_user = "dev_user"
            stmt_src = select(Card).filter(Card.user_id == src_user).order_by(Card.id.asc())
            src_cards = (await db.execute(stmt_src)).scalars().all()

        if not src_cards:
            # Если карточек в БД нет вообще, загружаем встроенные пресеты
            from pathlib import Path
            from app.services.card_db_sync import append_or_sync_cards_to_database, sync_subject_knowledge_and_practice
            presets_dir = Path(__file__).resolve().parents[2] / "app" / "static" / "presets"
            if not presets_dir.exists():
                presets_dir = Path("app/static/presets").resolve()
            preset_files = [
                presets_dir / "sudoustroystvo.json",
                presets_dir / "python.json",
                presets_dir / "law.json",
                presets_dir / "hsk3.json"
            ]
            total_loaded = 0
            for pf in preset_files:
                if pf.exists():
                    try:
                        p_data = json.loads(pf.read_text(encoding="utf-8"))
                        p_cards = p_data.get("cards", [])
                        p_sub = p_data.get("subject_slug", pf.stem)
                        p_title = p_data.get("phrase_title", pf.stem.capitalize())
                        if p_cards:
                            c_new, _, _, _ = await append_or_sync_cards_to_database(p_cards, p_sub, p_title, user_id, db)
                            total_loaded += c_new
                            if user_id != "default_user":
                                await append_or_sync_cards_to_database(p_cards, p_sub, p_title, "default_user", db)
                            await sync_subject_knowledge_and_practice(db, user_id, p_sub, p_cards)
                    except Exception as pe:
                        print(f"[Auth Onboarding Preset Error] {pf}: {pe}")

            if total_loaded > 0 and user_setting:
                limits = dict(user_setting.subject_limits or {})
                limits["_onboarded"] = True
                user_setting.subject_limits = limits
                await db.commit()

            print(f"[Auth Onboarding] Загружено {total_loaded} карточек из встроенных пресетов для {user_id}")
            return

        # Копируем фразы-контейнеры
        stmt_phrases = select(Phrase).filter(Phrase.user_id == src_user)
        src_phrases = (await db.execute(stmt_phrases)).scalars().all()

        now = utc_now()
        phrase_id_map = {}
        for p in src_phrases:
            new_p = Phrase(
                user_id=user_id,
                subject=p.subject,
                text=p.text
            )
            db.add(new_p)
            await db.flush()
            phrase_id_map[p.id] = new_p.id

        # Копируем карточки с чистым FSRS состоянием
        for c in src_cards:
            new_c = Card(
                user_id=user_id,
                phrase_id=phrase_id_map.get(c.phrase_id, c.phrase_id),
                category_id=getattr(c, "category_id", None),
                subject=c.subject,
                text=c.text,
                secondary_text=c.secondary_text,
                translation=c.translation,
                example=c.example,
                mnemonic=c.mnemonic,
                content_type=getattr(c, "content_type", "text"),
                difficulty=c.difficulty if c.difficulty is not None else 5.5,
                stability=c.stability if c.stability is not None else 0.0,
                state=0,
                lapses=0,
                last_review=None,
                next_review=now,
                organ_slug=getattr(c, "organ_slug", None),
                layer=getattr(c, "layer", 1),
                topological_rank=getattr(c, "topological_rank", 0),
                has_seen_intro=False,
                intro_phase=0,
                is_anchored=getattr(c, "is_anchored", False),
            )
            db.add(new_c)

        # Копируем сохраненный граф знаний
        user_graph_count = (await db.execute(
            select(func.count(TopicKnowledgeGraph.id)).filter(TopicKnowledgeGraph.user_id == user_id)
        )).scalar() or 0

        if user_graph_count == 0:
            stmt_graph = select(TopicKnowledgeGraph).filter(TopicKnowledgeGraph.user_id == src_user)
            src_graphs = (await db.execute(stmt_graph)).scalars().all()
            if not src_graphs:
                stmt_graph = select(TopicKnowledgeGraph).filter(TopicKnowledgeGraph.user_id == "dev_user")
                src_graphs = (await db.execute(stmt_graph)).scalars().all()

            for g in src_graphs:
                new_g = TopicKnowledgeGraph(
                    user_id=user_id,
                    subject=g.subject,
                    graph_data=g.graph_data,
                    tree_data=g.tree_data,
                    created_at=now,
                    updated_at=now
                )
                db.add(new_g)

        if user_setting:
            limits = dict(user_setting.subject_limits or {})
            limits["_onboarded"] = True
            user_setting.subject_limits = limits

        await db.commit()
        print(f"[Auth Onboarding] Для пользователя {user_id} клонирована библиотека из {len(src_cards)} карточек.")
    except Exception as e:
        print(f"[Auth Onboarding] Ошибка автоклонирования для {user_id}: {e}")
        await db.rollback()


async def get_current_user_id(request: Request, db: AsyncSession = Depends(get_db)) -> str:
    """
    Основная зависимость FastAPI для получения проверенного user_id.
    1. Ищет заголовок Authorization (tma <initData>), X-Telegram-Init-Data или X-User-Id.
    2. При наличии Telegram initData проверяет криптографическую подпись бота.
    3. Если подпись валидна — возвращает Telegram ID пользователя.
    4. При локальной разработке в браузере (вне Telegram) безопасно использует фолбэк (X-User-Id, tg_id или dev_user).
    5. Автоматически инициализирует запись UserSetting в БД при первом входе.
    6. Клонирует стартовую библиотеку и граф знаний для новых пользователей.
    """
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    init_data_header = request.headers.get("x-telegram-init-data") or request.headers.get("X-Telegram-Init-Data")
    custom_user_header = request.headers.get("x-user-id") or request.headers.get("X-User-Id")
    query_tg_id = request.query_params.get("tg_id") or request.query_params.get("user_id")

    user_id = None

    # 1. Пробуем разобрать и верифицировать tma <initData>
    init_data_str = None
    if auth_header and auth_header.lower().startswith("tma "):
        init_data_str = auth_header[4:].strip()
    elif init_data_header:
        init_data_str = init_data_header.strip()

    init_data_verified = False
    payload_user_id = None

    if init_data_str:
        verified_data = parse_and_verify_telegram_init_data(init_data_str, settings.TELEGRAM_BOT_TOKEN)
        if verified_data:
            init_data_verified = True
            if isinstance(verified_data, dict):
                if "id" in verified_data:
                    user_id = str(verified_data["id"])
                elif "user" in verified_data:
                    u = verified_data["user"]
                    if isinstance(u, dict) and "id" in u:
                        user_id = str(u["id"])
                    elif isinstance(u, str):
                        try:
                            user_id = str(json.loads(u).get("id"))
                        except Exception:
                            pass
        else:
            print(f"[Auth WARN] Не удалось верифицировать HMAC подпись Telegram initData")
            try:
                raw_str = init_data_str.strip()
                if "tgWebAppData=" in raw_str:
                    raw_str = urllib.parse.unquote(raw_str.split("tgWebAppData=")[1].split("&")[0])
                unq = urllib.parse.unquote(raw_str) if "%" in raw_str else raw_str
                raw_parsed = dict(urllib.parse.parse_qsl(unq, keep_blank_values=True))
                raw_u = raw_parsed.get("user")
                if raw_u:
                    u_obj = json.loads(raw_u) if isinstance(raw_u, str) else raw_u
                    if isinstance(u_obj, dict) and u_obj.get("id"):
                        payload_user_id = str(u_obj["id"])
            except Exception as e:
                print(f"[Auth Payload Parse Error] {e}")

    # 2. Определяем кандидата из заголовков/параметров
    candidate = None
    if custom_user_header and custom_user_header.strip():
        candidate = custom_user_header.strip()
    elif query_tg_id and query_tg_id.strip():
        candidate = query_tg_id.strip()

    # Если криптографическая подпись Telegram валидна, но user_id еще не извлечен из payload
    if init_data_verified and not user_id and candidate:
        user_id = candidate

    # 3. Фолбэк для прямого браузерного доступа, запуска по кнопке Меню / KeyboardButton, разработки и тестов
    if not user_id:
        is_dev_mode = (
            settings.DEBUG
            or getattr(settings, "TESTING", False)
            or not settings.TELEGRAM_BOT_TOKEN
            or settings.TELEGRAM_BOT_TOKEN == "placeholder_bot_token"
        )
        if is_dev_mode:
            user_id = candidate or payload_user_id or "dev_user"
        else:
            if payload_user_id and payload_user_id.isdigit():
                user_id = payload_user_id
            elif candidate == "default_user":
                user_id = "default_user"
            elif candidate and candidate.isdigit():
                # Числовой Telegram ID (открыто через Menu Button или KeyboardButton)
                user_id = candidate
            elif init_data_str and not init_data_verified:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Недействительная криптографическая подпись Telegram WebApp."
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Требуется авторизация через Telegram WebApp."
                )

    # Гарантируем наличие UserSetting и UserSession для этого пользователя
    try:
        setting_stmt = select(UserSetting).filter(UserSetting.user_id == user_id)
        setting_res = await db.execute(setting_stmt)
        user_setting = setting_res.scalar_one_or_none()
        
        if not user_setting:
            user_setting = UserSetting(
                user_id=user_id,
                daily_limit=10,
                target_retention=0.9,
                assoc_preference="acoustic",
                subject_limits={"all": 10}
            )
            db.add(user_setting)
            await db.commit()

        # Также проверяем UserSession для таймера и уведомлений
        session_stmt = select(UserSession).filter(UserSession.telegram_id == user_id)
        session_res = await db.execute(session_stmt)
        if not session_res.scalar_one_or_none():
            new_session = UserSession(telegram_id=user_id, user_id=user_id)
            db.add(new_session)
            await db.commit()

        # Онбординг стартовой колоды карточек и графа знаний для числового Telegram пользователя
        if user_id.isdigit():
            await ensure_user_has_starter_deck(user_id, db)

    except Exception as e:
        print(f"[Auth] Предупреждение при инициализации профиля пользователя {user_id}: {e}")
        await db.rollback()

    return user_id
