# app/api/endpoints/admin.py
import csv
import io
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text, select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
import json
import secrets
from pathlib import Path
from typing import Optional
from app.core.config import settings
from app.database.session import get_db
from app.database.models import UserSession, UserSetting, AiTelemetryLog, InviteCode, Card, ReviewLog, Phrase
from app.api.endpoints.management import save_cards_to_database, append_or_sync_cards_to_database

router = APIRouter()

class SwitchPhaseIn(BaseModel):
    phase: int = 2

class SetParticipantIn(BaseModel):
    user_id: str
    is_participant: bool = True
    phase: int = 1

def verify_admin_token(request: Request):
    """Проверка административного токена из заголовка X-Admin-Token."""
    header_token = request.headers.get("x-admin-token") or request.headers.get("X-Admin-Token")
    if not header_token or header_token != settings.ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещен: недействительный или отсутствующий X-Admin-Token"
        )
    return header_token

# --- 5.1. ВЫГРУЗКА ДАТАСЕТА ЭКСПЕРИМЕНТА В CSV ДЛЯ PANDAS / R ---
@router.get("/export/experiment-dataset")
async def export_experiment_dataset(
    token: str = Depends(verify_admin_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Формирует и отдает потоковый CSV-датасет участников научного эксперимента (Фаза 1).
    Кодировка: UTF-8 с BOM (\ufeff) для безупречной загрузки в pandas.read_csv() и Excel без битых символов.
    """
    sql_query = text("""
        SELECT 
            u.user_id AS user_id,
            r.id AS log_id,
            r.card_id,
            c.subject AS subject_id,
            r.rating,
            r.response_time,
            r.is_outlier,
            r.is_cram,
            r.stability,
            r.difficulty,
            r.elapsed_days,
            r.scheduled_days,
            d.mental_effort,
            d.perceived_retention,
            d.true_retention,
            d.session_duration,
            r.timestamp AS review_time
        FROM review_logs r
        JOIN user_sessions u ON r.user_id = u.user_id
        JOIN cards c ON r.card_id = c.id
        LEFT JOIN daily_sessions d ON r.user_id = d.user_id 
             AND DATE(r.timestamp) = DATE(d.timestamp)
        WHERE u.is_experiment_participant = 1
        ORDER BY r.user_id, r.timestamp ASC;
    """)

    result = await db.execute(sql_query)
    rows = result.mappings().all()

    csv_headers = [
        "user_id", "log_id", "card_id", "subject_id", "rating",
        "response_time", "is_outlier", "is_cram", "stability",
        "difficulty", "elapsed_days", "scheduled_days",
        "mental_effort", "perceived_retention", "true_retention",
        "session_duration", "review_time"
    ]

    async def csv_stream():
        output = io.StringIO()
        # UTF-8 BOM для гарантированной поддержки кириллицы в Pandas и Excel
        output.write("\ufeff")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(csv_headers)
        yield output.getvalue().encode("utf-8")
        output.seek(0)
        output.truncate(0)

        for row in rows:
            record = []
            for col in csv_headers:
                val = row.get(col)
                if isinstance(val, datetime):
                    val = val.isoformat()
                elif isinstance(val, bool):
                    val = 1 if val else 0
                elif val is None:
                    val = ""
                record.append(val)
            writer.writerow(record)
            yield output.getvalue().encode("utf-8")
            output.seek(0)
            output.truncate(0)

    return StreamingResponse(
        csv_stream(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="grinder_experiment_phase1.csv"',
            "Cache-Control": "no-cache"
        }
    )

# --- 5.2. ВЫГРУЗКА ИНЖЕНЕРНОЙ ТЕЛЕМЕТРИИ ИИ (CSV / JSON) ---
@router.get("/export/ai-telemetry")
async def export_ai_telemetry(
    format: str = Query("json", regex="^(json|csv)$"),
    limit: int = Query(1000, ge=1, le=50000),
    token: str = Depends(verify_admin_token),
    db: AsyncSession = Depends(get_db)
):
    """Выгружает журнал сбоев, задержек и кэширования LLM-шлюза (AiTelemetryLog)."""
    stmt = (
        select(AiTelemetryLog)
        .order_by(AiTelemetryLog.created_at.desc())
        .limit(limit)
    )
    res = await db.execute(stmt)
    logs = res.scalars().all()

    fields = [
        "job_id", "input_chars", "model_requested", "model_resolved",
        "cache_hit", "is_truncated", "repair_successful", "cards_generated",
        "duration_ms", "status", "error_message", "created_at"
    ]

    if format == "json":
        data = []
        for l in logs:
            data.append({
                "id": l.id,
                "job_id": l.job_id,
                "user_id": l.user_id,
                "input_chars": l.input_chars,
                "prompt_tokens": l.prompt_tokens,
                "completion_tokens": l.completion_tokens,
                "model_requested": l.model_requested,
                "model_resolved": l.model_resolved,
                "cache_hit": bool(l.cache_hit),
                "is_truncated": bool(l.is_truncated),
                "repair_successful": bool(l.repair_successful),
                "cards_generated": l.cards_generated,
                "duration_ms": l.duration_ms,
                "status": l.status,
                "error_message": l.error_message,
                "created_at": l.created_at.isoformat() if l.created_at else None
            })
        return {"status": "success", "count": len(data), "telemetry": data}

    # CSV экспорт
    async def csv_stream():
        output = io.StringIO()
        output.write("\ufeff")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(fields)
        yield output.getvalue().encode("utf-8")
        output.seek(0)
        output.truncate(0)

        for l in logs:
            row = [
                l.job_id or "",
                l.input_chars,
                l.model_requested,
                l.model_resolved,
                1 if l.cache_hit else 0,
                1 if l.is_truncated else 0,
                1 if l.repair_successful else 0,
                l.cards_generated,
                l.duration_ms,
                l.status,
                l.error_message or "",
                l.created_at.isoformat() if l.created_at else ""
            ]
            writer.writerow(row)
            yield output.getvalue().encode("utf-8")
            output.seek(0)
            output.truncate(0)

    return StreamingResponse(
        csv_stream(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="ai_telemetry.csv"',
            "Cache-Control": "no-cache"
        }
    )

# --- 5.3. ПЕРЕКЛЮЧЕНИЕ ФАЗЫ ЭКСПЕРИМЕНТА ---
@router.post("/experiment/switch-phase")
async def switch_experiment_phase(
    payload: SwitchPhaseIn,
    token: str = Depends(verify_admin_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Массовое переключение фазы для участников научного эксперимента.
    При phase=2 снимает все блокировки колоды и импорта.
    """
    stmt_sessions = (
        update(UserSession)
        .where(UserSession.is_experiment_participant == True)
        .values(experiment_phase=payload.phase)
    )
    res_sessions = await db.execute(stmt_sessions)
    updated_sessions = res_sessions.rowcount

    stmt_settings = (
        update(UserSetting)
        .where(UserSetting.is_experiment_participant == True)
        .values(experiment_phase=payload.phase)
    )
    await db.execute(stmt_settings)
    await db.commit()

    return {
        "status": "success",
        "phase": payload.phase,
        "updated_users": updated_sessions,
        "message": f"Фаза успешно переключена на {payload.phase} для {updated_sessions} участников."
    }

# --- 5.4. УПРАВЛЕНИЕ СТАТУСОМ УЧАСТНИКА (ВКЛЮЧЕНИЕ / ВЫКЛЮЧЕНИЕ) ---
@router.post("/experiment/set-participant")
async def set_experiment_participant(
    payload: SetParticipantIn,
    token: str = Depends(verify_admin_token),
    db: AsyncSession = Depends(get_db)
):
    """Устанавливает статус участия в эксперименте для конкретного пользователя."""
    # Обновляем UserSession
    stmt_sess = select(UserSession).filter(UserSession.user_id == payload.user_id)
    res_sess = await db.execute(stmt_sess)
    sess = res_sess.scalar_one_or_none()
    if not sess:
        sess = UserSession(
            telegram_id=payload.user_id,
            user_id=payload.user_id,
            is_experiment_participant=payload.is_participant,
            experiment_phase=payload.phase
        )
        db.add(sess)
    else:
        sess.is_experiment_participant = payload.is_participant
        sess.experiment_phase = payload.phase

    # Обновляем UserSetting
    stmt_set = select(UserSetting).filter(UserSetting.user_id == payload.user_id)
    res_set = await db.execute(stmt_set)
    user_set = res_set.scalar_one_or_none()
    if not user_set:
        user_set = UserSetting(
            user_id=payload.user_id,
            daily_limit=10,
            target_retention=0.9,
            assoc_preference="acoustic",
            subject_limits={"all": 10},
            is_experiment_participant=payload.is_participant,
            experiment_phase=payload.phase
        )
        db.add(user_set)
    else:
        user_set.is_experiment_participant = payload.is_participant
        user_set.experiment_phase = payload.phase

    await db.commit()
    return {
        "status": "success",
        "user_id": payload.user_id,
        "is_experiment_participant": payload.is_participant,
        "experiment_phase": payload.phase
    }


# --- 5. СИСТЕМА ИНВАЙТОВ И СПИСОК УЧАСТНИКОВ ---

class GenerateInviteIn(BaseModel):
    created_by: str = "admin"

@router.post("/invites/generate")
async def generate_invite_code(
    payload: GenerateInviteIn = None,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_token)
):
    """Генерация уникального инвайт-кода для участника эксперимента."""
    created_by = payload.created_by if payload else "admin"
    code = f"INV-{secrets.token_hex(4).upper()}"
    invite = InviteCode(code=code, created_by=created_by)
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "DATAGRINDERbot"
    bot_clean = str(bot_username).lstrip("@")
    invite_url = f"https://t.me/{bot_clean}?start=inv_{invite.code}"

    return {
        "status": "success",
        "code": invite.code,
        "invite_url": invite_url,
        "created_at": invite.created_at.isoformat() if invite.created_at else ""
    }

@router.get("/invites")
async def list_invites(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_token)
):
    """Список всех сгенерированных инвайтов с их статусом."""
    bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "DATAGRINDERbot"
    bot_clean = str(bot_username).lstrip("@")

    stmt = select(InviteCode).order_by(InviteCode.id.desc())
    res = await db.execute(stmt)
    invites = res.scalars().all()
    return {
        "status": "success",
        "total": len(invites),
        "invites": [
            {
                "id": inv.id,
                "code": inv.code,
                "invite_url": f"https://t.me/{bot_clean}?start=inv_{inv.code}",
                "created_by": inv.created_by,
                "is_used": inv.is_used,
                "used_by_user_id": inv.used_by_user_id,
                "used_by_username": inv.used_by_username,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
                "used_at": inv.used_at.isoformat() if inv.used_at else None
            }
            for inv in invites
        ]
    }

@router.get("/participants")
async def list_participants(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_token)
):
    """Детальный список участников эксперимента с Telegram @username и статистикой."""
    stmt = select(UserSession).filter(UserSession.is_experiment_participant == True).order_by(UserSession.id.asc())
    res = await db.execute(stmt)
    users = res.scalars().all()

    participants_data = []
    for u in users:
        # Считаем карточки и логи повторений
        c_stmt = select(text("count(*)")).select_from(Card).filter(Card.user_id == u.user_id)
        c_count = (await db.execute(c_stmt)).scalar() or 0

        r_stmt = select(text("count(*)")).select_from(ReviewLog).filter(ReviewLog.user_id == u.user_id)
        r_count = (await db.execute(r_stmt)).scalar() or 0

        participants_data.append({
            "telegram_id": u.telegram_id,
            "user_id": u.user_id,
            "username": u.username,
            "full_name": u.full_name,
            "experiment_phase": u.experiment_phase,
            "cards_count": c_count,
            "reviews_count": r_count
        })

    return {
        "status": "success",
        "total": len(participants_data),
        "participants": participants_data
    }


# --- 6. МАССОВАЯ РАЗДАЧА ЭТАЛОННОЙ КОЛОДЫ УЧАСТНИКАМ ---

class DistributeDeckIn(BaseModel):
    preset_name: str = "sudoustroystvo"
    subject_slug: str = "sudoustroystvo"
    phrase_title: str = "Судоустройство: Основной курс"
    cards: Optional[list] = None
    target_user_id: Optional[str] = None
    mode: str = "append"  # "append" (дозагрузить/обновить) или "overwrite" (полный сброс)
    overwrite_existing: Optional[bool] = None  # для обратной совместимости

@router.post("/experiment/distribute-deck")
async def distribute_deck(
    payload: DistributeDeckIn = None,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_token)
):
    """
    Массовая раздача эталонного пакета карточек участникам научного эксперимента.
    Загружает карточки из app/static/presets/{preset_name}.json (или переданного списка cards)
    и синхронизирует/дозагружает их участникам эксперимента.
    Режимы:
    - mode="append" (по умолчанию): сохраняет FSRS-прогресс студентов, обновляет формулировки и добавляет новые карточки.
    - mode="overwrite": полностью удаляет карточки по предмету и создает колоду с нуля.
    """
    if payload is None:
        payload = DistributeDeckIn()

    # Определение режима с поддержкой обратной совместимости
    effective_mode = payload.mode.lower() if payload.mode else "append"
    if payload.overwrite_existing is True and (not hasattr(payload, "model_fields_set") or "mode" not in payload.model_fields_set):
        effective_mode = "overwrite"
    elif payload.overwrite_existing is False and (not hasattr(payload, "model_fields_set") or "mode" not in payload.model_fields_set):
        effective_mode = "append"

    cards_to_distribute = payload.cards
    if not cards_to_distribute:
        preset_path = Path("app/static/presets") / f"{payload.preset_name}.json"
        if not preset_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Файл пресета '{preset_path}' не найден. Создайте его или передайте 'cards' в теле запроса."
            )
        try:
            content = json.loads(preset_path.read_text(encoding="utf-8"))
            cards_to_distribute = content.get("cards", [])
            if "phrase_title" in content:
                payload.phrase_title = content["phrase_title"]
            if "subject_slug" in content:
                payload.subject_slug = content["subject_slug"]
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Ошибка чтения файла пресета: {e}"
            )

    if not cards_to_distribute:
        raise HTTPException(status_code=400, detail="Набор карточек пуст.")

    # Выбираем участников эксперимента
    if payload.target_user_id:
        stmt = select(UserSession).filter(UserSession.user_id == payload.target_user_id)
    else:
        stmt = select(UserSession).filter(UserSession.is_experiment_participant == True)
    
    users = (await db.execute(stmt)).scalars().all()
    if not users:
        return {
            "status": "warning",
            "message": "Нет зарегистрированных участников для раздачи.",
            "cards_count": len(cards_to_distribute),
            "users_affected": 0,
            "mode": effective_mode
        }

    users_affected = 0
    total_cards_created = 0
    total_cards_updated = 0

    for u in users:
        if effective_mode == "overwrite":
            await db.execute(delete(Card).filter(Card.user_id == u.user_id, Card.subject == payload.subject_slug))
            await db.execute(delete(Phrase).filter(Phrase.user_id == u.user_id, Phrase.subject == payload.subject_slug))
            await db.commit()

            created, _, _ = await save_cards_to_database(
                cards_data=cards_to_distribute,
                subject_slug=payload.subject_slug,
                phrase_title=payload.phrase_title,
                user_id=u.user_id,
                db=db
            )
            total_cards_created += created
        else:
            created, updated, _, _ = await append_or_sync_cards_to_database(
                cards_data=cards_to_distribute,
                subject_slug=payload.subject_slug,
                phrase_title=payload.phrase_title,
                user_id=u.user_id,
                db=db
            )
            total_cards_created += created
            total_cards_updated += updated
        users_affected += 1

    await db.commit()

    msg = (
        f"Колода «{payload.phrase_title}» успешно дозагружена для {users_affected} участников (новых: {total_cards_created}, обновлено: {total_cards_updated})."
        if effective_mode == "append"
        else f"Колода «{payload.phrase_title}» полностью перезаписана для {users_affected} участников ({total_cards_created} карт создано с нуля)."
    )

    return {
        "status": "success",
        "mode": effective_mode,
        "message": msg,
        "subject": payload.subject_slug,
        "cards_in_deck": len(cards_to_distribute),
        "users_affected": users_affected,
        "total_cards_created": total_cards_created,
        "total_cards_updated": total_cards_updated
    }


# --- 7. ПЕРЕКЛЮЧЕНИЕ ИИ-ПРОВАЙДЕРА (DEEPSEEK <-> XIAOMI MIMO) ---

class SwitchAiProviderIn(BaseModel):
    provider: str  # "deepseek" или "mimo"
    model: Optional[str] = None

def set_active_ai_provider(provider: str, model: Optional[str] = None) -> str:
    """Устанавливает активного ИИ-провайдера (и опционально модель) и сохраняет выбор в .env для персистентности."""
    import os
    import re
    norm = provider.strip().lower()
    if norm not in ("deepseek", "mimo"):
        raise ValueError(f"Недопустимый ИИ-провайдер: '{provider}'. Поддерживаются: 'deepseek', 'mimo'")
    
    settings.AI_PROVIDER = norm
    os.environ["AI_PROVIDER"] = norm

    clean_model = model.strip() if model and model.strip() else None
    if clean_model:
        if norm == "mimo":
            settings.MIMO_MODEL = clean_model
            os.environ["MIMO_MODEL"] = clean_model
        else:
            settings.DEEPSEEK_MODEL = clean_model
            os.environ["DEEPSEEK_MODEL"] = clean_model

    env_path = Path(".env")
    try:
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
            if re.search(r"^AI_PROVIDER=.*", content, flags=re.MULTILINE):
                new_content = re.sub(r"^AI_PROVIDER=.*", f"AI_PROVIDER={norm}", content, flags=re.MULTILINE)
            else:
                new_content = content.rstrip() + f"\nAI_PROVIDER={norm}\n"
            
            if clean_model:
                model_var = "MIMO_MODEL" if norm == "mimo" else "DEEPSEEK_MODEL"
                if re.search(rf"^{model_var}=.*", new_content, flags=re.MULTILINE):
                    new_content = re.sub(rf"^{model_var}=.*", f"{model_var}={clean_model}", new_content, flags=re.MULTILINE)
                else:
                    new_content = new_content.rstrip() + f"\n{model_var}={clean_model}\n"

            env_path.write_text(new_content, encoding="utf-8")
        else:
            txt = f"AI_PROVIDER={norm}\n"
            if clean_model:
                model_var = "MIMO_MODEL" if norm == "mimo" else "DEEPSEEK_MODEL"
                txt += f"{model_var}={clean_model}\n"
            env_path.write_text(txt, encoding="utf-8")
        print(f"[Admin] AI_PROVIDER успешно обновлен на '{norm}' (модель: {clean_model or 'default'}) в .env")
    except Exception as env_err:
        print(f"[Admin WARN] Ошибка записи настроек ИИ в .env: {env_err}")
    return norm

@router.get("/ai-provider")
async def get_ai_provider_status(
    token: str = Depends(verify_admin_token)
):
    """Возвращает статус текущего активного ИИ-провайдера и конфигурацию моделей."""
    current_provider = getattr(settings, "AI_PROVIDER", "deepseek").lower()
    current_model = settings.MIMO_MODEL if current_provider == "mimo" else settings.DEEPSEEK_MODEL
    active_has_key = bool(settings.MIMO_API_KEY) if current_provider == "mimo" else bool(settings.DEEPSEEK_API_KEY)

    def mask_key(k: str) -> str:
        if not k:
            return ""
        if len(k) <= 8:
            return "***"
        return f"{k[:4]}...{k[-4:]}"

    return {
        "status": "success",
        "provider": current_provider,
        "model": current_model,
        "active_has_key": active_has_key,
        "available_providers": ["deepseek", "mimo"],
        "deepseek": {
            "model": settings.DEEPSEEK_MODEL,
            "base_url": settings.DEEPSEEK_BASE_URL,
            "has_key": bool(settings.DEEPSEEK_API_KEY),
            "key_masked": mask_key(settings.DEEPSEEK_API_KEY)
        },
        "mimo": {
            "model": settings.MIMO_MODEL,
            "base_url": settings.MIMO_BASE_URL,
            "has_key": bool(settings.MIMO_API_KEY),
            "key_masked": mask_key(settings.MIMO_API_KEY)
        }
    }

@router.post("/switch-ai-provider")
async def switch_ai_provider(
    payload: SwitchAiProviderIn,
    token: str = Depends(verify_admin_token)
):
    """
    Переключает активного ИИ-провайдера системы между DeepSeek и Xiaomi MiMo.
    Обновляет глобальные настройки приложения в памяти и файл .env.
    """
    try:
        active = set_active_ai_provider(payload.provider, payload.model)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    active_model = settings.MIMO_MODEL if active == "mimo" else settings.DEEPSEEK_MODEL
    provider_name = "Xiaomi MiMo" if active == "mimo" else "DeepSeek"
    has_key = bool(settings.MIMO_API_KEY) if active == "mimo" else bool(settings.DEEPSEEK_API_KEY)
    
    warning = None
    if not has_key:
        warning = f"Ключ {active.upper()}_API_KEY еще не настроен в файле .env"
        msg = f"ИИ переключен на {provider_name} ({active_model}). ⚠️ Внимание: {warning}!"
    else:
        msg = f"ИИ-провайдер успешно переключен на {provider_name} ({active_model})."

    return {
        "status": "success",
        "provider": active,
        "model": active_model,
        "has_key": has_key,
        "warning": warning,
        "message": msg
    }



