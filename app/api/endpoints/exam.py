# app/api/endpoints/exam.py
"""Подготовка к экзамену по билетам: план (билеты + дата), его состояние, эталоны для билетов вне курса."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
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
    MAX_TICKETS, MIN_TICKETS, apply_emergency, create_plan, deactivate_plan, emergency_overview, exam_overview, exam_priorities,
    get_active_plan, parse_ticket_list, set_ticket_answer, simulator_check, simulator_draw, skip_ticket, start_matching, undo_emergency,
)
from app.services.text_extract import ExtractError, MAX_EXTRACT_BYTES, extract_text
from app.services.graph_service import resolve_subject_alias
from app.services.knowledge_path import normalize_subject
from app.services.quota import allows_rematch, enforce_exam_budget, enforce_exam_plan

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


@router.get("/exams/priorities")
async def get_exam_priorities(current_user: str = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """Планы по всем предметам, от самого срочного: ближе экзамен и ниже готовность — выше в списке."""
    return {"items": await exam_priorities(db, current_user)}


@router.post("/exam/extract")
@limiter.limit("30/hour")
async def extract_tickets_file(request: Request, file: UploadFile = File(...), current_user: str = Depends(get_current_user_id)):
    """Список билетов из файла (PDF, Word, PowerPoint, текст): возвращает текст, пользователь проверяет его и отправляет как обычно."""
    contents = await file.read(MAX_EXTRACT_BYTES + 1)
    try:
        text = extract_text(file.filename or "", contents)
    except ExtractError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:  # noqa: BLE001 — битый файл
        raise HTTPException(status_code=400, detail="Не получилось прочитать файл.")
    items = parse_ticket_list(text)
    return {"text": text[:100_000], "count": len(items), "sample": [it["question"] for it in items[:3]]}


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
    await enforce_exam_plan(db, current_user, subject)
    await enforce_exam_budget(db, current_user)
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
    await enforce_exam_budget(db, current_user)
    plan.status, plan.error = "matching", None
    plan.created_at = utc_now()
    await db.commit()
    start_matching(plan.id)
    return {"id": plan.id, "status": "matching"}


@router.post("/exam/{subject}/rematch")
@limiter.limit("10/hour")
async def rematch_exam(request: Request, subject: str, current_user: str = Depends(get_current_user_id),
                       db: AsyncSession = Depends(get_db)):
    """Найти заново только билеты «нет в книге» (после добавления нового материала в предмет). Остальные билеты не трогаются."""
    from app.database.models import ExamTicket
    plan = await get_active_plan(db, current_user, _subject(subject))
    if not plan or plan.status != "ready":
        raise HTTPException(status_code=409, detail="План не готов.")
    if not await allows_rematch(db, current_user):
        raise HTTPException(status_code=402, detail={"code": "quota", "message": "Повторный поиск билетов после нового материала доступен на платном тарифе."})
    missing = (await db.execute(
        select(func.count(ExamTicket.id)).where(ExamTicket.plan_id == plan.id, ExamTicket.status == "missing")
    )).scalar() or 0
    if not missing:
        raise HTTPException(status_code=409, detail="Все билеты уже найдены или пропущены.")
    await enforce_exam_budget(db, current_user)
    start_matching(plan.id, only_missing=True)
    return {"id": plan.id, "searching": missing}


@router.get("/exam/{subject}/emergency")
async def get_emergency(subject: str, current_user: str = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """Аварийный режим: если всё не успевается, какие билеты закрываются минимумом тем, а какие разумно отложить."""
    return await emergency_overview(db, current_user, _subject(subject))


@router.post("/exam/{subject}/emergency/apply")
async def apply_exam_emergency(subject: str, current_user: str = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    try:
        return await apply_emergency(db, current_user, _subject(subject))
    except LookupError:
        raise HTTPException(status_code=409, detail="План не готов.")


@router.post("/exam/{subject}/emergency/undo")
async def undo_exam_emergency(subject: str, current_user: str = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    try:
        return await undo_emergency(db, current_user, _subject(subject))
    except LookupError:
        raise HTTPException(status_code=404, detail="План не найден.")


@router.get("/exam/{subject}/simulator/draw")
async def draw_simulator_ticket(subject: str, exclude: str = "", current_user: str = Depends(get_current_user_id),
                                db: AsyncSession = Depends(get_db)):
    """Симулятор: случайный билет из тех, чьи темы уже пройдены, и время на ответ."""
    ids = [int(x) for x in exclude.split(",") if x.strip().isdigit()][:200]
    try:
        return await simulator_draw(db, current_user, _subject(subject), ids)
    except LookupError as e:
        raise HTTPException(status_code=409, detail=str(e))


class SimulatorAnswerIn(BaseModel):
    answer: str = Field("", max_length=6000)


@router.post("/exam/ticket/{ticket_id}/simulate")
async def check_simulator_answer(ticket_id: int, payload: SimulatorAnswerIn, current_user: str = Depends(get_current_user_id),
                                 db: AsyncSession = Depends(get_db)):
    """Проверка ответа в симуляторе: что названо, что упущено. В повторения ничего не записывается."""
    try:
        return await simulator_check(db, current_user, ticket_id, payload.answer)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/exam/ticket/{ticket_id}/ai-check")
@limiter.limit("30/hour")
async def ai_check_answer(request: Request, ticket_id: int, payload: SimulatorAnswerIn, current_user: str = Depends(get_current_user_id),
                          db: AsyncSession = Depends(get_db)):
    """ИИ-оценка смысла ответа (платная, месячная квота): по смыслу, а не по словам. В повторения ничего не записывается."""
    from app.services.exam_prep import ai_check_ticket
    try:
        return await ai_check_ticket(db, current_user, ticket_id, payload.answer)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="ИИ сейчас не ответил. Попробуйте ещё раз через минуту: проверка не засчитана.")


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
