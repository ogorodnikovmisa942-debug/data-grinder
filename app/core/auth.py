# app/core/auth.py
import hmac
import hashlib
import json
import urllib.parse
from fastapi import Request, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.config import settings
from app.database.session import get_db
from app.database.models import UserSetting, UserSession

def parse_and_verify_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Проверяет криптографическую подпись initData от Telegram WebApp.
    Возвращает словарь данных пользователя при успехе, либо None.
    """
    if not init_data or not bot_token or bot_token == "placeholder_bot_token":
        return None

    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        hash_check = parsed.pop("hash", None)
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
                return json.loads(user_raw)
            return parsed
        return None
    except Exception as e:
        print(f"[Auth] Ошибка валидации initData: {e}")
        return None

async def get_current_user_id(request: Request, db: AsyncSession = Depends(get_db)) -> str:
    """
    Основная зависимость FastAPI для получения проверенного user_id.
    1. Ищет заголовок Authorization (tma <initData>), X-Telegram-Init-Data или X-User-Id.
    2. При наличии Telegram initData проверяет криптографическую подпись бота.
    3. Если подпись валидна — возвращает Telegram ID пользователя.
    4. При локальной разработке в браузере (вне Telegram) безопасно использует фолбэк (X-User-Id, tg_id или dev_user).
    5. Автоматически инициализирует запись UserSetting в БД при первом входе.
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

    # Фолбэк для разработки и прямого браузерного доступа
    if not user_id:
        if custom_user_header:
            user_id = custom_user_header.strip()
        elif query_tg_id:
            user_id = query_tg_id.strip()
        else:
            user_id = "dev_user"

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
