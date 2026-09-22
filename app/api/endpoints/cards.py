# app/api/endpoints/cards.py
import json
import secrets
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, Query, HTTPException, status, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, update

from app.database.session import get_db
from app.database.models import (
    Card, ReviewLog, Phrase, TopicKnowledgeGraph, PracticeItem, PracticeSessionLog, UserSetting, GenerationJob, utc_now
)
from app.services.ai_gateway import regenerate_card_mnemonic
from app.services.graph_service import (
    resolve_subject_alias, get_all_subject_aliases, clean_graph_data, build_hierarchical_tree
)
from app.services.card_db_sync import (
    check_experiment_lock, sync_subject_knowledge_and_practice
)
from app.core.auth import get_current_user_id
from app.core.config import settings

router = APIRouter()


class ShareDeckIn(BaseModel):
    subject: str


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


class BulkCardDeleteIn(BaseModel):
    card_ids: list[int]


class RegenerateMnemonicIn(BaseModel):
    preference: str = "visual"


class SubjectRenameIn(BaseModel):
    old_subject: str
    new_subject: str


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
        sub_aliases = get_all_subject_aliases(subject)
        stmt = stmt.filter(Card.subject.in_(sub_aliases))
    
    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_res = await db.execute(count_stmt)
    total = count_res.scalar_one()

    if total == 0 and current_user not in ("default_user", "dev_user"):
        stmt_def = select(Card).filter(Card.user_id.in_(["default_user", "dev_user"]))
        if subject != 'all':
            sub_aliases = get_all_subject_aliases(subject)
            stmt_def = stmt_def.filter(Card.subject.in_(sub_aliases))
        count_def_res = await db.execute(select(func.count()).select_from(stmt_def.subquery()))
        total_def = count_def_res.scalar_one()
        if total_def > 0:
            stmt = stmt_def
            total = total_def
    
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


@router.get("/data/cards/export")
async def export_cards_json(
    subject: str = Query("all"),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Экспорт карточек текущего пользователя в эталонном формате пресета .json.
    Идеально подходит для скачивания колоды и последующей раздачи всем участникам.
    """
    sub_aliases = get_all_subject_aliases(subject) if subject != "all" else ["all"]
    stmt = select(Card).filter(Card.user_id == current_user)
    if subject != "all":
        stmt = stmt.filter(Card.subject.in_(sub_aliases))
    stmt = stmt.order_by(Card.topological_rank.asc(), Card.id.asc())

    res = await db.execute(stmt)
    cards = res.scalars().all()

    # Если у пользователя нет карт, но он админ/dev в браузере — проверяем default_user
    if not cards and current_user != "default_user":
        stmt_def = select(Card).filter(Card.user_id == "default_user")
        if subject != "all":
            stmt_def = stmt_def.filter(Card.subject.in_(sub_aliases))
        stmt_def = stmt_def.order_by(Card.topological_rank.asc(), Card.id.asc())
        res_def = await db.execute(stmt_def)
        cards = res_def.scalars().all()

    canonical = resolve_subject_alias(subject)
    phrase_title = "Судоустройство: Основной курс" if canonical == "sudoustroystvo" else f"Курс: {canonical}"
    if cards:
        p_stmt = select(Phrase.text).filter(Phrase.user_id == cards[0].user_id)
        if subject != "all":
            p_stmt = p_stmt.filter(Phrase.subject.in_(sub_aliases))
        found_title = (await db.execute(p_stmt)).scalar()
        if found_title:
            phrase_title = found_title

    payload = {
        "phrase_title": phrase_title,
        "subject_slug": canonical if subject != "all" else (cards[0].subject if cards else "sudoustroystvo"),
        "total_cards": len(cards),
        "cards": [
            {
                "text": c.text,
                "secondary_text": c.secondary_text or "",
                "translation": c.translation,
                "example": c.example or "",
                "mnemonic": c.mnemonic,
                "organ_slug": c.organ_slug,
                "layer": c.layer,
                "topological_rank": c.topological_rank
            }
            for c in cards
        ]
    }

    json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    sub_tag = canonical if subject != "all" else "all_subjects"
    filename = f"grinder_deck_{sub_tag}.json"

    return Response(
        content=json_bytes,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache"
        }
    )


@router.post("/data/cards/share")
async def share_cards_deck(
    payload: ShareDeckIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Генерирует уникальный ключ/диплинк для вирусного шеринга колоды карточек с друзьями в Telegram.
    """
    sub = payload.subject.strip().lower()
    if not sub or sub == "all":
        raise HTTPException(status_code=400, detail="Укажите конкретный предмет для шеринга.")

    canonical = resolve_subject_alias(sub)
    sub_aliases = get_all_subject_aliases(sub)

    stmt = select(Card).filter(Card.user_id == current_user, Card.subject.in_(sub_aliases)).order_by(Card.id.asc())
    cards = (await db.execute(stmt)).scalars().all()
    if not cards and current_user != "default_user":
        stmt_def = select(Card).filter(Card.user_id == "default_user", Card.subject.in_(sub_aliases)).order_by(Card.id.asc())
        cards = (await db.execute(stmt_def)).scalars().all()

    if not cards:
        raise HTTPException(status_code=404, detail="В этой колоде пока нет карточек для шеринга.")

    p_stmt = select(Phrase.text).filter(Phrase.user_id == cards[0].user_id, Phrase.subject.in_(sub_aliases))
    found_title = (await db.execute(p_stmt)).scalar() or f"Колода: {canonical}"

    share_key = secrets.token_hex(4)
    deck_data = {
        "phrase_title": found_title,
        "subject_slug": canonical,
        "created_by": current_user,
        "created_at": utc_now().isoformat(),
        "total_cards": len(cards),
        "cards": [
            {
                "text": c.text,
                "secondary_text": c.secondary_text or "",
                "translation": c.translation,
                "example": c.example or "",
                "mnemonic": c.mnemonic,
                "content_type": getattr(c, "content_type", "text")
            }
            for c in cards
        ]
    }

    shares_dir = Path("app/static/presets/shares")
    shares_dir.mkdir(parents=True, exist_ok=True)
    share_file = shares_dir / f"{share_key}.json"
    share_file.write_text(json.dumps(deck_data, ensure_ascii=False, indent=2), encoding="utf-8")

    bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "DATAGRINDERbot"
    bot_clean = str(bot_username).lstrip("@")
    tg_link = f"https://t.me/{bot_clean}?start=deck_{share_key}"

    return {
        "status": "success",
        "share_key": share_key,
        "subject": sub,
        "theme": found_title,
        "title": found_title,
        "cards_count": len(cards),
        "total_cards": len(cards),
        "tg_link": tg_link,
        "share_url": tg_link
    }


# --- 5. РУЧНОЕ СОЗДАНИЕ И РЕДАКТИРОВАНИЕ КАРТОЧЕК ---
@router.post("/management/cards")
async def create_manual_card(
    payload: ManualCardIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    await check_experiment_lock(current_user, db)
    if not payload.text.strip() or not payload.translation.strip():
        raise HTTPException(status_code=400, detail="Лицевая сторона и перевод обязательны.")

    clean_sub = payload.subject.strip().lower() or "generic"
    canonical = resolve_subject_alias(clean_sub)
    clean_title = payload.phrase_title.strip() or "Пользовательские карточки"
    
    phrase_res = await db.execute(
        select(Phrase).filter(Phrase.text == clean_title, Phrase.subject == canonical, Phrase.user_id == current_user)
    )
    phrase = phrase_res.scalar_one_or_none()
    if not phrase:
        phrase = Phrase(text=clean_title, subject=canonical, user_id=current_user)
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
        subject=canonical,
        text=payload.text.strip(),
        secondary_text=payload.secondary_text.strip(),
        translation=payload.translation.strip(),
        example=payload.example.strip(),
        difficulty=payload.difficulty,
        stability=1.5 if mnemonic_json else 1.0,
        state=0,
        mnemonic=mnemonic_json,
        next_review=utc_now()
    )
    db.add(card)
    await db.flush()
    saved_id = card.id

    # Синхронизируем граф знаний и практику для предмета (R2.2)
    await sync_subject_knowledge_and_practice(
        db=db,
        user_id=current_user,
        subject_slug=canonical,
        fallback_title=clean_title or canonical
    )

    await db.commit()
    return {"status": "success", "card_id": saved_id}


@router.put("/management/cards/{card_id}")
async def update_single_card(
    card_id: int,
    payload: CardUpdateIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    await check_experiment_lock(current_user, db)
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
    await check_experiment_lock(current_user, db)
    card_res = await db.execute(select(Card).filter(Card.id == card_id, Card.user_id == current_user))
    card = card_res.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена или нет прав доступа")
    
    target_sub = resolve_subject_alias(payload.target_subject.strip().lower())
    target_aliases = get_all_subject_aliases(target_sub)
    
    phrase_res = await db.execute(
        select(Phrase).filter(Phrase.text == "[МИГРИРОВАВШИЕ КАРТОЧКИ]", Phrase.subject.in_(target_aliases), Phrase.user_id == current_user)
    )
    phrase = phrase_res.scalar_one_or_none()
    if not phrase:
        phrase = Phrase(text="[МИГРИРОВАВШИЕ КАРТОЧКИ]", subject=target_sub, user_id=current_user)
        db.add(phrase)
        await db.flush()
        
    old_sub = card.subject
    card.subject = target_sub
    card.phrase_id = phrase.id
    await db.commit()
    await sync_subject_knowledge_and_practice(db=db, user_id=current_user, subject_slug=target_sub)
    if old_sub and resolve_subject_alias(old_sub) != target_sub:
        await sync_subject_knowledge_and_practice(db=db, user_id=current_user, subject_slug=old_sub)
    await db.commit()

    return {"status": "success", "card_id": card_id, "target_subject": target_sub}


# --- 7. БЕЗОПАСНОЕ УДАЛЕНИЕ КАРТОЧЕК ---
@router.delete("/management/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(
    card_id: int, 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    await check_experiment_lock(current_user, db)
    card_res = await db.execute(select(Card).filter(Card.id == card_id, Card.user_id == current_user))
    card = card_res.scalar_one_or_none()
    if not card: 
        raise HTTPException(status_code=404, detail="Карточка не найдена или нет прав доступа")
    sub = card.subject
    await db.execute(delete(ReviewLog).where(ReviewLog.card_id == card_id))
    await db.delete(card)
    await db.commit()
    if sub:
        await sync_subject_knowledge_and_practice(db=db, user_id=current_user, subject_slug=sub)
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
    await check_experiment_lock(current_user, db)
    if not payload.card_ids or not payload.target_subject.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Идентификаторы карточек не могут быть пустыми и целевой предмет должен быть указан"
        )
    
    target_sub = resolve_subject_alias(payload.target_subject.strip().lower())
    target_aliases = get_all_subject_aliases(target_sub)
    
    source_subs_res = await db.execute(
        select(Card.subject).where(Card.id.in_(payload.card_ids), Card.user_id == current_user).distinct()
    )
    source_subs = [s[0] for s in source_subs_res.all() if s[0]]

    phrase_res = await db.execute(
        select(Phrase).filter(Phrase.text == "[МИГРИРОВАВШИЕ КАРТОЧКИ]", Phrase.subject.in_(target_aliases), Phrase.user_id == current_user)
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
    await sync_subject_knowledge_and_practice(db=db, user_id=current_user, subject_slug=target_sub)
    for s in source_subs:
        if s and resolve_subject_alias(s) != target_sub:
            await sync_subject_knowledge_and_practice(db=db, user_id=current_user, subject_slug=s)
    await db.commit()
    
    return {
        "status": "success",
        "moved_count": len(payload.card_ids),
        "target_subject": target_sub
    }


# --- 8.5 МАССОВОЕ УДАЛЕНИЕ КАРТОЧЕК И ОЧИСТКА ПРЕДМЕТА ---
@router.post("/data/cards/delete")
async def bulk_delete_cards(
    payload: BulkCardDeleteIn, 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    await check_experiment_lock(current_user, db)
    if not payload.card_ids:
        raise HTTPException(status_code=400, detail="Список идентификаторов пуст")
    
    card_subs_res = await db.execute(
        select(Card.subject).where(Card.id.in_(payload.card_ids), Card.user_id == current_user).distinct()
    )
    affected_subs = [s[0] for s in card_subs_res.all() if s[0]]

    await db.execute(delete(ReviewLog).where(ReviewLog.card_id.in_(payload.card_ids)))
    await db.execute(delete(Card).where(Card.id.in_(payload.card_ids), Card.user_id == current_user))
    await db.commit()

    for s in affected_subs:
        await sync_subject_knowledge_and_practice(db=db, user_id=current_user, subject_slug=s)
    await db.commit()
    return {"status": "success", "deleted_count": len(payload.card_ids)}


# --- 8.1 УПРАВЛЕНИЕ ПРЕДМЕТАМИ (СПИСОК, ПЕРЕИМЕНОВАНИЕ, УДАЛЕНИЕ) ---
@router.get("/data/subjects/details")
async def get_subjects_details(
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(Card.subject, func.count(Card.id))
        .filter(Card.user_id == current_user)
        .group_by(Card.subject)
    )
    res = await db.execute(stmt)
    rows = res.all()

    phrase_stmt = select(Phrase.subject).filter(Phrase.user_id == current_user).distinct()
    phrase_res = await db.execute(phrase_stmt)
    phrase_subs = [s[0] for s in phrase_res.all() if s[0]]

    subjects_map: dict[str, int] = {}
    for sub, count in rows:
        if sub:
            subjects_map[sub] = count

    for ps in phrase_subs:
        if ps and ps not in subjects_map:
            subjects_map[ps] = 0

    subjects_list = [
        {"slug": sub, "name": sub.upper(), "cards_count": subjects_map[sub]}
        for sub in sorted(subjects_map.keys())
    ]
    return {"status": "success", "subjects": subjects_list}


@router.post("/data/subjects/rename")
async def rename_subject(
    payload: SubjectRenameIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    await check_experiment_lock(current_user, db)
    old_sub = payload.old_subject.strip().lower()
    new_sub = payload.new_subject.strip().lower()
    
    if not old_sub or not new_sub:
        raise HTTPException(status_code=400, detail="Название предмета не может быть пустым.")
    if old_sub == "all" or new_sub == "all":
        raise HTTPException(status_code=400, detail="Нельзя использовать зарезервированное имя 'all'.")
    if old_sub == new_sub:
        return {"status": "success", "message": "Имена совпадают", "subject": new_sub, "cards_updated": 0}

    target_subs = get_all_subject_aliases(old_sub)
    if old_sub not in target_subs:
        target_subs.append(old_sub)

    # 1. Обновляем карточки
    card_res = await db.execute(
        update(Card)
        .where(Card.subject.in_(target_subs), Card.user_id == current_user)
        .values(subject=new_sub)
    )
    cards_updated = card_res.rowcount

    # 2. Обновляем темы (Phrase)
    await db.execute(
        update(Phrase)
        .where(Phrase.subject.in_(target_subs), Phrase.user_id == current_user)
        .values(subject=new_sub)
    )

    # 3. Обновляем задачи генерации (GenerationJob)
    await db.execute(
        update(GenerationJob)
        .where(GenerationJob.subject.in_(target_subs), GenerationJob.user_id == current_user)
        .values(subject=new_sub)
    )

    # 4. Обновляем граф знаний, практические задания и лог сессий (R2)
    await db.execute(
        update(TopicKnowledgeGraph)
        .where(TopicKnowledgeGraph.subject.in_(target_subs), TopicKnowledgeGraph.user_id == current_user)
        .values(subject=new_sub)
    )
    await db.execute(
        update(PracticeItem)
        .where(PracticeItem.subject.in_(target_subs), PracticeItem.user_id == current_user)
        .values(subject=new_sub)
    )
    await db.execute(
        update(PracticeSessionLog)
        .where(PracticeSessionLog.subject.in_(target_subs), PracticeSessionLog.user_id == current_user)
        .values(subject=new_sub)
    )

    # 5. Обновляем лимиты в UserSetting
    setting_res = await db.execute(select(UserSetting).filter(UserSetting.user_id == current_user))
    setting = setting_res.scalar_one_or_none()
    if setting and setting.subject_limits:
        limits = dict(setting.subject_limits)
        limits_changed = False
        for s in target_subs:
            if s in limits:
                limits[new_sub] = limits.pop(s)
                limits_changed = True
        if limits_changed:
            setting.subject_limits = limits

    await db.commit()
    return {
        "status": "success",
        "old_subject": old_sub,
        "new_subject": new_sub,
        "cards_updated": cards_updated
    }


@router.delete("/data/subjects/{subject_slug}")
async def delete_subject_all(
    subject_slug: str,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    await check_experiment_lock(current_user, db)
    sub = subject_slug.strip().lower()
    if sub == "all":
        raise HTTPException(status_code=400, detail="Нельзя удалить служебный фильтр 'all'.")

    target_subs = get_all_subject_aliases(sub)
    if sub not in target_subs:
        target_subs.append(sub)

    # 1. Удаляем ReviewLog карточек предмета по всем алиасам (по subject и по phrase_id)
    phrase_ids_res = await db.execute(select(Phrase.id).where(Phrase.subject.in_(target_subs), Phrase.user_id == current_user))
    phrase_ids = phrase_ids_res.scalars().all()

    card_stmt = select(Card.id).where(Card.user_id == current_user)
    if phrase_ids:
        card_stmt = card_stmt.where((Card.subject.in_(target_subs)) | (Card.phrase_id.in_(phrase_ids)))
    else:
        card_stmt = card_stmt.where(Card.subject.in_(target_subs))

    card_ids_res = await db.execute(card_stmt)
    card_ids = card_ids_res.scalars().all()
    if card_ids:
        await db.execute(delete(ReviewLog).where(ReviewLog.card_id.in_(card_ids)))
        await db.execute(delete(Card).where(Card.id.in_(card_ids)))

    # 2. Удаляем Phrase и GenerationJob по всем алиасам
    await db.execute(delete(Phrase).where(Phrase.subject.in_(target_subs), Phrase.user_id == current_user))
    await db.execute(delete(GenerationJob).where(GenerationJob.subject.in_(target_subs), GenerationJob.user_id == current_user))

    # 3. Удаляем граф знаний, практические задания и лог сессий предмета
    await db.execute(delete(TopicKnowledgeGraph).where(
        TopicKnowledgeGraph.subject.in_(target_subs),
        TopicKnowledgeGraph.user_id == current_user
    ))
    await db.execute(delete(PracticeItem).where(
        PracticeItem.subject.in_(target_subs),
        PracticeItem.user_id == current_user
    ))
    await db.execute(delete(PracticeSessionLog).where(
        PracticeSessionLog.subject.in_(target_subs),
        PracticeSessionLog.user_id == current_user
    ))

    # 4. Межпредметные связи (R2.1: очистка cross links в графах знаний других предметов)
    other_kg_stmt = select(TopicKnowledgeGraph).where(
        TopicKnowledgeGraph.user_id == current_user,
        ~TopicKnowledgeGraph.subject.in_(target_subs)
    )
    other_kgs = (await db.execute(other_kg_stmt)).scalars().all()
    for okg in other_kgs:
        if not okg.graph_data:
            continue
        nodes = okg.graph_data.get("nodes", [])
        edges = okg.graph_data.get("edges", [])
        modified = False
        new_edges = []
        for e in edges:
            src = str(e.get("source", ""))
            tgt = str(e.get("target", ""))
            lbl = str(e.get("label", ""))
            if any(s in src.lower() or s in tgt.lower() or s in lbl.lower() for s in target_subs):
                modified = True
            else:
                new_edges.append(e)

        new_nodes = []
        for n in nodes:
            n_id = str(n.get("id", "")).lower()
            n_sub = str(n.get("subject", "")).lower()
            n_name = str(n.get("name", "")).lower()
            if any(s in n_id or s in n_sub or s in n_name for s in target_subs):
                modified = True
            else:
                new_nodes.append(n)

        if modified:
            c_nodes, c_edges = clean_graph_data(new_nodes, new_edges)
            okg.graph_data = {"nodes": c_nodes, "edges": c_edges}
            okg.tree_data = build_hierarchical_tree(c_nodes, c_edges, root_title=okg.subject)
            okg.updated_at = utc_now()

    # 5. Очищаем лимиты из UserSetting по всем алиасам
    setting_res = await db.execute(select(UserSetting).filter(UserSetting.user_id == current_user))
    setting = setting_res.scalar_one_or_none()
    if setting and setting.subject_limits:
        limits = dict(setting.subject_limits)
        limits_changed = False
        for s in target_subs:
            if s in limits:
                del limits[s]
                limits_changed = True
        if limits_changed:
            setting.subject_limits = limits

    await db.commit()
    return {"status": "success", "deleted_subject": sub, "deleted_cards": len(card_ids)}
