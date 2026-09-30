import hmac
import time
import hashlib
import json
import urllib.parse
from datetime import datetime
from fastapi import Request, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.config import settings
from app.database.session import get_db
from app.database.models import UserSetting, UserSession, Card, Phrase, utc_now

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

        # Свежесть: подпись валидна вечно, поэтому украденный initData нельзя принимать без срока годности
        max_age = settings.INIT_DATA_MAX_AGE_SECONDS
        auth_date = parsed.get("auth_date", "")
        if max_age and auth_date.isdigit() and time.time() - int(auth_date) > max_age:
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


def _extract_user_id(verified: dict) -> str | None:
    """Достаёт числовой Telegram id из проверенных данных initData."""
    raw = verified.get("id")
    if raw is None and isinstance(verified.get("user"), dict):
        raw = verified["user"].get("id")
    raw = str(raw).strip() if raw is not None else ""
    return raw if raw.isdigit() else None


async def ensure_user_has_starter_deck(user_id: str, db: AsyncSession):
    """
    Онбординг-заглушка. Карточки раздаются только через админку (/admin/experiment/distribute-deck)
    или создаются/импортируются пользователем самостоятельно.
    """
    return


async def get_current_user_id(request: Request, db: AsyncSession = Depends(get_db)) -> str:
    """
    Основная зависимость FastAPI для получения проверенного user_id.
    1. Ищет заголовок Authorization (tma <initData>) или X-Telegram-Init-Data.
    2. Проверяет криптографическую подпись бота и свежесть auth_date. Только подписанный id считается личностью.
    3. Идентификаторы без подписи (X-User-Id, tg_id, неверифицированный payload) принимаются
       ТОЛЬКО в режиме разработки/тестов (settings.is_dev_mode()); в боевом режиме — 401.
    4. Автоматически инициализирует UserSetting/UserSession при первом входе.
    """
    auth_header = request.headers.get("authorization")
    init_data_header = request.headers.get("x-telegram-init-data")
    custom_user_header = request.headers.get("x-user-id") or request.headers.get("x-telegram-user-id")
    query_tg_id = request.query_params.get("tg_id") or request.query_params.get("user_id")

    init_data_str = None
    if auth_header and auth_header.lower().startswith("tma "):
        init_data_str = auth_header[4:].strip()
    elif init_data_header:
        init_data_str = init_data_header.strip()

    user_id = None
    if init_data_str:
        verified = parse_and_verify_telegram_init_data(init_data_str, settings.TELEGRAM_BOT_TOKEN)
        if verified:
            user_id = _extract_user_id(verified)

    if not user_id:
        if settings.is_dev_mode():
            candidate = (custom_user_header or "").strip() or (query_tg_id or "").strip()
            user_id = candidate or "dev_user"
        else:
            detail = (
                "Недействительная или устаревшая подпись Telegram WebApp."
                if init_data_str else "Требуется авторизация через Telegram WebApp."
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

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


    except Exception as e:
        print(f"[Auth] Предупреждение при инициализации профиля пользователя {user_id}: {e}")
        await db.rollback()

    return user_id
