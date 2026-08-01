# app/api/endpoints/train.py
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from collections import defaultdict
from app.database.session import get_db
from app.database.models import Card, ReviewLog, Phrase
from app.services.fsrs_core import calculate_intervals
from datetime import datetime
from app.api.endpoints.management import SUBJECT_LIMITS

router = APIRouter()

class AnswerIn(BaseModel):
    card_id: int
    rating: int  # 1 = Again, 2 = Hard, 3 = Good, 4 = Easy
    response_time: int
    has_association: bool | None = None
    is_cram: bool = False
    is_introduction: bool = False

def apply_interleaving(cards_list: list, max_consecutive: int = 1) -> list:
    """
    Алгоритмический балансировщик (интерливинг).
    Гарантирует, что подряд пойдет не более max_consecutive карт одного предмета.
    """
    if not cards_list:
        return []
        
    by_subject = defaultdict(list)
    for c in cards_list:
        by_subject[c.subject].append(c)
        
    interleaved_result = []
    last_subject = None
    consecutive_count = 0
    
    while by_subject:
        # Сортируем темы по остаточному количеству карт для исключения когнитивного голодания крупных топиков
        available_subjects = sorted(by_subject.keys(), key=lambda s: len(by_subject[s]), reverse=True)
        chosen_subject = None
        
        for sub in available_subjects:
            if sub == last_subject and consecutive_count >= max_consecutive:
                continue
            chosen_subject = sub
            break
            
        # Защитный фолбэк: если правил не осталось, забираем то, что есть в избытке
        if not chosen_subject:
            chosen_subject = available_subjects[0]
            
        card = by_subject[chosen_subject].pop(0)
        if not by_subject[chosen_subject]:
            del by_subject[chosen_subject]
            
        if chosen_subject == last_subject:
            consecutive_count += 1
        else:
            last_subject = chosen_subject
            consecutive_count = 1
            
        interleaved_result.append(card)
        
    return interleaved_result

# --- 1. ВЫДАЧА ОЧЕРЕДИ С ИНТЕРЛИВИНГОМ ТЕМ ---
@router.get("/session")
async def get_session_cards(subject: str = Query("all"), mode: str = Query("mixed"), db: AsyncSession = Depends(get_db)):
    now = datetime.utcnow()
    
    # 1. Сбор просроченных повторений (REV)
    review_stmt = select(Card).filter(Card.state == 2, Card.next_review <= now)
    if subject != 'all':
        review_stmt = review_stmt.filter(Card.subject == subject)
    review_res = await db.execute(review_stmt)
    due_reviews = review_res.scalars().all()

    # 2. Сбор краткосрочной памяти внутри дня (LRN)
    intra_stmt = select(Card).filter(Card.state.in_([1, 3]))
    if subject != 'all':
        intra_stmt = intra_stmt.filter(Card.subject == subject)
    intra_res = await db.execute(intra_stmt)
    intra_day_cards = intra_res.scalars().all()

    # 3. Расчет квот на новые карты по лимитам конфига с учетом уже изученных за день
    limit = SUBJECT_LIMITS.get(subject, 10)
    
    # Получаем начало сегодняшнего дня (UTC) для расчета лимитов
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Считаем количество новых карт (state == 0 на момент ответа), изученных сегодня
    new_today_stmt = select(ReviewLog.id).join(Card, ReviewLog.card_id == Card.id).filter(
        ReviewLog.state == 0,
        ReviewLog.review_time >= today_start
    )
    if subject != 'all':
        new_today_stmt = new_today_stmt.filter(Card.subject == subject)
        
    new_today_res = await db.execute(new_today_stmt)
    already_learned_today = len(new_today_res.scalars().all())
    
    allowed_new_count = max(0, limit - already_learned_today)

    new_cards = []
    if allowed_new_count > 0:
        new_stmt = select(Card).filter(Card.state == 0)
        if subject != 'all':
            new_stmt = new_stmt.filter(Card.subject == subject)
        new_stmt = new_stmt.limit(allowed_new_count)
        new_res = await db.execute(new_stmt)
        new_cards = new_res.scalars().all()

    if mode == "new":
        full_pool = intra_day_cards + new_cards
    elif mode == "review":
        full_pool = due_reviews
    elif mode == "cram":
        cram_stmt = select(Card).order_by(Card.difficulty.desc(), Card.stability.asc()).limit(limit)
        if subject != 'all':
            cram_stmt = cram_stmt.filter(Card.subject == subject)
        cram_res = await db.execute(cram_stmt)
        full_pool = cram_res.scalars().all()
    else: # mixed
        full_pool = due_reviews + intra_day_cards + new_cards
    
    # Запускаем интерливинг в отдельном потоке, только если режим "ALL", чтобы размыть контекст
    if subject == 'all':
        full_pool = await asyncio.to_thread(apply_interleaving, full_pool, 1)
    
    # Оптимизация N+1: собираем все phrase_id для anchored карт и загружаем их за один запрос
    phrase_ids = {c.phrase_id for c in full_pool if c.is_anchored and c.phrase_id}
    phrase_map = {}
    if phrase_ids:
        phrases_stmt = select(Phrase).filter(Phrase.id.in_(list(phrase_ids)))
        phrases_res = await db.execute(phrases_stmt)
        phrase_map = {p.id: p.text for p in phrases_res.scalars().all()}

    result = []
    for c in full_pool:
        phrase_text = phrase_map.get(c.phrase_id, "") if c.is_anchored else ""

        result.append({
            "id": c.id, 
            "text": c.text, 
            "secondary_text": c.secondary_text if c.secondary_text else "",
            "translation": c.translation, 
            "state": c.state, 
            "subject": c.subject,
            "is_anchored": c.is_anchored, 
            "phrase_text": phrase_text, 
            "mnemonic": c.mnemonic,
            "has_seen_intro": c.has_seen_intro,
            "intro_phase": c.intro_phase,
            "content_type": c.content_type,
            "example": c.example if c.example else ""
        })
    return result

# --- 2. СПИСОК ПРЕДМЕТОВ ---
@router.get("/subjects")
async def get_available_subjects(db: AsyncSession = Depends(get_db)):
    stmt = select(Card.subject).distinct()
    res = await db.execute(stmt)
    subjects = res.all()
    return [s[0] for s in subjects if s[0]]

# --- 3. ОБРАБОТКА ОТВЕТОВ И ВАЛИДАЦИЯ FSRS В БД ---
@router.post("/answer")
async def handle_answer(payload: AnswerIn, db: AsyncSession = Depends(get_db)):
    if payload.rating not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="Неверный рейтинг. Допустимо от 1 до 4.")

    stmt = select(Card).filter(Card.id == payload.card_id)
    res = await db.execute(stmt)
    card = res.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")

    now = datetime.utcnow()
    old_state = card.state
    old_next_review = card.next_review
    
    # Check if we are in cram mode. If so, do not update FSRS timers.
    # We can detect cram mode if we pass a special flag, or we can just pass it in AnswerIn
    is_cram = payload.rating == 0 # we will use rating 0 or pass a flag. Let's add is_cram to AnswerIn later. Actually, wait.
    # I should add is_cram to AnswerIn.
    
    scheduled_days = 0
    if card.last_review and old_next_review:
        scheduled_days = (old_next_review - card.last_review).days

    # Расчет интервалов через ядро FSRS
    stability, difficulty, state, next_review, elapsed_days = calculate_intervals(card, payload.rating, now)

    if payload.rating == 1:
        card.lapses += 1

    # Валидация и жесткое обновление весов в data_grinder.db, только если не Штурм
    if not payload.is_cram:
        card.stability = stability
        card.difficulty = difficulty
        card.state = state
        card.next_review = next_review
        card.last_review = now
        if payload.is_introduction:
            card.has_seen_intro = True

    # Определяем наличие ассоциации, если не передано явно
    has_assoc = payload.has_association
    if has_assoc is None:
        has_assoc = card.mnemonic is not None

    log = ReviewLog(
        card_id=card.id,
        rating=payload.rating,
        review_time=now,
        state=old_state,
        elapsed_days=int(elapsed_days),
        scheduled_days=scheduled_days,
        has_association=has_assoc,
        response_time=payload.response_time,
        stability=stability,
        difficulty=difficulty,
        timestamp=now
    )
    db.add(log)
    await db.commit()
    
    return {"status": "success"}