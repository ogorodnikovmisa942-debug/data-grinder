# app/api/endpoints/exam.py
"""Подготовка к экзамену по билетам: план (билеты + дата), его состояние, эталоны для билетов вне курса."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.core.limiter import limiter
from app.core.timeutil import get_user_timezone, local_now
from app.database.models import KnowledgeNode, utc_now
from app.database.session import get_db
from app.services.card_db_sync import check_experiment_lock
from app.services.exam_prep import (
    MAX_TICKETS, MIN_TICKETS, create_plan, deactivate_plan, exam_overview, get_active_plan,
    parse_ticket_list, set_ticket_answer, skip_ticket, start_matching,
)
from app.services.graph_service import resolve_subject_alias
from app.services.knowledge_path import normalize_subject

router = APIRouter()


class ExamPlanIn(BaseModel):
    title: str = Field("Билеты", max_length=120)
    exam_date: date
    text: str = Field(..., min_length=3, max_length=100_000)


class TicketsTextIn(BaseModel):
    text: str = Field(..., max_length=100_000)


class TicketAnswerIn(BaseModel):
    answer: str = Field(..., min_length=1, max_length=3000)


def _subject(subject: str) -> str:
    return normalize_subject(resolve_subject_alias(subject.strip().lower()) or subject)


@router.get("/exam/{subject}")
async def get_exam(subject: str, current_user: str = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """Активный план подготовки: билеты, дни до экзамена, норма уроков на сегодня."""
    return await exam_overview(db, current_user, _subject(subject))


@router.post("/exam/{subject}/preview")
async def preview_exam(subject: str, payload: TicketsTextIn, current_user: str = Depends(get_current_user_id)):
    """Сколько билетов распознано — до запуска разбора (без ИИ)."""
    items = parse_ticket_list(payload.text)
    return {"count": len(items), "with_answers": sum(1 for it in items if it["answer"]),
            "sample": [it["question"] for it in items[:3]]}


@router.post("/exam/{subject}")
@limiter.limit("10/hour")
async def create_exam(
    request: Request,
    subject: str,
    payload: ExamPlanIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Новый план: билеты сохраняются сразу, сопоставление с графом идёт в фоне (статус matching → ready)."""
    await check_experiment_lock(current_user, db)
    subject = _subject(subject)
    has_path = (await db.execute(
        select(func.count(KnowledgeNode.id)).where(KnowledgeNode.user_id == current_user, KnowledgeNode.subject == subject)
    )).scalar() or 0
    if not has_path:
        raise HTTPException(status_code=400, detail="У предмета нет графа тем: сначала загрузите книгу.")
    today = local_now(await get_user_timezone(db, current_user)).date()
    if payload.exam_date < today:
        raise HTTPException(status_code=400, detail="Дата экзамена уже прошла.")
    if payload.exam_date > today + timedelta(days=365):
        raise HTTPException(status_code=400, detail="Дата экзамена дальше чем через год.")
    items = parse_ticket_list(payload.text)
    if len(items) < MIN_TICKETS:
        raise HTTPException(status_code=400, detail="Не нашёл ни одного билета. Пишите по билету на строку.")
    plan = await create_plan(db, current_user, subject, payload.title, payload.exam_date, items[:MAX_TICKETS])
    start_matching(plan.id)
    return {"id": plan.id, "status": plan.status, "tickets": len(items)}


@router.post("/exam/{subject}/retry")
@limiter.limit("10/hour")
async def retry_exam(request: Request, subject: str, current_user: str = Depends(get_current_user_id),
                     db: AsyncSession = Depends(get_db)):
    plan = await get_active_plan(db, current_user, _subject(subject))
    if not plan or plan.status != "failed":
        raise HTTPException(status_code=409, detail="Повторять нечего.")
    plan.status, plan.error = "matching", None
    plan.created_at = utc_now()
    await db.commit()
    start_matching(plan.id)
    return {"id": plan.id, "status": "matching"}


@router.post("/exam/{subject}/off")
async def turn_off_exam(subject: str, current_user: str = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """Выключить режим экзамена. Уже отвеченные билеты остаются в повторениях."""
    await deactivate_plan(db, current_user, _subject(subject))
    return {"active": False}


@router.post("/exam/ticket/{ticket_id}/answer")
async def answer_ticket(ticket_id: int, payload: TicketAnswerIn, current_user: str = Depends(get_current_user_id),
                        db: AsyncSession = Depends(get_db)):
    """Эталон для билета, которого нет в курсе: вписан пользователем."""
    try:
        ticket = await set_ticket_answer(db, current_user, ticket_id, payload.answer)
    except LookupError:
        raise HTTPException(status_code=404, detail="Билет не найден")
    return {"id": ticket.id, "status": ticket.status}


@router.post("/exam/ticket/{ticket_id}/skip")
async def skip_exam_ticket(ticket_id: int, current_user: str = Depends(get_current_user_id),
                           db: AsyncSession = Depends(get_db)):
    try:
        ticket = await skip_ticket(db, current_user, ticket_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Билет не найден")
    return {"id": ticket.id, "status": ticket.status}
