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
    Поддерживает Telegram Bot API 7.0+ (автоматически исключает hash и signature перед проверкой).
    """
    if not init_data or not bot_token or bot_token == "placeholder_bot_token":
        return None

    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        hash_check = parsed.pop("hash", None)
        parsed.pop("signature", None)  # В Bot API 7.0+ Telegram добавляет signature третьих сторон, не входящую в hash
        if not hash_check:
            return None

        # Формируем строку проверки: отсортированные по алфавиту пары key=value через \n
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        
        # Секретный ключ вычисляется как HMAC-SHA256(b"WebAppData", bot_token)
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if hmac.compare_digest(calculated_hash, hash_check):
            user_raw = parsed.get("user")
            if user_raw:
                if isinstance(user_raw, str):
                    return json.loads(user_raw)
                return user_raw
            return parsed
        return None
    except Exception as e:
        print(f"[Auth] Ошибка валидации initData: {e}")
        return None


async def ensure_user_has_starter_deck(user_id: str, db: AsyncSession):
    """
    Если у пользователя (открывшего Telegram Mini App с числовым ID) еще нет личных карточек,
    копирует библиотеку карточек и структуру графа знаний из default_user / dev_user.
    Это дает пользователю персональный прогресс FSRS без пустого экрана.
    """
    if not user_id or not user_id.isdigit() or user_id in ("default_user", "dev_user"):
        return

    try:
        # Проверяем UserSetting: если пользователь уже проходил онбординг, никогда не клонируем повторно
        setting_stmt = select(UserSetting).filter(UserSetting.user_id == user_id)
        user_setting = (await db.execute(setting_stmt)).scalar_one_or_none()
        if user_setting and user_setting.subject_limits and user_setting.subject_limits.get("_onboarded"):
            return

        user_cards_count = (await db.execute(
            select(func.count(Card.id)).filter(Card.user_id == user_id)
        )).scalar() or 0

        if user_cards_count > 0:
            if user_setting:
                limits = dict(user_setting.subject_limits or {})
                if not limits.get("_onboarded"):
                    limits["_onboarded"] = True
                    user_setting.subject_limits = limits
                    await db.commit()
            return

        # Ищем источник карточек
        src_user = "default_user"
        stmt_src = select(Card).filter(Card.user_id == src_user).order_by(Card.id.asc())
        src_cards = (await db.execute(stmt_src)).scalars().all()
        if not src_cards:
            src_user = "dev_user"
            stmt_src = select(Card).filter(Card.user_id == src_user).order_by(Card.id.asc())
            src_cards = (await db.execute(stmt_src)).scalars().all()

        if not src_cards:
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
    query_tg_id = request.query_params.get("tg_id")

    user_id = None

    # Пробуем разобрать tma <initData>
    init_data_str = None
    if auth_header and auth_header.startswith("tma "):
        init_data_str = auth_header[4:].strip()
    elif init_data_header:
        init_data_str = init_data_header.strip()

    if init_data_str:
        verified_data = parse_and_verify_telegram_init_data(init_data_str, settings.TELEGRAM_BOT_TOKEN)
        if verified_data and "id" in verified_data:
            user_id = str(verified_data["id"])
        elif settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_TOKEN != "placeholder_bot_token":
            # Токен настроен, но подпись не сошлась — отклоняем запрос
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                detail="Недействительная криптографическая подпись Telegram WebApp."
            )

    # Фолбэк для прямого браузерного доступа, разработки и тестов
    if not user_id:
        is_dev_mode = (
            settings.DEBUG
            or getattr(settings, "TESTING", False)
            or not settings.TELEGRAM_BOT_TOKEN
            or settings.TELEGRAM_BOT_TOKEN == "placeholder_bot_token"
        )
        if is_dev_mode:
            if custom_user_header:
                user_id = custom_user_header.strip()
            elif query_tg_id:
                user_id = query_tg_id.strip()
            else:
                user_id = "dev_user"
        else:
            # Для внешнего прямого браузерного доступа без Telegram:
            # Разрешаем безопасный гостевой/демо доступ только для "default_user",
            # предотвращая несанкционированную подмену чужого числового ID
            if custom_user_header == "default_user":
                user_id = "default_user"
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

        # Онбординг стартовой колоды карточек и графа знаний для нового пользователя
        if user_id not in ("default_user", "dev_user"):
            await ensure_user_has_starter_deck(user_id, db)

    except Exception as e:
        print(f"[Auth] Предупреждение при инициализации профиля пользователя {user_id}: {e}")
        await db.rollback()

    return user_id
