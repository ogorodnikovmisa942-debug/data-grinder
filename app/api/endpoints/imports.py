# app/api/endpoints/imports.py
import io
import csv
import re
import json
import os
from typing import Optional, List

from fastapi import APIRouter, Depends, Query, HTTPException, status, UploadFile, Request
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import get_db
from app.database.models import GenerationJob
from app.services.ai_gateway import parse_raw_text, analyze_source_density
from app.services.generation_worker import is_deepseek_offpeak
from app.services.graph_service import resolve_subject_alias
from app.services.card_db_sync import (
    check_experiment_lock, save_cards_to_database, sync_subject_knowledge_and_practice
)
from app.core.auth import get_current_user_id
from app.core.config import settings

router = APIRouter()


def get_is_offpeak() -> bool:
    try:
        import app.api.endpoints.management as mgmt
        return getattr(mgmt, "is_deepseek_offpeak", is_deepseek_offpeak)()
    except Exception:
        return is_deepseek_offpeak()


class ImportIn(BaseModel):
    text: str
    subject: str = ""                    # Целевой предмет, выбранный человеком
    density: str = "medium"
    volume: str = "medium"
    priority: str = "balanced"
    assoc_preference: str = "acoustic"
    granularity_mode: str = "atomic"     # "atomic" | "single_deep" | "cheatsheet"
    custom_instruction: str = ""        # Свободные пожелания пользователя
    commit_now: bool = False            # False = вернуть в Песочницу (Staging)
    is_deferred: bool = False           # True = отправить в очередь Ночного Грайндера (-50% стоимости)


class PresetImportIn(BaseModel):
    preset_name: str
    commit_now: bool = False  # False = вернуть в Песочницу (Staging)


class CardStagingItem(BaseModel):
    text: str = ""
    secondary_text: Optional[str] = ""
    translation: str = ""
    example: Optional[str] = ""
    initial_difficulty_tier: Optional[str] = "medium"
    mnemonic: dict | str | None = None
    theme: Optional[str] = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data):
        if isinstance(data, dict):
            if not data.get("text"):
                data["text"] = data.get("front") or data.get("question") or ""
            if not data.get("secondary_text"):
                data["secondary_text"] = data.get("secondary") or data.get("hint") or ""
            if not data.get("translation"):
                data["translation"] = data.get("back") or data.get("answer") or data.get("definition") or ""
        return data


class StagingCommitIn(BaseModel):
    subject: str
    theme: str
    cards: list[CardStagingItem]
    job_id: Optional[int] = None
    knowledge_graph: Optional[dict] = None


# --- 4. ИИ-КОНВЕЙЕР ИМПОРТА И ПЕСОЧНИЦА (STAGING SANDBOX) ---
@router.post("/config/import")
async def import_raw_text(
    payload: ImportIn, 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    await check_experiment_lock(current_user, db)
    if not payload.text.strip(): 
        return {"status": "error", "message": "Входящий текст пуст."}
    
    target_sub = resolve_subject_alias(payload.subject.strip().lower())
    if not target_sub:
        return {"status": "error", "message": "Целевой предмет не выбран. Выберите предмет из списка или укажите новый."}

    # Если выбрана отложенная обработка «Ночной Грайнд» (-50% стоимости)
    # ИЛИ материал является презентацией / конспектом / объемным текстом (>30 000 зн.)
    density_info = analyze_source_density(payload.text.strip())
    is_dense_material = (
        density_info["is_presentation"]
        or density_info.get("slide_count", 0) >= 8
        or (density_info["is_dense_notes"] and len(payload.text.strip()) >= 10000)
    )
    is_too_large = len(payload.text.strip()) > 30000 or is_dense_material

    if payload.is_deferred or is_too_large:
        is_offpeak = get_is_offpeak()
        archetype_label = (
            f"Презентация ({density_info.get('slide_count', 'слайды')})" if density_info["is_presentation"]
            else ("Конспект / шпаргалка" if density_info["is_dense_notes"] else "Крупный материал")
        )
        if is_offpeak:
            effective_deferred = False
            is_immediate = True
            if is_too_large:
                msg = f"🔥 Скидка 50% активна прямо сейчас! {archetype_label} ({len(payload.text.strip())} знаков) взят(а) в фоновую нарезку со скидкой 50%."
            else:
                msg = f"🔥 Скидка 50% активна прямо сейчас! Материал передан в немедленную фоновую обработку."
        else:
            if payload.is_deferred:
                effective_deferred = True
                is_immediate = False
                msg = "Материал принят в очередь «Ночной Грайнд». Обработка начнется в 19:30 по МСК (со скидкой 50%). Мы уведомим вас о готовности!"
            else:
                effective_deferred = False
                is_immediate = True
                msg = f"{archetype_label} ({len(payload.text.strip())} знаков) взят(а) в немедленную фоновую обработку по дневному тарифу."

        meaningful_lines = [
            l.strip() for l in payload.text.strip().split("\n")
            if l.strip() and not l.strip().startswith("===") and not l.strip().startswith("---")
        ]
        if meaningful_lines:
            extracted_theme = meaningful_lines[0][:40].strip()
        else:
            first_line = payload.text.strip().split("\n")[0][:40].strip()
            extracted_theme = first_line.replace("===", "").strip() or "Новый блок знаний"

        job = GenerationJob(
            user_id=current_user,
            telegram_id=current_user if current_user.isdigit() else None,
            subject=target_sub,
            theme=extracted_theme,
            raw_text=payload.text.strip(),
            granularity_mode=payload.granularity_mode,
            density=payload.density,
            volume=payload.volume,
            custom_instruction=payload.custom_instruction.strip(),
            is_deferred=effective_deferred,
            status="pending"
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)

        return {
            "status": "queued",
            "job_id": job.id,
            "subject": target_sub,
            "theme": job.theme,
            "is_immediate": is_immediate,
            "is_offpeak": is_offpeak,
            "message": msg
        }

    try: 
        parsed_data = await parse_raw_text(
            payload.text,
            target_subject=target_sub,
            density=payload.density,
            volume=payload.volume,
            priority=payload.priority,
            preference=payload.assoc_preference,
            granularity_mode=payload.granularity_mode,
            custom_instruction=payload.custom_instruction
        )
    except Exception as e: 
        import traceback
        traceback.print_exc()
        err_msg = str(e)
        print(f"[ERROR /api/config/import] {err_msg}")
        if "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
            return {"status": "error", "message": f"Лимит или баланс ИИ-провайдера ({settings.AI_PROVIDER}) исчерпан (429). Проверьте баланс или ключ API."}
        return {"status": "error", "message": f"Ошибка ИИ-генератора ({settings.AI_PROVIDER}): {err_msg}"}
        
    if "error" in parsed_data: 
        err_msg = str(parsed_data["error"])
        print(f"[ERROR /api/config/import from parsed_data] {err_msg}")
        if "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
            return {"status": "error", "message": f"Лимит или баланс ИИ-провайдера ({settings.AI_PROVIDER}) исчерпан (429). Проверьте баланс или ключ API."}
        return {"status": "error", "message": err_msg}
        
    subject_slug = target_sub
    phrase_title = parsed_data.get("phrase_title", "Новый блок знаний")
    cards = parsed_data.get("cards", [])
    kg_data = parsed_data.get("knowledge_graph")

    if not payload.commit_now:
        return {
            "status": "staging",
            "subject": subject_slug,
            "theme": phrase_title,
            "cards": cards,
            "knowledge_graph": kg_data
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
            await sync_subject_knowledge_and_practice(
                db=db,
                user_id=current_user,
                subject_slug=clean_sub,
                cards_data=cards,
                kg_data=kg_data,
                fallback_title=clean_title or clean_sub
            )
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
    await check_experiment_lock(current_user, db)
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
        await db.flush()
        if payload.job_id:
            stmt_job = select(GenerationJob).filter(GenerationJob.id == payload.job_id, GenerationJob.user_id == current_user)
            res_job = await db.execute(stmt_job)
            job = res_job.scalar_one_or_none()
            if job:
                job.status = "completed"
                job.cards_count = cards_created
                job.result_cards_json = None

        await sync_subject_knowledge_and_practice(
            db=db,
            user_id=current_user,
            subject_slug=clean_sub,
            cards_data=[c.dict() for c in payload.cards],
            kg_data=payload.knowledge_graph,
            fallback_title=clean_title or clean_sub
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


# --- 4.2 ЗАГРУЗКА ПАЧЕК ФАЙЛОВ НА КОДОВОМ УРОВНЕ (PDF, TXT, MD, CSV) ---
@router.post("/config/import/file")
async def import_file_at_code_level(
    request: Request,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    await check_experiment_lock(current_user, db)
    form = await request.form()
    
    raw_files = form.getlist("files")
    if not raw_files and "file" in form:
        raw_files = [form.get("file")]
    
    upload_list: list[UploadFile] = [
        f for f in raw_files 
        if hasattr(f, "filename") and f.filename
    ]
    if not upload_list:
        raise HTTPException(status_code=400, detail="Не передано ни одного файла.")

    target_sub = resolve_subject_alias(str(form.get("subject", "")).strip().lower())
    if not target_sub:
        raise HTTPException(status_code=400, detail="Целевой предмет не выбран. Выберите предмет из списка или укажите новый.")

    density = str(form.get("density", "medium"))
    volume = str(form.get("volume", "auto"))
    priority = str(form.get("priority", "balanced"))
    assoc_preference = str(form.get("assoc_preference", "acoustic"))
    granularity_mode = str(form.get("granularity_mode", "atomic"))
    custom_instruction = str(form.get("custom_instruction", "")).strip()
    commit_now = str(form.get("commit_now", "")).lower() == "true"
    is_deferred = str(form.get("is_deferred", "")).lower() == "true"

    all_cards: list[dict] = []
    all_extracted_texts: list[str] = []
    file_titles: list[str] = []

    for up_file in upload_list:
        filename = (up_file.filename or "file").lower()
        file_titles.append(up_file.filename or "файл")
        contents = await up_file.read()
        extracted_text = ""

        # 1. Формат PDF: извлечение через pypdf
        if filename.endswith(".pdf"):
            try:
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(contents))
                pages_text = []
                total_pages = len(reader.pages)
                max_pages = min(total_pages, 500)
                for idx in range(max_pages):
                    txt = reader.pages[idx].extract_text() or ""
                    if txt.strip():
                        pages_text.append(f"--- {up_file.filename}: Стр. {idx+1} ---\n{txt.strip()}")
                
                extracted_text = "\n\n".join(pages_text)
                print(f"[PDF Import] {up_file.filename}: извлечено {len(pages_text)} из {total_pages} страниц ({len(extracted_text)} знаков).")

            except Exception as e:
                print(f"[WARN] Ошибка чтения PDF {up_file.filename}: {e}")

        # 2. Формат CSV / TSV: прямой парсинг
        elif filename.endswith(".csv") or filename.endswith(".tsv"):
            delimiter = "\t" if filename.endswith(".tsv") else ","
            try:
                text_stream = io.StringIO(contents.decode("utf-8-sig", errors="ignore"))
                reader = csv.reader(text_stream, delimiter=delimiter)
                for row in reader:
                    if len(row) >= 2 and row[0].strip() and row[1].strip():
                        all_cards.append({
                            "text": row[0].strip(),
                            "secondary_text": row[2].strip() if len(row) > 2 else "",
                            "translation": row[1].strip(),
                            "example": "",
                            "initial_difficulty_tier": "medium",
                            "mnemonic": None
                        })
            except Exception as e:
                print(f"[WARN] Ошибка чтения CSV {up_file.filename}: {e}")

        # 3. Обычный текстовый или Markdown файл
        elif filename.endswith(".txt") or filename.endswith(".md"):
            try:
                extracted_text = contents.decode("utf-8-sig", errors="ignore")
            except Exception as e:
                print(f"[WARN] Ошибка чтения TXT {up_file.filename}: {e}")

        # 4. Формат DOCX
        elif filename.endswith(".docx"):
            try:
                import docx
                doc = docx.Document(io.BytesIO(contents))
                doc_paragraphs = []
                for p in doc.paragraphs:
                    p_text = p.text.strip()
                    if p_text:
                        doc_paragraphs.append(p_text)
                for table in doc.tables:
                    for row in table.rows:
                        row_cells = [c.text.strip() for c in row.cells if c.text.strip()]
                        if row_cells:
                            doc_paragraphs.append(" | ".join(row_cells))
                extracted_text = "\n\n".join(doc_paragraphs)
                print(f"[DOCX Import] {up_file.filename}: извлечено {len(doc_paragraphs)} параграфов ({len(extracted_text)} знаков).")
            except Exception as e:
                print(f"[WARN] Ошибка чтения DOCX {up_file.filename}: {e}")

        # 5. Формат PPTX
        elif filename.endswith(".pptx"):
            try:
                from pptx import Presentation
                prs = Presentation(io.BytesIO(contents))
                slides_text = []
                for s_idx, slide in enumerate(prs.slides, 1):
                    slide_lines = []
                    for shape in slide.shapes:
                        if hasattr(shape, "text") and shape.text.strip():
                            slide_lines.append(shape.text.strip())
                    if slide_lines:
                        slides_text.append(f"--- {up_file.filename}: Слайд {s_idx} ---\n" + "\n".join(slide_lines))
                extracted_text = "\n\n".join(slides_text)
                print(f"[PPTX Import] {up_file.filename}: извлечено {len(slides_text)} слайдов ({len(extracted_text)} знаков).")
            except Exception as e:
                print(f"[WARN] Ошибка чтения PPTX {up_file.filename}: {e}")

        if extracted_text.strip():
            all_extracted_texts.append(f"=== ДОКУМЕНТ: {up_file.filename} ===\n" + extracted_text.strip())

    if all_extracted_texts:
        combined_text = "\n\n".join(all_extracted_texts)
        density_info = analyze_source_density(combined_text)
        is_dense_material = (
            density_info["is_presentation"]
            or density_info.get("slide_count", 0) >= 8
            or (density_info["is_dense_notes"] and len(combined_text) >= 10000)
        )
        is_large = len(combined_text) > 30000 or len(upload_list) > 1 or is_dense_material

        if is_deferred or is_large:
            is_offpeak = is_deepseek_offpeak()
            archetype_label = (
                f"Презентация ({density_info.get('slide_count', 'слайды')})" if density_info["is_presentation"]
                else ("Конспект / шпаргалка" if density_info["is_dense_notes"] else "Крупный документ")
            )
            if is_offpeak:
                effective_deferred = False
                is_immediate = True
                if is_large:
                    msg = f"🔥 Скидка 50% активна прямо сейчас! {archetype_label} ({len(combined_text)} знаков) взят(а) в фоновую нарезку со скидкой 50%."
                else:
                    msg = f"🔥 Скидка 50% активна! Файлы ({len(upload_list)} шт.) переданы в немедленную фоновую обработку."
            else:
                if is_deferred:
                    effective_deferred = True
                    is_immediate = False
                    msg = f"Файлы ({len(upload_list)} шт.) поставлены в очередь «Ночной Грайнд». Обработка начнется в 19:30 по МСК (со скидкой 50%)."
                else:
                    effective_deferred = False
                    is_immediate = True
                    msg = f"{archetype_label} ({len(combined_text)} знаков) взят(а) в немедленную фоновую нарезку по дневному тарифу."

            theme_name = f"Пакетный импорт ({len(upload_list)} док.): {', '.join(file_titles[:2])}"
            if len(file_titles) > 2:
                theme_name += f" и ещё {len(file_titles) - 2}"

            job = GenerationJob(
                user_id=current_user,
                telegram_id=current_user if current_user.isdigit() else None,
                subject=target_sub,
                theme=theme_name,
                raw_text=combined_text,
                granularity_mode=granularity_mode,
                density=density,
                volume=volume,
                custom_instruction=custom_instruction,
                is_deferred=effective_deferred,
                status="pending"
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)

            return {
                "status": "queued",
                "job_id": job.id,
                "subject": target_sub,
                "theme": theme_name,
                "is_immediate": is_immediate,
                "is_offpeak": is_offpeak,
                "message": msg
            }

    if all_extracted_texts:
        combined_text = "\n\n".join(all_extracted_texts)
        try:
            parsed_data = await parse_raw_text(
                combined_text,
                target_subject=target_sub,
                density=density,
                volume=volume,
                priority=priority,
                preference=assoc_preference,
                granularity_mode=granularity_mode,
                custom_instruction=custom_instruction
            )
            if "cards" in parsed_data and isinstance(parsed_data["cards"], list):
                all_cards.extend(parsed_data["cards"])
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[ERROR /api/config/import/file] {e}")
            if not all_cards:
                raise HTTPException(status_code=500, detail=f"Ошибка ИИ при структурировании документов ({settings.AI_PROVIDER}): {str(e)}")

    if not all_cards:
        raise HTTPException(status_code=400, detail="Не удалось извлечь карточки из переданных файлов.")

    subject_slug = target_sub
    theme_name = f"Пакетный импорт ({len(upload_list)} док.): {', '.join(file_titles[:2])}"
    if len(file_titles) > 2:
        theme_name += f" и ещё {len(file_titles) - 2}"

    if not commit_now:
        return {
            "status": "staging",
            "subject": subject_slug,
            "theme": theme_name,
            "cards": all_cards
        }
    else:
        cards_created, clean_sub, clean_title = await save_cards_to_database(
            cards_data=all_cards,
            subject_slug=subject_slug,
            phrase_title=theme_name,
            user_id=current_user,
            db=db
        )
        if cards_created > 0:
            await sync_subject_knowledge_and_practice(
                db=db,
                user_id=current_user,
                subject_slug=clean_sub,
                cards_data=all_cards,
                fallback_title=clean_title or clean_sub
            )
        await db.commit()
        return {"status": "success", "subject": clean_sub, "theme": clean_title, "cards_count": cards_created}


# --- 4.2.0 ВЫГРУЗКА КАРТОЧЕК ИЗ ЗАДАЧИ В ПЕСОЧНИЦУ (STAGING SANDBOX) ---
@router.get("/config/import/staging/job/{job_id}")
async def get_staging_job_cards(
    job_id: int,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Возвращает сформированные карточки фоновой задачи для разбора в Песочнице."""
    stmt = select(GenerationJob).filter(GenerationJob.id == job_id, GenerationJob.user_id == current_user)
    res = await db.execute(stmt)
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена или нет прав доступа.")

    if not job.result_cards_json:
        if job.status in ("pending", "processing"):
            raise HTTPException(status_code=400, detail="Карточки ещё нарезаются ИИ в фоновом режиме. Пожалуйста, подождите завершения.")
        elif job.status == "failed":
            raise HTTPException(status_code=400, detail=f"Ошибка обработки: {job.error_message or 'Неизвестная ошибка'}")
        elif job.status == "completed":
            raise HTTPException(status_code=400, detail="Карточки из этой задачи уже были сохранены в базу знаний.")
        else:
            raise HTTPException(status_code=400, detail="Для этой задачи нет готовых карточек.")

    try:
        cards = json.loads(job.result_cards_json)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка распаковки карточек: {str(e)}")

    return {
        "status": "staging",
        "job_id": job.id,
        "subject": job.subject,
        "theme": job.theme,
        "cards": cards
    }


@router.delete("/config/import/staging/job/{job_id}")
async def delete_staging_job(
    job_id: int,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Удаляет задачу генерации и все её карточки из песочницы/базы данных."""
    stmt = select(GenerationJob).filter(GenerationJob.id == job_id, GenerationJob.user_id == current_user)
    res = await db.execute(stmt)
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена или нет прав доступа.")

    await db.delete(job)
    await db.commit()
    return {"status": "success", "message": f"Колода задачи #{job_id} успешно удалена из песочницы."}


# --- 4.2.1 ОЧЕРЕДЬ НОЧНОГО ГРАЙНДА И ФОНОВЫХ ЗАДАЧ ---
@router.get("/config/import/queue")
async def get_import_queue(
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Возвращает статус задач пользователя в очереди Ночного Грайндера и фоновых задач."""
    stmt = (
        select(GenerationJob)
        .filter(GenerationJob.user_id == current_user)
        .order_by(GenerationJob.id.desc())
        .limit(10)
    )
    res = await db.execute(stmt)
    jobs = res.scalars().all()
    return {
        "status": "success",
        "jobs": [
            {
                "id": j.id,
                "subject": j.subject,
                "theme": j.theme,
                "status": j.status,
                "cards_count": j.cards_count,
                "has_cards": bool(j.result_cards_json),
                "error_message": j.error_message,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "processed_at": j.processed_at.isoformat() if j.processed_at else None
            }
            for j in jobs
        ]
    }


@router.delete("/config/import/queue/{job_id}")
async def cancel_import_queue_job(
    job_id: int,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Отменяет или удаляет задачу пользователя в очереди ночной генерации (включая задачи со статусом ready_for_review)."""
    stmt = select(GenerationJob).filter(GenerationJob.id == job_id, GenerationJob.user_id == current_user)
    res = await db.execute(stmt)
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена или нет прав доступа.")
    
    if job.status in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Нельзя отменить задачу в статусе {job.status}.")
        
    job.status = "cancelled"
    job.error_message = "Отменено пользователем"
    await db.commit()
    return {"status": "success", "message": f"Задача #{job_id} успешно отменена."}


# --- 4.3 ИМПОРТ ГОТОВОЙ БИБЛИОТЕКИ ---
@router.post("/config/import/preset")
async def import_preset_library(
    payload: PresetImportIn, 
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    await check_experiment_lock(current_user, db)
    
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
            await sync_subject_knowledge_and_practice(
                db=db,
                user_id=current_user,
                subject_slug=clean_sub,
                cards_data=cards,
                fallback_title=clean_title or clean_sub
            )
            await db.commit()
            return {"status": "success", "subject": clean_sub, "theme": clean_title, "cards_count": cards_created}
        else:
            await db.rollback()
            return {"status": "error", "message": "В библиотеке нет валидных карточек."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка БД: {str(e)}")
