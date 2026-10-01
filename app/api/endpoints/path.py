# app/api/endpoints/path.py
"""API «Пути знаний»: состояние графа предмета, урок узла и отметка о его прохождении."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.database.models import KnowledgeNode
from app.database.session import get_db
from app.services.knowledge_path import (
    get_path_state, is_node_open, complete_lesson, next_path_step, get_day_plan, get_today_summary,
    normalize_subject, RUN_STEP_LIMITS,
)
from app.services.exam_prep import EXAM_STEP_LIMITS

router = APIRouter()


class CompleteLessonIn(BaseModel):
    checkpoint_score: int = Field(0, ge=0, le=10)


async def _get_own_node(db: AsyncSession, user_id: str, node_id: int) -> KnowledgeNode:
    node = (await db.execute(
        select(KnowledgeNode).where(KnowledgeNode.id == node_id, KnowledgeNode.user_id == user_id)
    )).scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Узел не найден")
    return node


@router.get("/path/{subject}")
async def get_path(subject: str, current_user: str = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """Все узлы предмета со статусами locked | open | lesson_done | mastered и связи для графа."""
    return await get_path_state(db, current_user, subject)


@router.get("/path/{subject}/next")
async def get_next_step(
    subject: str,
    done: str = Query("", max_length=200, description="Типы шагов, уже выданных в этом запуске, через запятую"),
    scope: str = Query("day", pattern="^(day|topic|exam)$", description="day — «Продолжить путь», topic — одна новая тема"),
    extra: bool = Query(False, description="Тема сверх дневной нормы по явному выбору"),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Следующий шаг занятия: review | cards | lesson | practice | done."""
    done_steps = [t for t in done.split(",") if t in RUN_STEP_LIMITS or t in EXAM_STEP_LIMITS]
    return await next_path_step(db, current_user, subject, done_steps, scope=scope, extra=extra)


@router.get("/path/{subject}/day")
async def get_day(subject: str, current_user: str = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """План дня: место под новые карточки, недоученная тема, следующий урок, цель дня и итоги для награды."""
    plan = await get_day_plan(db, current_user, subject)
    return {**plan, "today": await get_today_summary(db, current_user, normalize_subject(subject))}


@router.get("/path/node/{node_id}/lesson")
async def get_lesson(node_id: int, current_user: str = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    node = await _get_own_node(db, current_user, node_id)
    if not await is_node_open(db, current_user, node):
        raise HTTPException(status_code=403, detail="Узел ещё закрыт: сначала освой предыдущие темы")
    return {"node": node.to_dict(), "lesson": node.lesson}


@router.post("/path/node/{node_id}/complete")
async def complete_node_lesson(
    node_id: int,
    payload: CompleteLessonIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Урок пройден: карточки узла становятся доступны в тренировке."""
    node = await _get_own_node(db, current_user, node_id)
    if not await is_node_open(db, current_user, node):
        raise HTTPException(status_code=403, detail="Узел ещё закрыт")
    await complete_lesson(db, current_user, node.id, payload.checkpoint_score)
    await db.commit()
    return {"status": "success", "node_id": node.id}
