# app/api/endpoints/train.py
import asyncio
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from collections import defaultdict
from app.database.session import get_db
from app.database.models import Card, ReviewLog, Phrase, UserSetting, UserSession, Category, utc_now
from app.services.fsrs_core import calculate_intervals, calculate_adaptive_retention_factor
from app.core.auth import get_current_user_id, ensure_user_has_starter_deck
from app.core.config import settings
from app.services.graph_service import resolve_subject_alias, get_all_subject_aliases
from datetime import datetime

router = APIRouter()

KNOWN_SUBJECT_NAMES = {
    "sudoustr": "Судоустройство РФ",
    "sudoustroystvo": "Судоустройство РФ",
    "constitutional_law": "Конституционное право",
    "constitution": "Конституционное право",
    "law": "Юриспруденция",
    "law_civil": "Гражданское право",
    "ugolovnoe": "Уголовное право",
    "upk": "Уголовный процесс",
    "gpk": "Гражданский процесс",
    "python": "Python разработка",
    "chinese": "Китайский язык (HSK)",
    "hsk3": "Китайский язык (HSK 3)",
    "generic": "Общий курс"
}

async def get_subject_display_name(slug: str, user_id: str, db: AsyncSession) -> str:
    if not slug:
        return "Курс"
    # Try resolving from Category or Phrase
    stmt = select(Category.name).where(Category.user_id == user_id, Category.name.ilike(f"%{slug}%")).limit(1)
    cat = (await db.execute(stmt)).scalar()
    if cat:
        return cat
    if slug.lower() in KNOWN_SUBJECT_NAMES:
        return KNOWN_SUBJECT_NAMES[slug.lower()]
    stmt_p = select(Phrase.text).where(Phrase.user_id == user_id, Phrase.subject == slug).limit(1)
    p_text = (await db.execute(stmt_p)).scalar()
    if p_text:
        return p_text
    if slug.lower() in ("sudoustr", "sudoustroystvo"):
        return "Судоустройство РФ"
    return slug.replace('_', ' ').replace('-', ' ').title()

class AnswerIn(BaseModel):
    card_id: int
    rating: int  # 1 = Again, 2 = Hard, 3 = Good, 4 = Easy
    response_time: int = 0
    has_association: bool | None = None
    is_cram: bool = False
    is_introduction: bool = False
    is_fast_track: bool = False

def is_admin_or_dev(user_id: str) -> bool:
    user_clean = str(user_id or "").strip()
    if not user_clean:
        return False
    if user_clean in ("default_user", "dev_user"):
        return True
    try:
        from app.core.config import settings
        admin_id_str = str(getattr(settings, "ADMIN_TELEGRAM_ID", "") or "").strip()
        if admin_id_str:
            admins = [x.strip() for x in admin_id_str.split(",") if x.strip()]
            if user_clean in admins:
                return True
    except Exception:
        pass
    return False

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
    subject: Optional[str] = Query("all"), 
    mode: str = Query("mixed"), 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    if not subject:
        stmt = select(Card.subject).where(Card.user_id == current_user).order_by(Card.next_review.desc()).limit(1)
        res = await db.execute(stmt)
        active_sub = res.scalar()
        subject = active_sub or "sudoustroystvo"

    # Страховочный онбординг для числовых пользователей Telegram, если колода пуста
    if current_user and current_user.isdigit():
        total_user_cards = (await db.execute(
            select(func.count(Card.id)).filter(Card.user_id == current_user)
        )).scalar() or 0
        if total_user_cards == 0:
            await ensure_user_has_starter_deck(current_user, db)

    now = utc_now()
    
    # Проверяем участие в научном эксперименте
    session_stmt = select(UserSession).filter(UserSession.user_id == current_user)
    session_res = await db.execute(session_stmt)
    user_sess = session_res.scalars().first()
    is_participant = bool(user_sess and user_sess.is_experiment_participant)
    phase = user_sess.experiment_phase if user_sess else 1

    if is_participant and phase == 1 and not is_admin_or_dev(current_user):
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

    # Разрешаем все алиасы предмета (например, sudoustr <-> sudoustroystvo)
    sub_aliases = get_all_subject_aliases(subject) if subject != 'all' else ['all']

    # 1. Сбор просроченных повторений (REV) текущего пользователя с упорядочиванием по темам и дидактике
    review_stmt = select(Card).filter(
        Card.user_id == current_user,
        Card.state == 2, 
        Card.next_review <= now
    )
    if subject != 'all':
        review_stmt = review_stmt.filter(Card.subject.in_(sub_aliases))
    review_stmt = review_stmt.order_by(Card.subject.asc(), Card.layer.asc(), Card.topological_rank.asc(), Card.next_review.asc())
    review_res = await db.execute(review_stmt)
    due_reviews = review_res.scalars().all()

    # 2. Сбор краткосрочной памяти внутри дня (LRN) текущего пользователя
    intra_stmt = select(Card).filter(
        Card.user_id == current_user,
        Card.state.in_([1, 3])
    )
    if subject != 'all':
        intra_stmt = intra_stmt.filter(Card.subject.in_(sub_aliases))
    intra_stmt = intra_stmt.order_by(Card.subject.asc(), Card.layer.asc(), Card.topological_rank.asc())
    intra_res = await db.execute(intra_stmt)
    intra_day_cards = intra_res.scalars().all()

    # 3. Расчет квот на новые карты с учетом уже изученных именно этим пользователем за день
    today_start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    new_today_stmt = select(ReviewLog.id).join(Card, ReviewLog.card_id == Card.id).filter(
        ReviewLog.user_id == current_user,
        ReviewLog.state == 0,
        ReviewLog.review_time >= today_start
    )
    if subject != 'all':
        new_today_stmt = new_today_stmt.filter(Card.subject.in_(sub_aliases))
        
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
            new_stmt = new_stmt.filter(Card.subject.in_(sub_aliases))
        new_stmt = new_stmt.order_by(Card.layer.asc(), Card.topological_rank.asc(), Card.phrase_id.asc(), Card.id.asc()).limit(allowed_new_count)
        new_res = await db.execute(new_stmt)
        new_cards = new_res.scalars().all()
    elif mode == "new":
        # Если пользователь ЯВНО нажал "Учить новое/еще", но дневная квота исчерпана —
        # не блокируем экран, выдаем дополнительную порцию новых карт (Over-limit study)
        extra_limit = limit or 10
        extra_stmt = select(Card).filter(
            Card.user_id == current_user,
            Card.state == 0
        )
        if subject != 'all':
            extra_stmt = extra_stmt.filter(Card.subject.in_(sub_aliases))
        extra_stmt = extra_stmt.order_by(Card.layer.asc(), Card.topological_rank.asc(), Card.phrase_id.asc(), Card.id.asc()).limit(extra_limit)
        extra_res = await db.execute(extra_stmt)
        new_cards = extra_res.scalars().all()

    if mode == "new":
        # Режим "Учить новое": СТРОГО только новые карточки (state == 0), ни одной старой
        full_pool = new_cards
    elif mode == "review":
        # Режим "Повторение": долгосрочные повторения (state == 2) + краткосрочные внутри дня (state in [1, 3])
        full_pool = due_reviews + intra_day_cards
    elif mode == "cram":
        # Режим "Штурм": строго только уже изученные карточки (state in [1, 2, 3]), исключая новые (state == 0)
        # Сортировка: самые трудные (высокий difficulty) и наименее стабильные (низкая stability)
        cram_stmt = select(Card).filter(
            Card.user_id == current_user,
            Card.state.in_([1, 2, 3])
        ).order_by(Card.difficulty.desc(), Card.stability.asc()).limit(limit)
        if subject != 'all':
            cram_stmt = cram_stmt.filter(Card.subject.in_(sub_aliases))
        cram_res = await db.execute(cram_stmt)
        full_pool = cram_res.scalars().all()
    else: # mixed
        full_pool = due_reviews + intra_day_cards + new_cards

    # 4. Sibling Burying для Cloze (защита от немедленного прайминга)
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
    
    # Интерливинг запускаем ТОЛЬКО при выборе 'all' И если это режим review/mixed,
    # но НИКОГДА не размываем новые карточки (mode == 'new'), чтобы не разрушать связность урока
    if subject == 'all' and mode not in ('new', 'cram'):
        full_pool = await asyncio.to_thread(apply_interleaving, full_pool, 1)
    
    # Оптимизация N+1: собираем все phrase_id для всех карт и загружаем их с фильтром по пользователю
    phrase_ids = {c.phrase_id for c in full_pool if c.phrase_id}
    phrase_map = {}
    if phrase_ids:
        phrases_stmt = select(Phrase).filter(Phrase.id.in_(list(phrase_ids)), Phrase.user_id == current_user)
        phrases_res = await db.execute(phrases_stmt)
        phrase_map = {p.id: p.text for p in phrases_res.scalars().all()}

    distinct_subs = {c.subject for c in full_pool if c.subject}
    display_names_cache = {}
    for s in distinct_subs:
        canon_s = resolve_subject_alias(s)
        name = await get_subject_display_name(canon_s, current_user, db)
        display_names_cache[s] = name
        display_names_cache[canon_s] = name

    result = []
    for c in full_pool:
        phrase_text = phrase_map.get(c.phrase_id, "") or ""
        lapses_count = c.lapses or 0

        # Расчет дидактического контекста и причины появления карточки
        interval_days = 0
        if c.state == 2 and c.next_review and c.last_review:
            try:
                interval_days = max(1, round((c.next_review - c.last_review).total_seconds() / 86400))
            except Exception:
                interval_days = max(1, round(c.stability or 1.0))
        elif c.state == 2 and c.stability:
            interval_days = max(1, round(c.stability))

        if mode == "cram":
            reason_type = "cram"
            reason_icon = "local_fire_department"
            diff_val = round(c.difficulty or 5.5, 1)
            reason_label = f"Штурм (сложность {diff_val})"
        elif c.state == 0:
            reason_type = "new"
            reason_icon = "school"
            reason_label = "Новое понятие"
        elif c.state == 1:
            reason_type = "learning"
            reason_icon = "bolt"
            reason_label = "Закрепление (шаг 2)"
        elif c.state == 3:
            reason_type = "relearning"
            reason_icon = "warning"
            reason_label = "Закрепление ошибки"
        elif c.state == 2:
            reason_type = "review"
            reason_icon = "history"
            reason_label = f"Повторение ({interval_days} дн.)" if interval_days > 0 else "Повторение FSRS"
        else:
            reason_type = "review"
            reason_icon = "history"
            reason_label = "Повторение FSRS"

        canon_sub = resolve_subject_alias(c.subject or "")
        sub_title = display_names_cache.get(c.subject) or display_names_cache.get(canon_sub) or (c.subject.replace("_", " ").title() if c.subject else "Курс")

        result.append({
            "id": c.id, 
            "text": c.text, 
            "secondary_text": c.secondary_text if c.secondary_text else "",
            "translation": c.translation, 
            "state": c.state, 
            "subject": c.subject,
            "subject_title": sub_title,
            "reason_type": reason_type,
            "reason_label": reason_label,
            "reason_icon": reason_icon,
            "interval_days": interval_days,
            "is_anchored": c.is_anchored, 
            "phrase_text": phrase_text, 
            "chapter": phrase_text,
            "mnemonic": c.mnemonic,
            "has_seen_intro": c.has_seen_intro,
            "intro_phase": c.intro_phase,
            "content_type": c.content_type,
            "example": c.example if c.example else "",
            "topological_rank": c.topological_rank or 0,
            "organ_slug": c.organ_slug or "",
            "layer": c.layer if c.layer is not None else 1,
            "lapses": lapses_count,
            "is_leech": lapses_count >= 4
        })
    return result


async def get_next_train_session(
    subject: Optional[str] = None,
    mode: str = "mixed",
    current_user: str = "default_user",
    db: AsyncSession = None
):
    if not subject or subject == "all":
        stmt = select(Card.subject).where(Card.user_id == current_user).order_by(Card.next_review.desc()).limit(1)
        res = await db.execute(stmt)
        active_sub = res.scalar()
        subject = active_sub or "sudoustroystvo"
    return await get_session_cards(subject=subject, mode=mode, current_user=current_user, db=db)

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
    raw_subjects = [s[0] for s in res_cards.all() if s[0]] + [s[0] for s in res_phrases.all() if s[0]]
    if not raw_subjects and current_user not in ("default_user", "dev_user"):
        stmt_def_cards = select(Card.subject).filter(Card.user_id.in_(["default_user", "dev_user"])).distinct()
        stmt_def_phrases = select(Phrase.subject).filter(Phrase.user_id.in_(["default_user", "dev_user"])).distinct()
        res_def_cards = await db.execute(stmt_def_cards)
        res_def_phrases = await db.execute(stmt_def_phrases)
        raw_subjects = [s[0] for s in res_def_cards.all() if s[0]] + [s[0] for s in res_def_phrases.all() if s[0]]
    unique_subjects = sorted(list(dict.fromkeys(raw_subjects)))
    return unique_subjects

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

    if is_participant and phase == 1 and not is_admin_or_dev(current_user):
        target_retention = 0.9
    else:
        setting_res = await db.execute(select(UserSetting).filter(UserSetting.user_id == current_user))
        user_setting = setting_res.scalar_one_or_none()
        target_retention = user_setting.target_retention if user_setting else 0.9

    now = utc_now()
    old_state = card.state
    old_next_review = card.next_review
    
    scheduled_days = 0
    if card.last_review and old_next_review:
        scheduled_days = (old_next_review - card.last_review).days

    # Адаптивная калибровка retention на основе последних 30 логов пользователя
    recent_logs_stmt = select(ReviewLog.rating).where(ReviewLog.user_id == current_user).order_by(ReviewLog.review_time.desc()).limit(30)
    recent_logs_res = await db.execute(recent_logs_stmt)
    ratings = [{"rating": r} for r in recent_logs_res.scalars().all()]
    factor = calculate_adaptive_retention_factor(ratings)

    # Расчет интервалов через ядро FSRS с учетом санированного response_time, target_retention и адаптивного retention_factor
    stability, difficulty, state, next_review, elapsed_days = calculate_intervals(
        card=card, 
        rating=effective_rating, 
        now=now,
        response_time=effective_response_time,
        target_retention=target_retention,
        retention_factor=factor
    )

    # Валидация и обновление весов в БД, если не Штурм (cram)
    if not payload.is_cram:
        if effective_rating == 1:
            card.lapses += 1
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

submit_answer = handle_answer