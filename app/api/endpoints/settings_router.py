# app/api/endpoints/settings_router.py
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import get_db
from app.database.models import UserSetting
from app.services.generation_worker import is_deepseek_offpeak
from app.services.card_db_sync import check_experiment_lock, get_user_experiment_status
from app.core.auth import get_current_user_id
from app.core.config import settings

router = APIRouter()


class ConfigUpdate(BaseModel):
    daily_limit: int
    focus_mode_default: bool = False
    target_retention: float | None = 0.9
    assoc_preference: str | None = "acoustic"


# --- 3. НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ ИЗ ТАБЛИЦЫ БД ---
@router.get("/config")
async def get_config(
    subject: str = Query("all"),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    setting_res = await db.execute(select(UserSetting).filter(UserSetting.user_id == current_user))
    setting = setting_res.scalar_one_or_none()
    
    daily_limit = 10
    subject_limits = {}
    assoc_pref = "acoustic"
    target_retention = 0.9
    
    if setting:
        daily_limit = setting.daily_limit
        subject_limits = setting.subject_limits or {}
        assoc_pref = setting.assoc_preference or "acoustic"
        target_retention = setting.target_retention or 0.9
        
    current_subject_limit = subject_limits.get(subject, daily_limit)

    is_part, phase = await get_user_experiment_status(current_user, db)
    if is_part and phase == 1:
        current_subject_limit = settings.EXPERIMENT_DAILY_LIMIT
        daily_limit = settings.EXPERIMENT_DAILY_LIMIT

    return {
        "daily_limit": current_subject_limit, 
        "focus_mode_default": False,
        "assoc_preference": assoc_pref,
        "target_retention": target_retention,
        "is_experiment_participant": is_part,
        "experiment_phase": phase,
        "is_experiment_locked": bool(is_part and phase == 1)
    }


@router.post("/config")
async def update_config(
    payload: ConfigUpdate, 
    subject: str = Query("all"),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    await check_experiment_lock(current_user, db)
    setting_res = await db.execute(select(UserSetting).filter(UserSetting.user_id == current_user))
    setting = setting_res.scalar_one_or_none()
    
    if not setting:
        setting = UserSetting(
            user_id=current_user,
            daily_limit=payload.daily_limit,
            subject_limits={subject: payload.daily_limit}
        )
        db.add(setting)
    else:
        subject_limits = dict(setting.subject_limits or {})
        subject_limits[subject] = payload.daily_limit
        setting.subject_limits = subject_limits
        if subject == "all":
            setting.daily_limit = payload.daily_limit
        if payload.target_retention is not None:
            setting.target_retention = payload.target_retention
        if payload.assoc_preference is not None:
            setting.assoc_preference = payload.assoc_preference

    await db.commit()
    return {"status": "updated", "config": {"daily_limit": payload.daily_limit}}


@router.get("/config/ai-provider")
async def get_public_ai_provider():
    """Публичный статус активного ИИ-провайдера и модели для отображения в MiniApp."""
    return {
        "status": "success",
        "provider": "deepseek",
        "model": settings.DEEPSEEK_MODEL,
        "is_offpeak": is_deepseek_offpeak()
    }
