# app/api/endpoints/path.py
"""API «Пути знаний»: состояние графа предмета, урок узла и отметка о его прохождении."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.database.models import KnowledgeNode
from app.database.session import get_db
from app.services.knowledge_path import get_path_state, is_node_open, complete_lesson

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
