# app/api/endpoints/practice.py
"""
FastAPI Router for Autonomous Interactive Practice (Requirement R5).
Provides endpoints for generating dynamic practice sessions and verifying answers.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.core.auth import get_current_user_id
from app.services.practice_service import generate_practice_session, verify_practice_answer

router = APIRouter()


class PracticeItemResponse(BaseModel):
    id: str = Field(..., description="Unique practice item identifier")
    type: str = Field(..., description="situational | contrast_pair | slot_filling")
    prompt: str = Field(..., description="Task prompt or case scenario")
    options: List[str] = Field(..., description="List of answer choices")
    subject: str = Field(..., description="Subject domain slug")


class VerifyPracticeIn(BaseModel):
    item_id: str = Field(..., description="Practice item identifier")
    selected_answer: str = Field(..., description="User selected answer text")


class VerifyPracticeResponse(BaseModel):
    correct: bool = Field(..., description="Whether the answer was correct")
    selected: str = Field(..., description="Selected answer")
    correct_answer: str = Field(..., description="Authoritative correct answer")
    explanation: Optional[str] = Field(default=None, description="Detailed statutory or factual explanation")
    gold_standard: Optional[str] = Field(default=None, description="Decisive dividing criterion or formula")


class CompletePracticeIn(BaseModel):
    subject: str = Field(..., min_length=1, max_length=128)
    score: int = Field(..., ge=0)
    total: int = Field(..., ge=1)


class CompletePracticeResponse(BaseModel):
    status: str = "ok"
    success: bool = True
    log_id: int
    score: int
    total: int
    percentage: float
    message: str


class PracticeStatsResponse(BaseModel):
    subject: str
    total_sessions: int
    completed_today: bool
    today_completed: bool = False
    today_count: int = 0
    today_score: Optional[int] = None
    today_total: Optional[int] = None
    today_percentage: Optional[float] = None
    last_score: Optional[int] = None
    last_total: Optional[int] = None
    last_percentage: Optional[float] = None
    best_percentage: Optional[float] = None
    last_practiced_at: Optional[str] = None


@router.get("/practice/session", response_model=List[PracticeItemResponse])
async def get_practice_session(
    subject: str = Query(default="sudoustroystvo", min_length=1, max_length=128),
    count: int = Query(default=10, ge=1, le=50),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Возвращает список интерактивных практических заданий для пользователя по предмету.
    Работает автономно: динамически генерирует кейсы из карточек пользователя или пресетных сценариев.
    """
    items = await generate_practice_session(user_id=current_user, subject=subject, count=count, db=db)
    return items


@router.post("/practice/verify", response_model=VerifyPracticeResponse)
async def verify_answer(
    payload: VerifyPracticeIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Проверяет выбранный ответ на задание и возвращает вердикт с разъяснением и водораздельным критерием.
    """
    result = await verify_practice_answer(
        user_id=current_user,
        item_id=payload.item_id,
        selected_answer=payload.selected_answer,
        db=db
    )
    return result


@router.post("/practice/complete", response_model=CompletePracticeResponse)
async def complete_practice(
    payload: CompletePracticeIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Фиксирует завершение сессии практики, сохраняет результат в журнал и вычисляет процент освоения.
    """
    from app.database.models import PracticeSessionLog
    from datetime import datetime
    
    pct = round((payload.score / payload.total) * 100.0, 1)
    
    if pct >= 80:
        msg = "Превосходно! Вы уверенно различаете правовые режимы, звенья инстанций и водоразделы."
    elif pct >= 50:
        msg = "Хороший результат. Рекомендуем закрепить спорные моменты в Каркасе знаний."
    else:
        msg = "Требуется закрепление. Повторите ключевые институты в графе знаний."

    log = PracticeSessionLog(
        user_id=current_user,
        subject=payload.subject,
        score=payload.score,
        total=payload.total,
        percentage=pct,
        created_at=datetime.utcnow()
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)

    return CompletePracticeResponse(
        status="ok",
        log_id=log.id,
        score=payload.score,
        total=payload.total,
        percentage=pct,
        message=msg
    )


@router.get("/practice/stats", response_model=PracticeStatsResponse)
async def get_practice_stats(
    subject: str = Query(default="sudoustroystvo", min_length=1, max_length=128),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Возвращает статистику практики по предмету: пройдено ли сегодня, балл и история.
    """
    from app.database.models import PracticeSessionLog
    from sqlalchemy import select, func, desc
    from datetime import datetime, date

    stmt = (
        select(PracticeSessionLog)
        .where(PracticeSessionLog.user_id == current_user, PracticeSessionLog.subject == subject)
        .order_by(desc(PracticeSessionLog.created_at))
    )
    res = await db.execute(stmt)
    logs = res.scalars().all()

    total_sessions = len(logs)
    if not logs:
        return PracticeStatsResponse(
            subject=subject,
            total_sessions=0,
            completed_today=False,
            today_completed=False,
            today_count=0
        )

    today = datetime.utcnow().date()
    today_logs = [l for l in logs if l.created_at and l.created_at.date() == today]
    today_log = today_logs[0] if today_logs else None
    latest_log = logs[0] if logs else None
    best_pct = max((l.percentage for l in logs), default=0.0)

    is_today = today_log is not None
    return PracticeStatsResponse(
        subject=subject,
        total_sessions=total_sessions,
        completed_today=is_today,
        today_completed=is_today,
        today_count=len(today_logs),
        today_score=today_log.score if today_log else None,
        today_total=today_log.total if today_log else None,
        today_percentage=today_log.percentage if today_log else None,
        last_score=latest_log.score if latest_log else None,
        last_total=latest_log.total if latest_log else None,
        last_percentage=latest_log.percentage if latest_log else None,
        best_percentage=best_pct,
        last_practiced_at=latest_log.created_at.isoformat() if latest_log and latest_log.created_at else None
    )

