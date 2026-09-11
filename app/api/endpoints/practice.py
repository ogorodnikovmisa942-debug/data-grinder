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
