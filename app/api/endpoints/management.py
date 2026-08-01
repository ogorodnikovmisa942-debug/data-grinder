# app/api/endpoints/management.py
import asyncio
from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from collections import defaultdict
from app.database.session import get_db
from app.database.models import Card, ReviewLog, Phrase, UserSession, DailySession
from app.services.gemini_parser import parse_raw_text, regenerate_card_mnemonic
from datetime import datetime, timedelta

router = APIRouter()

class ConfigUpdate(BaseModel):
    daily_limit: int
    focus_mode_default: bool

class ImportIn(BaseModel):
    text: str
    density: str = "medium"
    volume: str = "medium"
    priority: str = "balanced"
    assoc_preference: str = "acoustic"

class PresetImportIn(BaseModel):
    preset_name: str

# Схема валидации входящего JSON-пакета для переноса карточки
class CardMoveIn(BaseModel):
    target_subject: str

class BulkCardMoveIn(BaseModel):
    card_ids: list[int]
    target_subject: str

class RegenerateMnemonicIn(BaseModel):
    preference: str = "visual"

class DailySessionIn(BaseModel):
    mental_effort: int
    association_utility: int
    perceived_retention: int
    session_duration: int

# Единственное централизованное хранилище лимитов сессий
SUBJECT_LIMITS = {"all": 10}

# --- 1. ВЫДАЧА АРХИВА КАРТОЧЕК ДЛЯ ЭКРАНА DATA ---
@router.get("/data/cards")
async def get_all_cards(
    subject: str = Query("all"), 
    page: int = Query(1, ge=1), 
    limit: int = Query(1000, ge=1, le=10000), 
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Card)
    if subject != 'all': 
        stmt = stmt.filter(Card.subject == subject)
    
    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_res = await db.execute(count_stmt)
    total = count_res.scalar_one()
    
    offset = (page - 1) * limit
    stmt = stmt.offset(offset).limit(limit)
    cards_res = await db.execute(stmt)
    cards = cards_res.scalars().all()
    
    return {
        "total": total, 
        "page": page, 
        "limit": limit,
        "cards": [{"id": c.id, "text": c.text, "secondary_text": c.secondary_text if c.secondary_text else "", "translation": c.translation, "state": c.state, "subject": c.subject, "example": c.example if c.example else ""} for c in cards]
    }

# --- 2. АНАЛИТИКА И ДАШБОРД ДЛЯ ЭКРАНА STATS ---
@router.get("/stats/dashboard")
async def get_analytics(subject: str = Query("all"), db: AsyncSession = Depends(get_db)):
    card_stmt = select(Card)
    if subject != 'all': 
        card_stmt = card_stmt.filter(Card.subject == subject)
    card_res = await db.execute(card_stmt)
    cards = card_res.scalars().all()
    
    states_dict = {0: 0, 1: 0, 2: 0, 3: 0}
    for c in cards: 
        states_dict[c.state if c.state in states_dict else 0] += 1
        
    total_cards = len(cards)
    progress_percent = round((states_dict[2] / total_cards) * 100) if total_cards > 0 else 0

    # Расчет Retention Rate (удержание знаний) за 30 дней
    one_month_ago = datetime.utcnow() - timedelta(days=30)
    
    if subject != 'all':
        log_stmt = select(ReviewLog).join(Card).filter(ReviewLog.review_time >= one_month_ago, Card.subject == subject)
    else:
        log_stmt = select(ReviewLog).filter(ReviewLog.review_time >= one_month_ago)
        
    log_res = await db.execute(log_stmt)
    logs = log_res.scalars().all()
    total_reviews = len(logs)
    successful_reviews = sum(1 for r in logs if r.rating > 1)
    retention_rate = round((successful_reviews / total_reviews) * 100, 1) if total_reviews > 0 else 0.0

    # Расчет ударного режима (Streak)
    streak_stmt = select(func.date(ReviewLog.review_time)).distinct().order_by(func.date(ReviewLog.review_time).desc()).limit(30)
    streak_res = await db.execute(streak_stmt)
    active_days = streak_res.all()
    
    streak = 0
    dates_set = {datetime.strptime(str(d[0]), "%Y-%m-%d").date() if isinstance(d[0], str) else d[0] for d in active_days}
    current_date = datetime.utcnow().date()
    if current_date not in dates_set: 
        current_date -= timedelta(days=1)
    while current_date in dates_set:
        streak += 1
        current_date -= timedelta(days=1)

    # Тематическая раскладка матрицы знаний
    breakdown = []
    if subject == "all":
        # Группируем в памяти, чтобы избежать N+1 запросов к БД
        by_sub = defaultdict(list)
        for c in cards:
            if c.subject:
                by_sub[c.subject].append(c)
        for sub, sub_cards in by_sub.items():
            sub_total = len(sub_cards)
            sub_review = sum(1 for c in sub_cards if c.state == 2)
            breakdown.append({
                "label": sub.upper(), 
                "progress": round((sub_review / sub_total) * 100) if sub_total > 0 else 0
            })
    else:
        phrase_stmt = select(Phrase).filter(Phrase.subject == subject)
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

    # Проверяем, пройден ли опрос сегодня по МСК (UTC+3)
    msk_now = datetime.utcnow() + timedelta(hours=3)
    msk_today_start = msk_now.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_today_start = msk_today_start - timedelta(hours=3)
    
    survey_stmt = select(DailySession).filter(DailySession.timestamp >= utc_today_start)
    survey_res = await db.execute(survey_stmt)
    survey_completed = survey_res.scalars().first() is not None

    # Вычисляем количество карт, готовых к повторению к вечеру (21:00 по МСК / 18:00 по UTC)
    if msk_now.hour < 21:
        evening_msk = msk_now.replace(hour=21, minute=0, second=0, microsecond=0)
    else:
        evening_msk = (msk_now + timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
    evening_utc = evening_msk - timedelta(hours=3)
    
    evening_stmt = select(func.count(Card.id)).filter(
        Card.state.in_([1, 2, 3]),
        Card.next_review <= evening_utc
    )
    if subject != 'all':
        evening_stmt = evening_stmt.filter(Card.subject == subject)
    evening_res = await db.execute(evening_stmt)
    due_evening = evening_res.scalar() or 0

    return {
        "cards_new": states_dict[0], 
        "cards_learning": states_dict[1] + states_dict[3], 
        "cards_review": states_dict[2],
        "progress_percent": f"{progress_percent}%", 
        "retention_rate_30d": f"{retention_rate}%", 
        "streak_days": streak, 
        "breakdown": breakdown,
        "survey_completed": survey_completed,
        "due_evening": due_evening
    }

# --- 3. НАСТРОЙКА ИНТЕНСИВНОСТИ СЕССИЙ (CONFIG) ---
@router.get("/config")
async def get_config(subject: str = Query("all")):
    return {"daily_limit": SUBJECT_LIMITS.get(subject, 10), "focus_mode_default": False}

@router.post("/config")
async def update_config(payload: ConfigUpdate, subject: str = Query("all")):
    SUBJECT_LIMITS[subject] = payload.daily_limit
    return {"status": "updated", "config": {"daily_limit": payload.daily_limit}}

# --- 4. ИИ-КОНВЕЙЕР ИМПОРТА СЫРОГО ТЕКСТА КАРТОЧЕК ---
@router.post("/config/import")
async def import_raw_text(payload: ImportIn, db: AsyncSession = Depends(get_db)):
    if not payload.text.strip(): 
        return {"status": "error", "message": "Входящий текст пуст."}
    try: 
        parsed_data = await parse_raw_text(
            payload.text,
            density=payload.density,
            volume=payload.volume,
            priority=payload.priority,
            preference=payload.assoc_preference
        )
    except Exception as e: 
        return {"status": "error", "message": f"Ошибка вызова Gemini API: {str(e)}"}
        
    if "error" in parsed_data: 
        return {"status": "error", "message": parsed_data["error"]}
        
    subject_slug = parsed_data.get("subject_slug", "generic").lower()
    phrase_title = parsed_data.get("phrase_title", "Новый блок знаний")
    
    try:
        new_phrase = Phrase(text=phrase_title, subject=subject_slug)
        db.add(new_phrase)
        await db.flush() 
        
        cards_created = 0
        for c in parsed_data.get("cards", []):
            if not c.get("text") or not c.get("translation"): 
                continue
            
            difficulty = 5.5  
            if c.get("initial_difficulty_tier") == "easy": 
                difficulty = 3.0  
            elif c.get("initial_difficulty_tier") == "hard": 
                difficulty = 7.5  
                
            stability = 1.0
            mnemonic_json = c.get("mnemonic", None)
            if mnemonic_json and isinstance(mnemonic_json, dict) and mnemonic_json.get("keyword"): 
                stability = 1.5  
                
            card = Card(
                phrase_id=new_phrase.id, subject=subject_slug, text=c["text"],
                secondary_text=c.get("secondary_text", ""), translation=c["translation"],
                example=c.get("example", ""),
                difficulty=difficulty, stability=stability, state=0, mnemonic=mnemonic_json, next_review=datetime.utcnow()  
            )
            db.add(card)
            cards_created += 1
            
        if cards_created > 0:
            await db.commit()
            return {"status": "success", "subject": subject_slug, "theme": phrase_title, "cards_count": cards_created}
        else: 
            await db.rollback()
            return {"status": "error", "message": "ИИ не смог нарезать карточки."}
    except Exception as e: 
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка БД: {str(e)}")

@router.post("/config/import/preset")
async def import_preset_library(payload: PresetImportIn, db: AsyncSession = Depends(get_db)):
    import os
    import json
    
    preset_name = payload.preset_name.strip().lower()
    preset_filename = f"{preset_name}.json"
    preset_path = os.path.join("app", "static", "presets", preset_filename)
    
    if not os.path.exists(preset_path):
        raise HTTPException(status_code=404, detail="Библиотека не найдена.")
        
    try:
        with open(preset_path, "r", encoding="utf-8") as f:
            parsed_data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения файла пресета: {str(e)}")
        
    subject_slug = parsed_data.get("subject_slug", "generic").lower()
    phrase_title = parsed_data.get("phrase_title", "Новый блок знаний")
    
    try:
        new_phrase = Phrase(text=phrase_title, subject=subject_slug)
        db.add(new_phrase)
        await db.flush() 
        
        cards_created = 0
        for c in parsed_data.get("cards", []):
            if not c.get("text") or not c.get("translation"): 
                continue
            
            difficulty = 5.5  
            if c.get("initial_difficulty_tier") == "easy": 
                difficulty = 3.0  
            elif c.get("initial_difficulty_tier") == "hard": 
                difficulty = 7.5  
                
            stability = 1.0
            mnemonic_json = c.get("mnemonic", None)
            if mnemonic_json and isinstance(mnemonic_json, dict) and mnemonic_json.get("keyword"): 
                stability = 1.5  
                
            card = Card(
                phrase_id=new_phrase.id, subject=subject_slug, text=c["text"],
                secondary_text=c.get("secondary_text", ""), translation=c["translation"],
                example=c.get("example", ""),
                difficulty=difficulty, stability=stability, state=0, mnemonic=mnemonic_json, next_review=datetime.utcnow()  
            )
            db.add(card)
            cards_created += 1
            
        if cards_created > 0:
            await db.commit()
            return {"status": "success", "subject": subject_slug, "theme": phrase_title, "cards_count": cards_created}
        else: 
            await db.rollback()
            return {"status": "error", "message": "В библиотеке нет валидных карточек."}
    except Exception as e: 
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка БД: {str(e)}")

# --- 5. МИГРАЦИЯ КАРТОЧЕК МЕЖДУ ПРЕДМЕТАМИ ЧЕРЕЗ СБОРКУ ОПЦИЙ ФРОНТЕНДА ---
@router.post("/management/cards/{card_id}/move")
async def move_card(card_id: int, payload: CardMoveIn, db: AsyncSession = Depends(get_db)):
    card_res = await db.execute(select(Card).filter(Card.id == card_id))
    card = card_res.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    
    target_sub = payload.target_subject.strip().lower()
    
    phrase_res = await db.execute(select(Phrase).filter(Phrase.text == "[МИГРИРОВАВШИЕ КАРТОЧКИ]", Phrase.subject == target_sub))
    phrase = phrase_res.scalar_one_or_none()
    if not phrase:
        phrase = Phrase(text="[МИГРИРОВАВШИЕ КАРТОЧКИ]", subject=target_sub)
        db.add(phrase)
        await db.flush()
        
    card.subject = target_sub
    card.phrase_id = phrase.id
    await db.commit()
    
    return {"status": "success", "card_id": card_id, "target_subject": target_sub}

# --- 6. СИНХРОНИЗАЦИЯ ТАЙМЕРА ПОМОДОРО И ОТДЫХА ---
@router.post("/timer/rest")
async def start_rest_session(tg_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    now = datetime.utcnow()
    rest_end = now + timedelta(minutes=17)
    session_res = await db.execute(select(UserSession).filter(UserSession.telegram_id == tg_id))
    session = session_res.scalar_one_or_none()
    if not session: 
        session = UserSession(telegram_id=tg_id)
        db.add(session)
    session.is_resting = True
    session.rest_ends_at = rest_end
    session.notified = False
    await db.commit()
    return {"status": "rest_started", "rest_ends_at": rest_end}

@router.get("/timer/status")
async def get_timer_status(tg_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    session_res = await db.execute(select(UserSession).filter(UserSession.telegram_id == tg_id))
    session = session_res.scalar_one_or_none()
    if not session or not session.is_resting: 
        return {"is_resting": False, "seconds_left": 0}
    now = datetime.utcnow()
    if now >= session.rest_ends_at:
        session.is_resting = False
        await db.commit()
        return {"is_resting": False, "seconds_left": 0}
    return {"is_resting": True, "seconds_left": int((session.rest_ends_at - now).total_seconds())}

# --- 7. БЕЗОПАСНОЕ УДАЛЕНИЕ КАРТОЧЕК ---
@router.delete("/management/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(card_id: int, db: AsyncSession = Depends(get_db)):
    card_res = await db.execute(select(Card).filter(Card.id == card_id))
    card = card_res.scalar_one_or_none()
    if not card: 
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    await db.delete(card)
    await db.commit()
    return None

# --- 7.5 ПЕРЕГЕНЕРАЦИЯ АССОЦИАЦИИ ---
@router.post("/management/cards/{card_id}/regenerate_mnemonic")
async def regenerate_mnemonic(card_id: int, payload: RegenerateMnemonicIn, db: AsyncSession = Depends(get_db)):
    card_res = await db.execute(select(Card).filter(Card.id == card_id))
    card = card_res.scalar_one_or_none()
    if not card: 
        raise HTTPException(status_code=404, detail="Карточка не найдена")
        
    new_mnemonic = await regenerate_card_mnemonic(
        text=card.text, 
        translation=card.translation, 
        subject=card.subject, 
        preference=payload.preference
    )
    
    if "error" in new_mnemonic:
        raise HTTPException(status_code=500, detail=new_mnemonic["error"])
        
    card.mnemonic = new_mnemonic
    await db.commit()
    return {"status": "success", "mnemonic": new_mnemonic}

# --- 8. МАССОВЫЙ ПЕРЕНОС КАРТОЧЕК (FastAPI + SQLAlchemy) ---
@router.post("/data/cards/move")
async def bulk_move_cards(payload: BulkCardMoveIn, db: AsyncSession = Depends(get_db)):
    if not payload.card_ids or not payload.target_subject.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Идентификаторы карточек не могут быть пустыми и целевой предмет должен быть указан"
        )
    
    target_sub = payload.target_subject.strip().lower()
    
    # Получаем или создаем техническую фразу для мигрировавших карточек
    phrase_res = await db.execute(
        select(Phrase).filter(Phrase.text == "[МИГРИРОВАВШИЕ КАРТОЧКИ]", Phrase.subject == target_sub)
    )
    phrase = phrase_res.scalar_one_or_none()
    if not phrase:
        phrase = Phrase(text="[МИГРИРОВАВШИЕ КАРТОЧКИ]", subject=target_sub)
        db.add(phrase)
        await db.flush()
    
    # Обновляем все карточки за одну атомарную транзакцию
    from sqlalchemy import update
    stmt = (
        update(Card)
        .where(Card.id.in_(payload.card_ids))
        .values(subject=target_sub, phrase_id=phrase.id)
    )
    await db.execute(stmt)
    await db.commit()
    
    return {
        "status": "success",
        "moved_count": len(payload.card_ids),
        "target_subject": target_sub
    }

# --- 9. ЗАПИСЬ ЕЖЕДНЕВНЫХ ИТОГОВ И КОГНИТИВНОГО ОПРОСА ---
@router.post("/stats/daily_session")
async def log_daily_session(
    payload: DailySessionIn, 
    tg_id: str = Query("default_user"),
    db: AsyncSession = Depends(get_db)
):
    # Предотвращение дубликатов: проверяем, была ли запись в последние 60 секунд
    check_time = datetime.utcnow() - timedelta(seconds=60)
    dup_stmt = select(DailySession).filter(DailySession.timestamp >= check_time)
    dup_res = await db.execute(dup_stmt)
    if dup_res.scalars().first():
        print(f"[Daily Session Survey] Игнорируем дубликат запроса от {tg_id}")
        return {"status": "success", "message": "Дубликат пропущен"}

    # Находим время последнего лога сессии, чтобы посчитать показатели
    last_session_stmt = select(DailySession).order_by(DailySession.id.desc()).limit(1)
    last_session_res = await db.execute(last_session_stmt)
    last_session = last_session_res.scalar_one_or_none()
    
    # По умолчанию берем за последние 24 часа
    start_time = datetime.utcnow() - timedelta(hours=24)
    if last_session:
        start_time = last_session.timestamp
        
    # Выгребаем логи повторений, совершенные с момента последней сессии
    logs_stmt = select(ReviewLog).filter(ReviewLog.review_time > start_time)
    logs_res = await db.execute(logs_stmt)
    logs = logs_res.scalars().all()
    
    total_reviewed = len(logs)
    new_cards_learned = sum(1 for l in logs if l.state == 0)
    
    # true_retention = нажатия 2,3,4 делить на общее число нажатий
    recalls = sum(1 for l in logs if l.rating in (2, 3, 4))
    true_retention = float(recalls) / total_reviewed if total_reviewed > 0 else 0.0
    
    # Маркер учебного дня: День {N + 1}
    count_stmt = select(func.count(DailySession.id))
    count_res = await db.execute(count_stmt)
    total_sessions_count = count_res.scalar_one()
    date_marker = f"День {total_sessions_count + 1}"
    
    # Логируем опрос с привязкой к telegram_id пользователя
    print(f"[Daily Session Survey] Сохранение отчета: пользователь={tg_id}, маркер={date_marker}, mental={payload.mental_effort}, assoc={payload.association_utility}, perceived={payload.perceived_retention}")
    
    # Сохраняем сессию
    session_entry = DailySession(
        date=date_marker,
        total_reviewed=total_reviewed,
        new_cards_learned=new_cards_learned,
        session_duration=payload.session_duration,
        true_retention=true_retention,
        mental_effort=payload.mental_effort,
        association_utility=payload.association_utility,
        perceived_retention=payload.perceived_retention,
        timestamp=datetime.utcnow()
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