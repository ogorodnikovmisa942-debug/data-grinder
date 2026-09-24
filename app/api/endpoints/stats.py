# app/api/endpoints/stats.py
from datetime import datetime, timedelta
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.session import get_db
from app.database.models import (
    Card, ReviewLog, Phrase, UserSession, DailySession, UserSetting, utc_now
)
from app.services.graph_service import resolve_subject_alias, get_all_subject_aliases
from app.services.card_db_sync import is_admin_or_dev
from app.core.auth import get_current_user_id
from app.core.config import settings

router = APIRouter()


class DailySessionIn(BaseModel):
    mental_effort: int
    association_utility: int
    perceived_retention: int
    session_duration: int


# --- 2. АНАЛИТИКА И ДАШБОРД ТЕКУЩЕГО ПОЛЬЗОВАТЕЛЯ ---
@router.get("/stats/dashboard")
async def get_analytics(
    subject: str = Query("all"), 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    sub_aliases = get_all_subject_aliases(subject) if subject != 'all' else ['all']
    card_stmt = select(Card).filter(Card.user_id == current_user)
    if subject != 'all': 
        card_stmt = card_stmt.filter(Card.subject.in_(sub_aliases))
    card_res = await db.execute(card_stmt)
    cards = card_res.scalars().all()

    states_dict = {0: 0, 1: 0, 2: 0, 3: 0}
    for c in cards: 
        states_dict[c.state if c.state in states_dict else 0] += 1
        
    total_cards = len(cards)
    progress_percent = round((states_dict[2] / total_cards) * 100) if total_cards > 0 else 0

    # Расчет Retention Rate за 30 дней для конкретного пользователя
    one_month_ago = utc_now() - timedelta(days=30)
    
    if subject != 'all':
        log_stmt = select(ReviewLog).join(Card, ReviewLog.card_id == Card.id).filter(
            ReviewLog.user_id == current_user,
            ReviewLog.review_time >= one_month_ago, 
            Card.subject.in_(sub_aliases)
        )
    else:
        log_stmt = select(ReviewLog).filter(
            ReviewLog.user_id == current_user,
            ReviewLog.review_time >= one_month_ago
        )
        
    log_res = await db.execute(log_stmt)
    logs = log_res.scalars().all()
    total_reviews = len(logs)
    successful_reviews = sum(1 for r in logs if r.rating > 1)
    retention_rate = round((successful_reviews / total_reviews) * 100, 1) if total_reviews > 0 else 0.0

    # Расчет ударного режима (Streak) для конкретного пользователя
    streak_stmt = select(func.date(ReviewLog.review_time)).filter(
        ReviewLog.user_id == current_user
    ).distinct().order_by(func.date(ReviewLog.review_time).desc()).limit(30)
    
    streak_res = await db.execute(streak_stmt)
    active_days = streak_res.all()
    
    streak = 0
    dates_set = {datetime.strptime(str(d[0]), "%Y-%m-%d").date() if isinstance(d[0], str) else d[0] for d in active_days}
    current_date = utc_now().date()
    if current_date not in dates_set: 
        current_date -= timedelta(days=1)
    while current_date in dates_set:
        streak += 1
        current_date -= timedelta(days=1)

    # Тематическая раскладка матрицы знаний текущего пользователя
    breakdown = []
    if subject == "all":
        by_sub = defaultdict(list)
        for c in cards:
            if c.subject:
                canon = resolve_subject_alias(c.subject)
                by_sub[canon].append(c)
        for sub, sub_cards in by_sub.items():
            sub_total = len(sub_cards)
            sub_review = sum(1 for c in sub_cards if c.state == 2)
            breakdown.append({
                "label": sub.upper(), 
                "progress": round((sub_review / sub_total) * 100) if sub_total > 0 else 0
            })
    else:
        phrase_stmt = select(Phrase).filter(Phrase.subject.in_(sub_aliases), Phrase.user_id == current_user)
        phrase_res = await db.execute(phrase_stmt)
        phrases = phrase_res.scalars().all()
        for phrase in phrases:
            phrase_cards = [c for c in cards if c.phrase_id == phrase.id]
            p_total = len(phrase_cards)
            p_review = sum(1 for c in phrase_cards if c.state == 2)
            breakdown.append({
                "label": phrase.text, 
                "progress": round((p_review / p_total) * 100) if p_total > 0 else 0
            })

    # Проверяем, пройден ли опрос сегодня именно этим пользователем
    msk_now = utc_now() + timedelta(hours=3)
    msk_today_start = msk_now.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_today_start = msk_today_start - timedelta(hours=3)
    
    survey_stmt = select(DailySession).filter(
        DailySession.user_id == current_user,
        DailySession.timestamp >= utc_today_start
    )
    survey_res = await db.execute(survey_stmt)
    survey_completed = survey_res.scalars().first() is not None

    # Карты к вечеру для текущего пользователя
    if msk_now.hour < 21:
        evening_msk = msk_now.replace(hour=21, minute=0, second=0, microsecond=0)
    else:
        evening_msk = (msk_now + timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
    evening_utc = evening_msk - timedelta(hours=3)
    
    evening_stmt = select(func.count(Card.id)).filter(
        Card.user_id == current_user,
        Card.state.in_([1, 2, 3]),
        Card.next_review <= evening_utc
    )
    if subject != 'all':
        evening_stmt = evening_stmt.filter(Card.subject.in_(sub_aliases))
    evening_res = await db.execute(evening_stmt)
    due_evening = evening_res.scalar() or 0

    # Проверяем карточки, требующие повторения прямо сейчас (REV просроченные + LRN краткосрочные)
    now_utc = utc_now()
    due_stmt = select(func.count(Card.id)).filter(
        Card.user_id == current_user,
        (
            (Card.state == 2) & (Card.next_review <= now_utc)
        ) | (
            Card.state.in_([1, 3])
        )
    )
    if subject != 'all':
        due_stmt = due_stmt.filter(Card.subject.in_(sub_aliases))
    due_res = await db.execute(due_stmt)
    due_reviews_now = due_res.scalar() or 0

    # Проверяем участие в эксперименте и рассчитываем дневную квоту новых карт
    session_stmt = select(UserSession).filter(UserSession.user_id == current_user)
    sess_res = await db.execute(session_stmt)
    user_sess = sess_res.scalars().first()
    is_participant = bool(user_sess and user_sess.is_experiment_participant)
    phase = user_sess.experiment_phase if user_sess else 1

    if is_participant and phase == 1 and not is_admin_or_dev(current_user):
        daily_new_limit = settings.EXPERIMENT_DAILY_LIMIT
    else:
        setting_res = await db.execute(select(UserSetting).filter(UserSetting.user_id == current_user))
        user_setting = setting_res.scalar_one_or_none()
        user_daily_limit = user_setting.daily_limit if user_setting else 10
        canonical_sub = resolve_subject_alias(subject)
        subject_limits = user_setting.subject_limits if (user_setting and user_setting.subject_limits) else {}
        daily_new_limit = subject_limits.get(canonical_sub, subject_limits.get(subject, user_daily_limit))

    # Сколько новых карточек изучено сегодня
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

    unlearned_in_deck = states_dict[0]
    allowed_new_count = max(0, daily_new_limit - already_learned_today)
    new_remaining_today = min(allowed_new_count, unlearned_in_deck)

    # 1. Распределение зрелости колоды по FSRS стабильности:
    maturity = {
        "new": states_dict[0],
        "fragile": 0,
        "developing": 0,
        "mature": 0,
        "mastered": 0
    }
    for c in cards:
        if c.state != 0:
            s_val = c.stability or 0.0
            if s_val < 7.0:
                maturity["fragile"] += 1
            elif s_val < 30.0:
                maturity["developing"] += 1
            elif s_val < 180.0:
                maturity["mature"] += 1
            else:
                maturity["mastered"] += 1

    # 2. Тепловая карта активности (Heatmap) за последние 60 дней
    sixty_days_ago = utc_now() - timedelta(days=60)
    heatmap_stmt = select(func.date(ReviewLog.review_time), func.count(ReviewLog.id)).filter(
        ReviewLog.user_id == current_user,
        ReviewLog.review_time >= sixty_days_ago
    ).group_by(func.date(ReviewLog.review_time))
    heatmap_res = await db.execute(heatmap_stmt)
    heatmap_data = {str(row[0]): row[1] for row in heatmap_res.all() if row[0]}

    # Подсчет изученных карт для режима штурма (все предметы, state in [1, 2, 3])
    cram_available_stmt = select(func.count(Card.id)).filter(
        Card.user_id == current_user,
        Card.state.in_([1, 2, 3])
    )
    cram_available_res = await db.execute(cram_available_stmt)
    cards_cram_available = cram_available_res.scalar() or 0

    return {
        "cards_new": states_dict[0], 
        "cards_learning": states_dict[1] + states_dict[3], 
        "cards_review": states_dict[2],
        "total_cards": total_cards,
        "cards_cram_available": cards_cram_available,
        "due_reviews_now": due_reviews_now,
        "new_remaining_today": new_remaining_today,
        "daily_new_limit": daily_new_limit,
        "already_learned_today": already_learned_today,
        "unlearned_in_deck": unlearned_in_deck,
        "progress_percent": f"{progress_percent}%", 
        "retention_rate_30d": f"{retention_rate}%", 
        "streak_days": streak, 
        "breakdown": breakdown,
        "survey_completed": survey_completed,
        "due_evening": due_evening,
        "maturity": maturity,
        "heatmap": heatmap_data
    }


# --- 9. ТАЙМЕР ПОМОДОРО ---
@router.post("/timer/rest")
async def start_rest_session(
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    now = utc_now()
    rest_end = now + timedelta(minutes=17)
    session_res = await db.execute(select(UserSession).filter(UserSession.telegram_id == current_user))
    session = session_res.scalar_one_or_none()
    if not session: 
        session = UserSession(telegram_id=current_user, user_id=current_user)
        db.add(session)
    session.is_resting = True
    session.rest_ends_at = rest_end
    session.notified = False
    await db.commit()
    return {"status": "rest_started", "rest_ends_at": rest_end}


@router.get("/timer/status")
async def get_timer_status(
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    session_res = await db.execute(select(UserSession).filter(UserSession.telegram_id == current_user))
    session = session_res.scalar_one_or_none()
    if not session or not session.is_resting: 
        return {"is_resting": False, "seconds_left": 0}
    now = utc_now()
    if now >= session.rest_ends_at:
        session.is_resting = False
        await db.commit()
        return {"is_resting": False, "seconds_left": 0}
    return {"is_resting": True, "seconds_left": int((session.rest_ends_at - now).total_seconds())}


# --- 10. ЗАПИСЬ ЕЖЕДНЕВНЫХ ИТОГОВ И КОГНИТИВНОГО ОПРОСА ---
@router.post("/stats/daily_session")
async def log_daily_session(
    payload: DailySessionIn, 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    check_time = utc_now() - timedelta(seconds=60)
    dup_stmt = select(DailySession).filter(
        DailySession.user_id == current_user,
        DailySession.timestamp >= check_time
    )
    dup_res = await db.execute(dup_stmt)
    if dup_res.scalars().first():
        return {"status": "success", "message": "Дубликат пропущен"}

    last_session_stmt = select(DailySession).filter(DailySession.user_id == current_user).order_by(DailySession.id.desc()).limit(1)
    last_session_res = await db.execute(last_session_stmt)
    last_session = last_session_res.scalar_one_or_none()
    
    start_time = utc_now() - timedelta(hours=24)
    if last_session:
        start_time = last_session.timestamp
        
    logs_stmt = select(ReviewLog).filter(
        ReviewLog.user_id == current_user,
        ReviewLog.review_time > start_time
    )
    logs_res = await db.execute(logs_stmt)
    logs = logs_res.scalars().all()
    
    total_reviewed = len(logs)
    new_cards_learned = sum(1 for l in logs if l.state == 0)
    recalls = sum(1 for l in logs if l.rating in (2, 3, 4))
    true_retention = float(recalls) / total_reviewed if total_reviewed > 0 else 0.0
    
    count_stmt = select(func.count(DailySession.id)).filter(DailySession.user_id == current_user)
    count_res = await db.execute(count_stmt)
    total_sessions_count = count_res.scalar_one()
    date_marker = f"День {total_sessions_count + 1}"
    
    session_entry = DailySession(
        user_id=current_user,
        date=date_marker,
        total_reviewed=total_reviewed,
        new_cards_learned=new_cards_learned,
        session_duration=payload.session_duration,
        true_retention=true_retention,
        mental_effort=payload.mental_effort,
        association_utility=payload.association_utility,
        perceived_retention=payload.perceived_retention,
        timestamp=utc_now()
    )
    db.add(session_entry)
    await db.commit()
    
    return {
        "status": "success",
        "date": date_marker,
        "total_reviewed": total_reviewed,
        "new_cards_learned": new_cards_learned,
        "true_retention": true_retention
    }
