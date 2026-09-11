# app/api/endpoints/train.py
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from collections import defaultdict
from app.database.session import get_db
from app.database.models import Card, ReviewLog, Phrase, UserSetting, UserSession
from app.services.fsrs_core import calculate_intervals
from app.core.auth import get_current_user_id
from app.core.config import settings
from datetime import datetime

router = APIRouter()

class AnswerIn(BaseModel):
    card_id: int
    rating: int  # 1 = Again, 2 = Hard, 3 = Good, 4 = Easy
    response_time: int = 0
    has_association: bool | None = None
    is_cram: bool = False
    is_introduction: bool = False
    is_fast_track: bool = False

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

# --- 1. ВЫДАЧА ОЧЕРЕДИ С ИНТЕРЛИВИНГОМ ТЕМ И ИЗОЛЯЦИЕЙ ПО ПОЛЬЗОВАТЕЛЮ ---
@router.get("/session")
async def get_session_cards(
    subject: str = Query("all"), 
    mode: str = Query("mixed"), 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    now = datetime.utcnow()
    
    # Проверяем участие в научном эксперименте
    session_stmt = select(UserSession).filter(UserSession.user_id == current_user)
    session_res = await db.execute(session_stmt)
    user_sess = session_res.scalars().first()
    is_participant = bool(user_sess and user_sess.is_experiment_participant)
    phase = user_sess.experiment_phase if user_sess else 1

    if is_participant and phase == 1:
        # Фаза 1: жесткий лок — только судоустройство, лимит 20 карт, target_retention = 0.9
        subject = "sudoustroystvo"
        limit = settings.EXPERIMENT_DAILY_LIMIT
        target_retention = 0.9
    else:
        # Получаем персональные настройки пользователя из БД
        setting_res = await db.execute(select(UserSetting).filter(UserSetting.user_id == current_user))
        user_setting = setting_res.scalar_one_or_none()
        user_daily_limit = user_setting.daily_limit if user_setting else 10
        
        # Лимит для конкретного предмета
        subject_limits = user_setting.subject_limits if (user_setting and user_setting.subject_limits) else {}
        limit = subject_limits.get(subject, user_daily_limit)

    # 1. Сбор просроченных повторений (REV) текущего пользователя
    review_stmt = select(Card).filter(
        Card.user_id == current_user,
        Card.state == 2, 
        Card.next_review <= now
    )
    if subject != 'all':
        review_stmt = review_stmt.filter(Card.subject == subject)
    review_res = await db.execute(review_stmt)
    due_reviews = review_res.scalars().all()

    # 2. Сбор краткосрочной памяти внутри дня (LRN) текущего пользователя
    intra_stmt = select(Card).filter(
        Card.user_id == current_user,
        Card.state.in_([1, 3])
    )
    if subject != 'all':
        intra_stmt = intra_stmt.filter(Card.subject == subject)
    intra_res = await db.execute(intra_stmt)
    intra_day_cards = intra_res.scalars().all()

    # 3. Расчет квот на новые карты с учетом уже изученных именно этим пользователем за день
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    new_today_stmt = select(ReviewLog.id).join(Card, ReviewLog.card_id == Card.id).filter(
        ReviewLog.user_id == current_user,
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
        new_stmt = select(Card).filter(
            Card.user_id == current_user,
            Card.state == 0
        )
        if subject != 'all':
            new_stmt = new_stmt.filter(Card.subject == subject)
        new_stmt = new_stmt.limit(allowed_new_count)
        new_res = await db.execute(new_stmt)
        new_cards = new_res.scalars().all()

    if mode == "new":
        # Режим "Учить новое": СТРОГО только новые карточки (state == 0), ни одной старой
        full_pool = new_cards
    elif mode == "review":
        # Режим "Повторение": долгосрочные повторения (state == 2) + краткосрочные внутри дня (state in [1, 3])
        full_pool = due_reviews + intra_day_cards
    elif mode == "cram":
        cram_stmt = select(Card).filter(Card.user_id == current_user).order_by(Card.difficulty.desc(), Card.stability.asc()).limit(limit)
        if subject != 'all':
            cram_stmt = cram_stmt.filter(Card.subject == subject)
        cram_res = await db.execute(cram_stmt)
        full_pool = cram_res.scalars().all()
    else: # mixed
        full_pool = due_reviews + intra_day_cards + new_cards

    # 4. Sibling Burying для Cloze (защита от немедленного прайминга)
    # Не показываем сиблингов (пропуски из одной фразы) в одну сессию и откладываем, если один уже изучен сегодня
    reviewed_today_phrase_stmt = select(Card.phrase_id).join(ReviewLog, ReviewLog.card_id == Card.id).filter(
        ReviewLog.user_id == current_user,
        ReviewLog.review_time >= today_start,
        Card.content_type == "cloze"
    ).distinct()
    reviewed_today_phrases = set((await db.execute(reviewed_today_phrase_stmt)).scalars().all())

    filtered_pool = []
    seen_cloze_phrase_ids = set(reviewed_today_phrases)

    for c in full_pool:
        if getattr(c, "content_type", "text") == "cloze" and c.phrase_id:
            if c.phrase_id in seen_cloze_phrase_ids:
                continue  # Откладываем сиблинга до следующего дня
            seen_cloze_phrase_ids.add(c.phrase_id)
        filtered_pool.append(c)

    full_pool = filtered_pool
    
    # Запускаем интерливинг в отдельном потоке, только если режим "ALL", чтобы размыть контекст
    if subject == 'all':
        full_pool = await asyncio.to_thread(apply_interleaving, full_pool, 1)
    
    # Оптимизация N+1: собираем все phrase_id для anchored карт и загружаем их с фильтром по пользователю
    phrase_ids = {c.phrase_id for c in full_pool if c.is_anchored and c.phrase_id}
    phrase_map = {}
    if phrase_ids:
        phrases_stmt = select(Phrase).filter(Phrase.id.in_(list(phrase_ids)), Phrase.user_id == current_user)
        phrases_res = await db.execute(phrases_stmt)
        phrase_map = {p.id: p.text for p in phrases_res.scalars().all()}

    result = []
    for c in full_pool:
        phrase_text = phrase_map.get(c.phrase_id, "") if c.is_anchored else ""

        lapses_count = c.lapses or 0
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
            "example": c.example if c.example else "",
            "lapses": lapses_count,
            "is_leech": lapses_count >= 4
        })
    return result

# --- 2. СПИСОК ПРЕДМЕТОВ ТЕКУЩЕГО ПОЛЬЗОВАТЕЛЯ ---
@router.get("/subjects")
async def get_available_subjects(
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    stmt_cards = select(Card.subject).filter(Card.user_id == current_user).distinct()
    stmt_phrases = select(Phrase.subject).filter(Phrase.user_id == current_user).distinct()
    res_cards = await db.execute(stmt_cards)
    res_phrases = await db.execute(stmt_phrases)
    subjects = list(dict.fromkeys([s[0] for s in res_cards.all() if s[0]] + [s[0] for s in res_phrases.all() if s[0]]))
    return sorted(subjects)

# --- 3. ОБРАБОТКА ОТВЕТОВ И ВАЛИДАЦИЯ FSRS В БД ---
@router.post("/answer")
async def handle_answer(
    payload: AnswerIn, 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    if payload.rating not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="Неверный рейтинг. Допустимо от 1 до 4.")

    stmt = select(Card).filter(Card.id == payload.card_id, Card.user_id == current_user)
    res = await db.execute(stmt)
    card = res.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена или нет прав доступа")

    # Санитария таймингов (защита от мисскликов и когнитивных выбросов)
    is_outlier = False
    effective_rating = payload.rating
    effective_response_time = payload.response_time

    if payload.response_time < 600:
        is_outlier = True
        if effective_rating == 4:
            effective_rating = 3  # запретить начисление Easy (<600 мс трактуется как миссклик)
    elif payload.response_time > 30000:
        is_outlier = True
        effective_response_time = 15000  # безопасное значение для исключения искусственных штрафов FSRS

    # Получаем target_retention с учетом научного эксперимента
    session_stmt = select(UserSession).filter(UserSession.user_id == current_user)
    session_res = await db.execute(session_stmt)
    user_sess = session_res.scalars().first()
    is_participant = bool(user_sess and user_sess.is_experiment_participant)
    phase = user_sess.experiment_phase if user_sess else 1

    if is_participant and phase == 1:
        target_retention = 0.9
    else:
        setting_res = await db.execute(select(UserSetting).filter(UserSetting.user_id == current_user))
        user_setting = setting_res.scalar_one_or_none()
        target_retention = user_setting.target_retention if user_setting else 0.9

    now = datetime.utcnow()
    old_state = card.state
    old_next_review = card.next_review
    
    scheduled_days = 0
    if card.last_review and old_next_review:
        scheduled_days = (old_next_review - card.last_review).days

    # Расчет интервалов через ядро FSRS с учетом санированного response_time и target_retention
    stability, difficulty, state, next_review, elapsed_days = calculate_intervals(
        card=card, 
        rating=effective_rating, 
        now=now,
        response_time=effective_response_time,
        target_retention=target_retention
    )

    if effective_rating == 1:
        card.lapses += 1

    # Валидация и обновление весов в БД, если не Штурм (cram)
    if not payload.is_cram:
        card.stability = stability
        card.difficulty = difficulty
        card.state = state
        card.next_review = next_review
        card.last_review = now
        if payload.is_introduction or payload.is_fast_track:
            card.has_seen_intro = True

    # Определяем наличие ассоциации, если не передано явно
    has_assoc = payload.has_association
    if has_assoc is None:
        has_assoc = card.mnemonic is not None

    # Гарантированное сохранение ReviewLog (включая режим is_cram = True)
    log = ReviewLog(
        card_id=card.id,
        user_id=current_user,
        rating=effective_rating,
        review_time=now,
        state=old_state,
        elapsed_days=int(elapsed_days),
        scheduled_days=scheduled_days,
        has_association=has_assoc,
        response_time=payload.response_time,  # фиксируем реальное время задержки
        stability=stability,
        difficulty=difficulty,
        timestamp=now,
        is_outlier=is_outlier,
        is_cram=payload.is_cram
    )
    db.add(log)
    await db.commit()
    
    return {"status": "success"}