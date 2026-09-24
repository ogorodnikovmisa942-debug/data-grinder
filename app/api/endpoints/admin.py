# app/api/endpoints/admin.py
import csv
import io
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text, select, update, delete, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
import json
import secrets
from pathlib import Path
from typing import Optional
from app.core.config import settings
from app.database.session import get_db
from app.database.models import UserSession, UserSetting, AiTelemetryLog, InviteCode, Card, ReviewLog, Phrase, utc_now
from app.api.endpoints.management import save_cards_to_database, append_or_sync_cards_to_database

router = APIRouter()

class SwitchPhaseIn(BaseModel):
    phase: int = 2

class SetParticipantIn(BaseModel):
    user_id: str
    is_participant: bool = True
    phase: int = 1

def verify_admin_token(request: Request):
    """Проверка административного токена из заголовка X-Admin-Token или параметра ?token=."""
    token = request.headers.get("x-admin-token") or request.headers.get("X-Admin-Token")
    if not token:
        token = request.query_params.get("token") or request.query_params.get("admin_token")
    if not token or token != settings.ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещен: недействительный или отсутствующий X-Admin-Token"
        )
    return token

# --- 5.1. ВЫГРУЗКА ДАТАСЕТА ЭКСПЕРИМЕНТА В CSV ДЛЯ PANDAS / R ---
@router.get("/export/experiment-dataset")
async def export_experiment_dataset(
    all_users: bool = Query(False, description="Выгрузить данные всех пользователей, а не только участников эксперимента"),
    token: str = Depends(verify_admin_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Формирует и отдает потоковый CSV-датасет участников научного эксперимента (Фаза 1) или всех пользователей.
    Кодировка: UTF-8 с BOM (\ufeff) для безупречной загрузки в pandas.read_csv() и Excel без битых символов.
    """
    where_clause = "" if all_users else "WHERE u.is_experiment_participant = 1"
    sql_query = text(f"""
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
        LEFT JOIN cards c ON r.card_id = c.id
        LEFT JOIN daily_sessions d ON r.user_id = d.user_id 
             AND DATE(r.timestamp) = DATE(d.timestamp)
        {where_clause}
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

# --- 5.3.1. ЗАВЕРШЕНИЕ ЭКСПЕРИМЕНТА ДЛЯ ВСЕХ УЧАСТНИКОВ ---
@router.post("/experiment/finish")
async def finish_experiment(
    token: str = Depends(verify_admin_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Завершает научный эксперимент:
    - Массово переводит всех участников в обычный свободный режим (is_experiment_participant = False).
    - Снимает все блокировки колоды и нарезки материалов.
    """
    stmt_sessions = (
        update(UserSession)
        .where(UserSession.is_experiment_participant == True)
        .values(is_experiment_participant=False)
    )
    res_sessions = await db.execute(stmt_sessions)
    updated_sessions = res_sessions.rowcount

    stmt_settings = (
        update(UserSetting)
        .where(UserSetting.is_experiment_participant == True)
        .values(is_experiment_participant=False)
    )
    await db.execute(stmt_settings)
    await db.commit()

    return {
        "status": "success",
        "released_users": updated_sessions,
        "message": f"Эксперимент успешно завершен. {updated_sessions} участников переведены в обычный режим со свободным доступом."
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
        c_stmt = select(func.count(Card.id)).filter(Card.user_id == u.user_id)
        c_count = (await db.execute(c_stmt)).scalar() or 0

        r_stmt = select(func.count(ReviewLog.id)).filter(ReviewLog.user_id == u.user_id)
        r_count = (await db.execute(r_stmt)).scalar() or 0

        participants_data.append({
            "telegram_id": u.telegram_id,
            "user_id": u.user_id,
            "username": u.username,
            "full_name": u.full_name,
            "is_experiment_participant": True,
            "experiment_phase": u.experiment_phase,
            "cards_count": c_count,
            "reviews_count": r_count
        })

    return {
        "status": "success",
        "total": len(participants_data),
        "participants": participants_data
    }


@router.get("/users")
async def list_all_users(
    only_participants: bool = Query(False, description="Показывать только участников эксперимента"),
    search: Optional[str] = Query(None, description="Поиск по username, ID или имени"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    token: str = Depends(verify_admin_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Детальный список ВСЕХ пользователей системы (а не только по инвайтам)
    со статистикой карточек, логов повторений и статусом участия в исследовании.
    """
    query = select(UserSession)
    if only_participants:
        query = query.filter(UserSession.is_experiment_participant == True)
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(
            or_(
                UserSession.username.ilike(s),
                UserSession.full_name.ilike(s),
                UserSession.telegram_id.ilike(s),
                UserSession.user_id.ilike(s)
            )
        )
    query = query.order_by(UserSession.id.desc()).offset(offset).limit(limit)

    users = (await db.execute(query)).scalars().all()

    total_all = (await db.execute(select(func.count(UserSession.id)))).scalar() or 0
    total_part = (await db.execute(select(func.count(UserSession.id)).filter(UserSession.is_experiment_participant == True))).scalar() or 0

    users_data = []
    for u in users:
        c_count = (await db.execute(select(func.count(Card.id)).filter(Card.user_id == u.user_id))).scalar() or 0
        r_count = (await db.execute(select(func.count(ReviewLog.id)).filter(ReviewLog.user_id == u.user_id))).scalar() or 0
        users_data.append({
            "id": u.id,
            "telegram_id": u.telegram_id,
            "user_id": u.user_id,
            "username": u.username,
            "full_name": u.full_name,
            "is_experiment_participant": bool(u.is_experiment_participant),
            "experiment_phase": u.experiment_phase,
            "is_resting": bool(u.is_resting),
            "cards_count": c_count,
            "reviews_count": r_count
        })

    return {
        "status": "success",
        "total_all": total_all,
        "total_participants": total_part,
        "count": len(users_data),
        "offset": offset,
        "limit": limit,
        "users": users_data
    }


@router.get("/export/users")
async def export_users_csv(
    only_participants: bool = Query(False),
    token: str = Depends(verify_admin_token),
    db: AsyncSession = Depends(get_db)
):
    """Выгружает список пользователей системы в CSV (UTF-8 BOM для Excel/Pandas)."""
    query = select(UserSession)
    if only_participants:
        query = query.filter(UserSession.is_experiment_participant == True)
    query = query.order_by(UserSession.id.asc())

    users = (await db.execute(query)).scalars().all()

    fields = [
        "id", "telegram_id", "user_id", "username", "full_name",
        "is_experiment_participant", "experiment_phase", "cards_count",
        "reviews_count", "is_resting"
    ]

    async def csv_stream():
        output = io.StringIO()
        output.write("\ufeff")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(fields)
        yield output.getvalue().encode("utf-8")
        output.seek(0)
        output.truncate(0)

        for u in users:
            c_count = (await db.execute(select(func.count(Card.id)).filter(Card.user_id == u.user_id))).scalar() or 0
            r_count = (await db.execute(select(func.count(ReviewLog.id)).filter(ReviewLog.user_id == u.user_id))).scalar() or 0
            writer.writerow([
                u.id,
                u.telegram_id,
                u.user_id,
                u.username or "",
                u.full_name or "",
                1 if u.is_experiment_participant else 0,
                u.experiment_phase,
                c_count,
                r_count,
                1 if u.is_resting else 0
            ])
            yield output.getvalue().encode("utf-8")
            output.seek(0)
            output.truncate(0)

    filename = "grinder_participants.csv" if only_participants else "grinder_all_users.csv"
    return StreamingResponse(
        csv_stream(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache"
        }
    )


# --- 6. МАССОВАЯ РАЗДАЧА ЭТАЛОННОЙ КОЛОДЫ УЧАСТНИКАМ ---

class DistributeDeckIn(BaseModel):
    preset_name: Optional[str] = None
    subject_slug: str
    phrase_title: str
    cards: Optional[list] = None
    target_user_id: Optional[str] = None
    distribute_to_all: Optional[bool] = False
    mode: str = "append"  # "append" (дозагрузить/обновить) или "overwrite" (полный сброс)
    overwrite_existing: Optional[bool] = None  # для обратной совместимости

@router.post("/experiment/distribute-deck")
async def distribute_deck(
    payload: DistributeDeckIn,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_token)
):
    """
    Массовая раздача эталонного пакета карточек участникам научного эксперимента (или всем пользователям).
    Загружает карточки из app/static/presets/{preset_name}.json (или переданного списка cards)
    и синхронизирует/дозагружает их пользователям.
    Режимы:
    - mode="append" (по умолчанию): сохраняет FSRS-прогресс студентов, обновляет формулировки и добавляет новые карточки.
    - mode="overwrite": полностью удаляет карточки по предмету и создает колоду с нуля.
    """
    # Определение режима с поддержкой обратной совместимости
    effective_mode = payload.mode.lower() if payload.mode else "append"
    if payload.overwrite_existing is True and (not hasattr(payload, "model_fields_set") or "mode" not in payload.model_fields_set):
        effective_mode = "overwrite"
    elif payload.overwrite_existing is False and (not hasattr(payload, "model_fields_set") or "mode" not in payload.model_fields_set):
        effective_mode = "append"

    cards_to_distribute = payload.cards
    if not cards_to_distribute:
        if not payload.preset_name:
            raise HTTPException(
                status_code=400,
                detail="Необходимо передать либо список 'cards', либо 'preset_name'."
            )
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

    # Сохраняем эталонную колоду на диск в app/static/presets
    if payload.cards and payload.subject_slug:
        try:
            save_preset_path = Path("app/static/presets") / f"{payload.subject_slug}.json"
            save_preset_path.parent.mkdir(parents=True, exist_ok=True)
            save_preset_path.write_text(json.dumps({
                "subject_slug": payload.subject_slug,
                "phrase_title": payload.phrase_title,
                "exported_at": utc_now().isoformat(),
                "total_cards": len(cards_to_distribute),
                "cards": cards_to_distribute
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # Выбираем целевых пользователей (конкретного, всех или только участников)
    if payload.target_user_id:
        stmt = select(UserSession).filter(UserSession.user_id == payload.target_user_id)
    elif payload.distribute_to_all:
        stmt = select(UserSession)
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


# --- 7. УПРАВЛЕНИЕ ИИ-МОДЕЛЯМИ DEEPSEEK ---

class SwitchAiProviderIn(BaseModel):
    provider: str = "deepseek"
    model: Optional[str] = None

def set_active_ai_provider(provider: str, model: Optional[str] = None) -> str:
    """Устанавливает активную модель DeepSeek и сохраняет выбор в .env для персистентности."""
    import os
    import re
    norm = provider.strip().lower()
    if norm not in ("deepseek",):
        raise ValueError(f"Недопустимый ИИ-провайдер: '{provider}'. Поддерживается: 'deepseek'")
    
    settings.AI_PROVIDER = norm
    os.environ["AI_PROVIDER"] = norm

    clean_model = model.strip() if model and model.strip() else None
    if clean_model:
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
                if re.search(r"^DEEPSEEK_MODEL=.*", new_content, flags=re.MULTILINE):
                    new_content = re.sub(r"^DEEPSEEK_MODEL=.*", f"DEEPSEEK_MODEL={clean_model}", new_content, flags=re.MULTILINE)
                else:
                    new_content = new_content.rstrip() + f"\nDEEPSEEK_MODEL={clean_model}\n"

            env_path.write_text(new_content, encoding="utf-8")
        else:
            txt = f"AI_PROVIDER={norm}\n"
            if clean_model:
                txt += f"DEEPSEEK_MODEL={clean_model}\n"
            env_path.write_text(txt, encoding="utf-8")
        print(f"[Admin] ИИ успешно обновлен на '{norm}' (модель: {clean_model or settings.DEEPSEEK_MODEL}) в .env")
    except Exception as env_err:
        print(f"[Admin WARN] Ошибка записи настроек ИИ в .env: {env_err}")
    return norm

@router.get("/ai-provider")
async def get_ai_provider_status(
    token: str = Depends(verify_admin_token)
):
    """Возвращает статус текущей конфигурации DeepSeek."""
    current_model = settings.DEEPSEEK_MODEL
    active_has_key = bool(settings.DEEPSEEK_API_KEY)

    def mask_key(k: str) -> str:
        if not k:
            return ""
        if len(k) <= 8:
            return "***"
        return f"{k[:4]}...{k[-4:]}"

    return {
        "status": "success",
        "provider": "deepseek",
        "model": current_model,
        "active_has_key": active_has_key,
        "available_providers": ["deepseek"],
        "available_models": ["deepseek-flash", "deepseek-chat", "deepseek-reasoner"],
        "deepseek": {
            "model": settings.DEEPSEEK_MODEL,
            "base_url": settings.DEEPSEEK_BASE_URL,
            "has_key": active_has_key,
            "key_masked": mask_key(settings.DEEPSEEK_API_KEY)
        }
    }

@router.post("/switch-ai-provider")
async def switch_ai_provider(
    payload: SwitchAiProviderIn,
    token: str = Depends(verify_admin_token)
):
    """
    Переключает конфигурацию модели DeepSeek (deepseek-flash, deepseek-chat, deepseek-reasoner).
    Обновляет глобальные настройки приложения в памяти и файл .env.
    """
    try:
        active = set_active_ai_provider(payload.provider or "deepseek", payload.model)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    active_model = settings.DEEPSEEK_MODEL
    has_key = bool(settings.DEEPSEEK_API_KEY)
    
    warning = None
    if not has_key:
        warning = "Ключ DEEPSEEK_API_KEY еще не настроен в файле .env"
        msg = f"ИИ настроен на DeepSeek ({active_model}). ⚠️ Внимание: {warning}!"
    else:
        msg = f"ИИ успешно переключен на DeepSeek ({active_model})."

    return {
        "status": "success",
        "provider": active,
        "model": active_model,
        "has_key": has_key,
        "warning": warning,
        "message": msg
    }



