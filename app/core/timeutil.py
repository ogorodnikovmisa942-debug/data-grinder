"""Время пользователя: границы суток и «сегодня» считаются по его часовому поясу, а не по UTC.

В БД время хранится как наивный UTC. Пояс пользователя берётся из UserSetting.timezone
(клиент присылает IANA-имя из Intl), по умолчанию — settings.DEFAULT_TIMEZONE.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.core.config import settings


def resolve_tz(name: str | None) -> ZoneInfo:
    for candidate in (name, settings.DEFAULT_TIMEZONE, "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            continue
    return ZoneInfo("UTC")


def is_valid_timezone(name: str | None) -> bool:
    if not name or len(name) > 64:
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def _utc_now_aware() -> datetime:
    return datetime.now(timezone.utc)


def local_now(tz_name: str | None, now_utc: datetime | None = None) -> datetime:
    """Текущее локальное время пользователя (aware)."""
    base = now_utc.replace(tzinfo=timezone.utc) if now_utc is not None else _utc_now_aware()
    return base.astimezone(resolve_tz(tz_name))


def local_midnight_utc(tz_name: str | None, now_utc: datetime | None = None, days_back: int = 0) -> datetime:
    """Начало локальных суток пользователя, как наивный UTC (для сравнения с колонками БД)."""
    local = local_now(tz_name, now_utc)
    midnight = (local - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).replace(tzinfo=None)


def to_local_date(dt_utc_naive: datetime, tz_name: str | None):
    return dt_utc_naive.replace(tzinfo=timezone.utc).astimezone(resolve_tz(tz_name)).date()


async def get_user_timezone(db, user_id: str) -> str:
    from app.database.models import UserSetting
    tz = (await db.execute(select(UserSetting.timezone).where(UserSetting.user_id == user_id))).scalar_one_or_none()
    return tz or settings.DEFAULT_TIMEZONE


async def user_day_start(db, user_id: str) -> datetime:
    """Начало сегодняшних суток пользователя (наивный UTC)."""
    return local_midnight_utc(await get_user_timezone(db, user_id))
