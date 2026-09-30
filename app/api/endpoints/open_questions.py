# app/api/endpoints/open_questions.py
"""
Вопросы с открытым ответом («билеты»): пользователь вставляет вопросы и эталонные ответы,
на тренировке пишет ответ своими словами, проверка идёт локально по ключевым тезисам (без ИИ и без затрат).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.core.limiter import limiter
from app.database.models import Card, Phrase, utc_now
from app.database.session import get_db
from app.services.card_db_sync import check_experiment_lock
from app.services.graph_service import resolve_subject_alias
from app.services.open_answer import (
    MAX_ANSWER_CHARS, derive_key_points, grade_answer, parse_questions, points_from_bullets,
)

router = APIRouter()


class CheckIn(BaseModel):
    card_id: int
    answer: str = Field("", max_length=MAX_ANSWER_CHARS)


class TextIn(BaseModel):
    text: str = Field(..., max_length=200_000)


class ImportOpenIn(BaseModel):
    subject: str = Field(..., min_length=1, max_length=128)
    title: str = Field("Вопросы", max_length=120)
    text: str = Field(..., max_length=200_000)


def build_card_fields(item: dict) -> dict:
    """Из пары «вопрос/ответ» получает поля карточки: эталон для показа и ключевые тезисы для проверки."""
    answer = item.get("answer", "")
    bullets = points_from_bullets(answer)
    if bullets:
        reference = "\n".join(f"• {p['text']}" + (f" ({', '.join(p['variants'])})" if p["variants"] else "") for p in bullets)
        key_points = bullets
    else:
        reference = answer
        key_points = derive_key_points(answer) if answer else None
    return {"text": item["question"], "translation": reference, "key_points": key_points or None}


@router.post("/open/preview")
async def preview_questions(payload: TextIn, current_user: str = Depends(get_current_user_id)):
    """Показывает, как вставленный текст разобран на вопросы и сколько тезисов найдено. Ничего не сохраняет."""
    items = parse_questions(payload.text)
    rows = []
    for it in items:
        f = build_card_fields(it)
        rows.append({
            "question": it["question"],
            "has_answer": bool(it["answer"]),
            "points": len(f["key_points"] or []),
        })
    return {"count": len(rows), "without_answer": sum(1 for r in rows if not r["has_answer"]), "items": rows[:200]}


@router.post("/open/import")
async def import_questions(
    payload: ImportOpenIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await check_experiment_lock(current_user, db)
    items = parse_questions(payload.text)
    if not items:
        raise HTTPException(status_code=400, detail="Не нашёл ни одного вопроса. Разделяйте вопросы пустой строкой.")
    subject = resolve_subject_alias(payload.subject.strip().lower())
    if not subject:
        raise HTTPException(status_code=400, detail="Выберите предмет.")

    phrase = Phrase(text=payload.title.strip() or "Вопросы", subject=subject, user_id=current_user)
    db.add(phrase)
    await db.flush()
    now = utc_now()
    existing = {
        t for (t,) in (await db.execute(
            select(Card.text).where(Card.user_id == current_user, Card.subject == subject, Card.content_type == "open")
        )).all()
    }
    created = skipped = without_answer = 0
    for it in items:
        if it["question"] in existing:
            skipped += 1
            continue
        fields = build_card_fields(it)
        if not it["answer"]:
            without_answer += 1
        db.add(Card(
            phrase_id=phrase.id, user_id=current_user, subject=subject, content_type="open",
            state=0, next_review=now, has_seen_intro=True, answer_type="open", **fields,
        ))
        existing.add(it["question"])
        created += 1
    await db.commit()
    return {"status": "success", "subject": subject, "created": created, "skipped_duplicates": skipped,
            "without_answer": without_answer}


@router.post("/open/check")
@limiter.limit("240/minute")
async def check_open_answer(
    request: Request,
    payload: CheckIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Локальная проверка ответа по ключевым тезисам карточки. Только рекомендация: оценку ставит пользователь."""
    card = (await db.execute(
        select(Card).where(Card.id == payload.card_id, Card.user_id == current_user)
    )).scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    result = grade_answer(payload.answer, card.key_points, card.translation)
    result["reference"] = card.translation or ""
    return result
