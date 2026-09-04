# app/api/endpoints/management.py
import asyncio
import io
import csv
import re
from fastapi import APIRouter, Depends, Query, HTTPException, status, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, update
from collections import defaultdict
from app.database.session import get_db
from app.database.models import Card, ReviewLog, Phrase, UserSession, DailySession, UserSetting
from app.services.ai_gateway import parse_raw_text, regenerate_card_mnemonic
from app.core.auth import get_current_user_id
from app.core.config import settings
from datetime import datetime, timedelta

router = APIRouter()

class ConfigUpdate(BaseModel):
    daily_limit: int
    focus_mode_default: bool = False
    target_retention: float | None = 0.9
    assoc_preference: str | None = "acoustic"

class ImportIn(BaseModel):
    text: str
    density: str = "medium"
    volume: str = "medium"
    priority: str = "balanced"
    assoc_preference: str = "acoustic"
    granularity_mode: str = "atomic"     # "atomic" | "single_deep" | "cheatsheet"
    custom_instruction: str = ""        # Свободные пожелания пользователя
    commit_now: bool = False  # False = вернуть в Песочницу (Staging)

class PresetImportIn(BaseModel):
    preset_name: str
    commit_now: bool = False  # False = вернуть в Песочницу (Staging)

class CardStagingItem(BaseModel):
    text: str
    secondary_text: str = ""
    translation: str
    example: str = ""
    initial_difficulty_tier: str = "medium"
    mnemonic: dict | str | None = None

class StagingCommitIn(BaseModel):
    subject: str
    theme: str
    cards: list[CardStagingItem]

class ManualCardIn(BaseModel):
    subject: str
    phrase_title: str = "Пользовательские карточки"
    text: str
    secondary_text: str = ""
    translation: str
    example: str = ""
    difficulty: float = 5.0
    mnemonic_keyword: str = ""
    mnemonic_cue: str = ""

class CardUpdateIn(BaseModel):
    text: str
    secondary_text: str = ""
    translation: str
    example: str = ""
    mnemonic_keyword: str = ""
    mnemonic_cue: str = ""

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

# Вспомогательная функция для создания карточек в БД
async def save_cards_to_database(cards_data: list, subject_slug: str, phrase_title: str, user_id: str, db: AsyncSession):
    clean_sub = subject_slug.strip().lower() or "generic"
    clean_title = phrase_title.strip() or "Новый блок знаний"
    
    phrase_res = await db.execute(
        select(Phrase).filter(Phrase.text == clean_title, Phrase.subject == clean_sub, Phrase.user_id == user_id)
    )
    phrase = phrase_res.scalar_one_or_none()
    if not phrase:
        phrase = Phrase(text=clean_title, subject=clean_sub, user_id=user_id)
        db.add(phrase)
        await db.flush()

    cards_created = 0
    now = datetime.utcnow()
    for c in cards_data:
        c_text = c.get("text", "") if isinstance(c, dict) else getattr(c, "text", "")
        c_trans = c.get("translation", "") if isinstance(c, dict) else getattr(c, "translation", "")
        if not c_text or not c_trans:
            continue

        c_sec = c.get("secondary_text", "") if isinstance(c, dict) else getattr(c, "secondary_text", "")
        c_ex = c.get("example", "") if isinstance(c, dict) else getattr(c, "example", "")
        c_tier = c.get("initial_difficulty_tier", "medium") if isinstance(c, dict) else getattr(c, "initial_difficulty_tier", "medium")
        c_mnem = c.get("mnemonic", None) if isinstance(c, dict) else getattr(c, "mnemonic", None)

        difficulty = 5.5
        if c_tier == "easy":
            difficulty = 3.5
        elif c_tier == "hard":
            difficulty = 7.5

        stability = 1.0
        if c_mnem:
            if isinstance(c_mnem, dict) and c_mnem.get("keyword"):
                stability = 1.5
            elif isinstance(c_mnem, str) and c_mnem.strip():
                stability = 1.5

        card = Card(
            phrase_id=phrase.id,
            user_id=user_id,
            subject=clean_sub,
            text=c_text,
            secondary_text=c_sec,
            translation=c_trans,
            example=c_ex,
            difficulty=difficulty,
            stability=stability,
            state=0,
            mnemonic=c_mnem,
            next_review=now
        )
        db.add(card)
        cards_created += 1

    return cards_created, clean_sub, clean_title

# --- 1. ВЫДАЧА АРХИВА КАРТОЧЕК ТЕКУЩЕГО ПОЛЬЗОВАТЕЛЯ ---
@router.get("/data/cards")
async def get_all_cards(
    subject: str = Query("all"), 
    page: int = Query(1, ge=1), 
    limit: int = Query(1000, ge=1, le=10000), 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Card).filter(Card.user_id == current_user)
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
        "cards": [
            {
                "id": c.id, 
                "text": c.text, 
                "secondary_text": c.secondary_text if c.secondary_text else "", 
                "translation": c.translation, 
                "state": c.state, 
                "subject": c.subject, 
                "example": c.example if c.example else "",
                "mnemonic": c.mnemonic
            } 
            for c in cards
        ]
    }

# --- 2. АНАЛИТИКА И ДАШБОРД ТЕКУЩЕГО ПОЛЬЗОВАТЕЛЯ ---
@router.get("/stats/dashboard")
async def get_analytics(
    subject: str = Query("all"), 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    card_stmt = select(Card).filter(Card.user_id == current_user)
    if subject != 'all': 
        card_stmt = card_stmt.filter(Card.subject == subject)
    card_res = await db.execute(card_stmt)
    cards = card_res.scalars().all()
    
    states_dict = {0: 0, 1: 0, 2: 0, 3: 0}
    for c in cards: 
        states_dict[c.state if c.state in states_dict else 0] += 1
        
    total_cards = len(cards)
    progress_percent = round((states_dict[2] / total_cards) * 100) if total_cards > 0 else 0

    # Расчет Retention Rate за 30 дней для конкретного пользователя
    one_month_ago = datetime.utcnow() - timedelta(days=30)
    
    if subject != 'all':
        log_stmt = select(ReviewLog).join(Card, ReviewLog.card_id == Card.id).filter(
            ReviewLog.user_id == current_user,
            ReviewLog.review_time >= one_month_ago, 
            Card.subject == subject
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
    current_date = datetime.utcnow().date()
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
                by_sub[c.subject].append(c)
        for sub, sub_cards in by_sub.items():
            sub_total = len(sub_cards)
            sub_review = sum(1 for c in sub_cards if c.state == 2)
            breakdown.append({
                "label": sub.upper(), 
                "progress": round((sub_review / sub_total) * 100) if sub_total > 0 else 0
            })
    else:
        phrase_stmt = select(Phrase).filter(Phrase.subject == subject, Phrase.user_id == current_user)
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
    msk_now = datetime.utcnow() + timedelta(hours=3)
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

# --- 3. НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ ИЗ ТАБЛИЦЫ БД ---
@router.get("/config")
async def get_config(
    subject: str = Query("all"),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    setting_res = await db.execute(select(UserSetting).filter(UserSetting.user_id == current_user))
    setting = setting_res.scalar_one_or_none()
    
    daily_limit = 10
    subject_limits = {}
    assoc_pref = "acoustic"
    target_retention = 0.9
    
    if setting:
        daily_limit = setting.daily_limit
        subject_limits = setting.subject_limits or {}
        assoc_pref = setting.assoc_preference or "acoustic"
        target_retention = setting.target_retention or 0.9
        
    current_subject_limit = subject_limits.get(subject, daily_limit)
    return {
        "daily_limit": current_subject_limit, 
        "focus_mode_default": False,
        "assoc_preference": assoc_pref,
        "target_retention": target_retention
    }

@router.post("/config")
async def update_config(
    payload: ConfigUpdate, 
    subject: str = Query("all"),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    setting_res = await db.execute(select(UserSetting).filter(UserSetting.user_id == current_user))
    setting = setting_res.scalar_one_or_none()
    
    if not setting:
        setting = UserSetting(
            user_id=current_user,
            daily_limit=payload.daily_limit,
            subject_limits={subject: payload.daily_limit}
        )
        db.add(setting)
    else:
        subject_limits = dict(setting.subject_limits or {})
        subject_limits[subject] = payload.daily_limit
        setting.subject_limits = subject_limits
        if subject == "all":
            setting.daily_limit = payload.daily_limit
        if payload.target_retention is not None:
            setting.target_retention = payload.target_retention
        if payload.assoc_preference is not None:
            setting.assoc_preference = payload.assoc_preference

    await db.commit()
    return {"status": "updated", "config": {"daily_limit": payload.daily_limit}}

# --- 4. ИИ-КОНВЕЙЕР ИМПОРТА И ПЕСОЧНИЦА (STAGING SANDBOX) ---
@router.post("/config/import")
async def import_raw_text(
    payload: ImportIn, 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    if not payload.text.strip(): 
        return {"status": "error", "message": "Входящий текст пуст."}
    try: 
        parsed_data = await parse_raw_text(
            payload.text,
            density=payload.density,
            volume=payload.volume,
            priority=payload.priority,
            preference=payload.assoc_preference,
            granularity_mode=payload.granularity_mode,
            custom_instruction=payload.custom_instruction
        )
    except Exception as e: 
        err_msg = str(e)
        if "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
            return {"status": "error", "message": f"Лимит или баланс ИИ-провайдера ({settings.AI_PROVIDER}) исчерпан (429). Проверьте баланс или ключ API."}
        return {"status": "error", "message": f"Ошибка ИИ-генератора ({settings.AI_PROVIDER}): {err_msg}"}
        
    if "error" in parsed_data: 
        err_msg = str(parsed_data["error"])
        if "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
            return {"status": "error", "message": f"Лимит или баланс ИИ-провайдера ({settings.AI_PROVIDER}) исчерпан (429). Проверьте баланс или ключ API."}
        return {"status": "error", "message": err_msg}
        
    subject_slug = parsed_data.get("subject_slug", "generic").lower()
    phrase_title = parsed_data.get("phrase_title", "Новый блок знаний")
    cards = parsed_data.get("cards", [])

    # Если включен режим Staging (по умолчанию True для фронтенда), возвращаем карточки в Песочницу!
    if not payload.commit_now:
        return {
            "status": "staging",
            "subject": subject_slug,
            "theme": phrase_title,
            "cards": cards
        }

    # Прямое сохранение, если запрошено
    try:
        cards_created, clean_sub, clean_title = await save_cards_to_database(
            cards_data=cards, 
            subject_slug=subject_slug, 
            phrase_title=phrase_title, 
            user_id=current_user, 
            db=db
        )
        if cards_created > 0:
            await db.commit()
            return {"status": "success", "subject": clean_sub, "theme": clean_title, "cards_count": cards_created}
        else:
            await db.rollback()
            return {"status": "error", "message": "ИИ не смог нарезать карточки."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка БД: {str(e)}")

# --- 4.1 ФИКСАЦИЯ ОДОБРЕННЫХ КАРТОЧЕК ИЗ ПЕСОЧНИЦЫ (STAGING COMMIT) ---
@router.post("/config/import/commit")
async def commit_staging_cards(
    payload: StagingCommitIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    if not payload.cards:
        raise HTTPException(status_code=400, detail="Список одобренных карточек пуст.")

    try:
        cards_created, clean_sub, clean_title = await save_cards_to_database(
            cards_data=[c.dict() for c in payload.cards],
            subject_slug=payload.subject,
            phrase_title=payload.theme,
            user_id=current_user,
            db=db
        )
        await db.commit()
        return {
            "status": "success", 
            "subject": clean_sub, 
            "theme": clean_title, 
            "cards_count": cards_created
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения из песочницы: {str(e)}")

# --- 4.2 ЗАГРУЗКА ФАЙЛОВ НА КОДОВОМ УРОВНЕ (БЕЗ ДОРОГОГО LLM) ---
@router.post("/config/import/file")
async def import_file_at_code_level(
    file: UploadFile = File(...),
    density: str = Form("medium"),
    volume: str = Form("medium"),
    priority: str = Form("balanced"),
    assoc_preference: str = Form("acoustic"),
    granularity_mode: str = Form("atomic"),
    custom_instruction: str = Form(""),
    commit_now: bool = Form(False),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    filename = file.filename.lower()
    contents = await file.read()
    extracted_text = ""

    # 1. Формат PDF: бесплатное извлечение на чистом Python через pypdf
    if filename.endswith(".pdf"):
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(contents))
            pages_text = []
            for idx, page in enumerate(reader.pages):
                txt = page.extract_text() or ""
                if txt.strip():
                    pages_text.append(f"--- Страница {idx+1} ---\n{txt}")
            extracted_text = "\n\n".join(pages_text)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Ошибка чтения PDF файла: {str(e)}")

    # 2. Формат CSV / TSV: парсинг без LLM вообще!
    elif filename.endswith(".csv") or filename.endswith(".tsv"):
        delimiter = "\t" if filename.endswith(".tsv") else ","
        try:
            text_stream = io.StringIO(contents.decode("utf-8-sig", errors="ignore"))
            reader = csv.reader(text_stream, delimiter=delimiter)
            rows = list(reader)
            
            cards = []
            for row in rows:
                if len(row) >= 2 and row[0].strip() and row[1].strip():
                    front = row[0].strip()
                    back = row[1].strip()
                    hint = row[2].strip() if len(row) > 2 else ""
                    cards.append({
                        "text": front,
                        "secondary_text": hint,
                        "translation": back,
                        "example": "",
                        "initial_difficulty_tier": "medium",
                        "mnemonic": None
                    })
            
            if cards:
                sub_name = re.sub(r'[^a-z0-9_]', '_', filename.split('.')[0].lower())
                theme_name = f"Импорт файла {file.filename}"
                
                if not commit_now:
                    return {
                        "status": "staging",
                        "subject": sub_name,
                        "theme": theme_name,
                        "cards": cards
                    }
                else:
                    cards_created, clean_sub, clean_title = await save_cards_to_database(
                        cards_data=cards,
                        subject_slug=sub_name,
                        phrase_title=theme_name,
                        user_id=current_user,
                        db=db
                    )
                    await db.commit()
                    return {"status": "success", "subject": clean_sub, "theme": clean_title, "cards_count": cards_created}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Ошибка парсинга CSV: {str(e)}")

    # 3. Обычный текстовый или Markdown файл
    elif filename.endswith(".txt") or filename.endswith(".md"):
        try:
            extracted_text = contents.decode("utf-8-sig", errors="ignore")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Ошибка чтения текста: {str(e)}")
    else:
        raise HTTPException(status_code=400, detail="Неподдерживаемый формат. Используйте .pdf, .txt, .md, .csv, .tsv")

    if not extracted_text.strip():
        raise HTTPException(status_code=400, detail="Не удалось извлечь текст из файла.")

    # Передаем извлеченный текст в ИИ-структуризатор
    try:
        parsed_data = await parse_raw_text(
            extracted_text,
            density=density,
            volume=volume,
            priority=priority,
            preference=assoc_preference,
            granularity_mode=granularity_mode,
            custom_instruction=custom_instruction
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка ИИ при структурировании файла ({settings.AI_PROVIDER}): {str(e)}")

    if "error" in parsed_data:
        return {"status": "error", "message": parsed_data["error"]}

    subject_slug = parsed_data.get("subject_slug", "generic").lower()
    phrase_title = parsed_data.get("phrase_title", f"Импорт: {file.filename}")
    cards = parsed_data.get("cards", [])

    if not commit_now:
        return {
            "status": "staging",
            "subject": subject_slug,
            "theme": phrase_title,
            "cards": cards
        }
    else:
        cards_created, clean_sub, clean_title = await save_cards_to_database(
            cards_data=cards,
            subject_slug=subject_slug,
            phrase_title=phrase_title,
            user_id=current_user,
            db=db
        )
        await db.commit()
        return {"status": "success", "subject": clean_sub, "theme": clean_title, "cards_count": cards_created}

# --- 4.3 ИМПОРТ ГОТОВОЙ БИБЛИОТЕКИ ---
@router.post("/config/import/preset")
async def import_preset_library(
    payload: PresetImportIn, 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    import os
    import json
    
    preset_name = payload.preset_name.strip().lower()
    if not re.match(r'^[a-z0-9_]+$', preset_name):
        raise HTTPException(status_code=400, detail="Недопустимое имя пресета.")
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
    cards = parsed_data.get("cards", [])

    if not payload.commit_now:
        return {
            "status": "staging",
            "subject": subject_slug,
            "theme": phrase_title,
            "cards": cards
        }
        
    try:
        cards_created, clean_sub, clean_title = await save_cards_to_database(
            cards_data=cards,
            subject_slug=subject_slug,
            phrase_title=phrase_title,
            user_id=current_user,
            db=db
        )
        if cards_created > 0:
            await db.commit()
            return {"status": "success", "subject": clean_sub, "theme": clean_title, "cards_count": cards_created}
        else:
            await db.rollback()
            return {"status": "error", "message": "В библиотеке нет валидных карточек."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка БД: {str(e)}")

# --- 5. РУЧНОЕ СОЗДАНИЕ И РЕДАКТИРОВАНИЕ КАРТОЧЕК ---
@router.post("/management/cards")
async def create_manual_card(
    payload: ManualCardIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    if not payload.text.strip() or not payload.translation.strip():
        raise HTTPException(status_code=400, detail="Лицевая сторона и перевод обязательны.")

    clean_sub = payload.subject.strip().lower() or "generic"
    clean_title = payload.phrase_title.strip() or "Пользовательские карточки"
    
    phrase_res = await db.execute(
        select(Phrase).filter(Phrase.text == clean_title, Phrase.subject == clean_sub, Phrase.user_id == current_user)
    )
    phrase = phrase_res.scalar_one_or_none()
    if not phrase:
        phrase = Phrase(text=clean_title, subject=clean_sub, user_id=current_user)
        db.add(phrase)
        await db.flush()

    mnemonic_json = None
    if payload.mnemonic_keyword or payload.mnemonic_cue:
        mnemonic_json = {
            "keyword": payload.mnemonic_keyword.strip(),
            "verbal_cue": payload.mnemonic_cue.strip()
        }

    card = Card(
        phrase_id=phrase.id,
        user_id=current_user,
        subject=clean_sub,
        text=payload.text.strip(),
        secondary_text=payload.secondary_text.strip(),
        translation=payload.translation.strip(),
        example=payload.example.strip(),
        difficulty=payload.difficulty,
        stability=1.5 if mnemonic_json else 1.0,
        state=0,
        mnemonic=mnemonic_json,
        next_review=datetime.utcnow()
    )
    db.add(card)
    await db.flush()
    saved_id = card.id
    await db.commit()
    return {"status": "success", "card_id": saved_id}

@router.put("/management/cards/{card_id}")
async def update_single_card(
    card_id: int,
    payload: CardUpdateIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    card_res = await db.execute(select(Card).filter(Card.id == card_id, Card.user_id == current_user))
    card = card_res.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена или нет прав доступа")

    card.text = payload.text.strip()
    card.secondary_text = payload.secondary_text.strip()
    card.translation = payload.translation.strip()
    card.example = payload.example.strip()

    if payload.mnemonic_keyword or payload.mnemonic_cue:
        card.mnemonic = {
            "keyword": payload.mnemonic_keyword.strip(),
            "verbal_cue": payload.mnemonic_cue.strip()
        }

    await db.commit()
    return {"status": "success", "card_id": card.id}

# --- 6. МИГРАЦИЯ КАРТОЧЕК МЕЖДУ ПРЕДМЕТАМИ ---
@router.post("/management/cards/{card_id}/move")
async def move_card(
    card_id: int, 
    payload: CardMoveIn, 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    card_res = await db.execute(select(Card).filter(Card.id == card_id, Card.user_id == current_user))
    card = card_res.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена или нет прав доступа")
    
    target_sub = payload.target_subject.strip().lower()
    
    phrase_res = await db.execute(
        select(Phrase).filter(Phrase.text == "[МИГРИРОВАВШИЕ КАРТОЧКИ]", Phrase.subject == target_sub, Phrase.user_id == current_user)
    )
    phrase = phrase_res.scalar_one_or_none()
    if not phrase:
        phrase = Phrase(text="[МИГРИРОВАВШИЕ КАРТОЧКИ]", subject=target_sub, user_id=current_user)
        db.add(phrase)
        await db.flush()
        
    card.subject = target_sub
    card.phrase_id = phrase.id
    await db.commit()
    
    return {"status": "success", "card_id": card_id, "target_subject": target_sub}

# --- 7. БЕЗОПАСНОЕ УДАЛЕНИЕ КАРТОЧЕК ---
@router.delete("/management/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(
    card_id: int, 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    card_res = await db.execute(select(Card).filter(Card.id == card_id, Card.user_id == current_user))
    card = card_res.scalar_one_or_none()
    if not card: 
        raise HTTPException(status_code=404, detail="Карточка не найдена или нет прав доступа")
    await db.delete(card)
    await db.commit()
    return None

# --- 7.5 ПЕРЕГЕНЕРАЦИЯ АССОЦИАЦИИ ---
@router.post("/management/cards/{card_id}/regenerate_mnemonic")
async def regenerate_mnemonic(
    card_id: int, 
    payload: RegenerateMnemonicIn, 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    card_res = await db.execute(select(Card).filter(Card.id == card_id, Card.user_id == current_user))
    card = card_res.scalar_one_or_none()
    if not card: 
        raise HTTPException(status_code=404, detail="Карточка не найдена или нет прав доступа")
        
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

# --- 8. МАССОВЫЙ ПЕРЕНОС КАРТОЧЕК ---
@router.post("/data/cards/move")
async def bulk_move_cards(
    payload: BulkCardMoveIn, 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    if not payload.card_ids or not payload.target_subject.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Идентификаторы карточек не могут быть пустыми и целевой предмет должен быть указан"
        )
    
    target_sub = payload.target_subject.strip().lower()
    
    phrase_res = await db.execute(
        select(Phrase).filter(Phrase.text == "[МИГРИРОВАВШИЕ КАРТОЧКИ]", Phrase.subject == target_sub, Phrase.user_id == current_user)
    )
    phrase = phrase_res.scalar_one_or_none()
    if not phrase:
        phrase = Phrase(text="[МИГРИРОВАВШИЕ КАРТОЧКИ]", subject=target_sub, user_id=current_user)
        db.add(phrase)
        await db.flush()
    
    stmt = (
        update(Card)
        .where(Card.id.in_(payload.card_ids), Card.user_id == current_user)
        .values(subject=target_sub, phrase_id=phrase.id)
    )
    await db.execute(stmt)
    await db.commit()
    
    return {
        "status": "success",
        "moved_count": len(payload.card_ids),
        "target_subject": target_sub
    }

# --- 9. ТАЙМЕР ПОМОДОРО ---
@router.post("/timer/rest")
async def start_rest_session(
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    now = datetime.utcnow()
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
    now = datetime.utcnow()
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
    check_time = datetime.utcnow() - timedelta(seconds=60)
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
    
    start_time = datetime.utcnow() - timedelta(hours=24)
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