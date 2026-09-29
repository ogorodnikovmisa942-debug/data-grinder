# app/services/generation_worker.py
import re
import time
import html
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select, update, text
from app.database.session import AsyncSessionLocal
from app.database.models import GenerationJob, utc_now
from app.services.ai_gateway.client import is_deepseek_offpeak_now, record_ai_telemetry
from app.services.ai_gateway.path_builder import build_learning_path
from app.services.knowledge_path import save_learning_path, normalize_subject
from app.core.config import settings
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.session.aiohttp import AiohttpSession

def is_deepseek_offpeak() -> bool:
    """Активно ли окно скидки DeepSeek 50% (пик: пн–пт 01:00–04:00 и 06:00–10:00 UTC, остальное — скидка)."""
    now_utc = None
    # Поддержка мокирования datetime.utcnow в тестах
    if hasattr(datetime, "utcnow") and hasattr(datetime.utcnow, "return_value"):
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    return is_deepseek_offpeak_now(now_utc)

async def send_worker_telegram_push(chat_id: int | str, text_msg: str, reply_markup=None, parse_mode: str = "HTML"):
    """Отправляет push-уведомление в Telegram пользователю."""
    token = settings.TELEGRAM_BOT_TOKEN
    if not token or token == "placeholder_bot_token":
        return
    session = None
    try:
        session = AiohttpSession(timeout=15)
        bot = Bot(token=token, session=session)
        await bot.send_message(
            chat_id=int(chat_id),
            text=text_msg,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
    except Exception as e:
        print(f"[Worker Notifier] Не удалось доставить Telegram push ({chat_id}): {e}")
    finally:
        if session:
            await session.close()

async def _record_path_calls(job_id: int, user_id: str, calls: list[dict]) -> None:
    """Пишет каждый вызов LLM в ai_telemetry_logs со стоимостью (в т.ч. неудачные, но оплаченные)."""
    for c in calls:
        await record_ai_telemetry(
            job_id=str(job_id),
            user_id=user_id,
            model_requested=settings.DEEPSEEK_MODEL or "deepseek-flash",
            model_resolved=c.get("model") or "none",
            input_chars=0,
            prompt_tokens=c.get("prompt_tokens", 0),
            completion_tokens=c.get("completion_tokens", 0),
            cache_hit=c.get("cache_hit_tokens", 0) > 0,
            cache_hit_tokens=c.get("cache_hit_tokens", 0),
            cost_usd=c.get("cost_usd", 0.0),
            is_truncated=c.get("finish_reason") == "length",
            duration_ms=c.get("duration_ms", 0),
            status="failed" if c.get("error") else "success",
            error_message=c.get("error"),
        )


async def process_generation_job(job_id: int, is_offpeak: bool):
    """Строит «Путь знаний» по материалу задачи: карта → уроки и карточки → сохранение в БД."""
    job_start_time = time.time()

    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(GenerationJob).filter(GenerationJob.id == job_id))).scalar_one_or_none()
        if not job:
            return
        job_data = {
            "id": job.id,
            "user_id": job.user_id,
            "telegram_id": job.telegram_id,
            "subject": normalize_subject(job.subject),
            "theme": job.theme,
            "raw_text": job.raw_text,
        }

    char_count = len(job_data["raw_text"] or "")
    tariff_label = "со скидкой 50%" if is_offpeak else "по пиковому тарифу"
    print(f"[Generation Worker] Задача #{job_id} («{job_data['theme']}», {char_count} зн.) {tariff_label}...", flush=True)

    calls: list[dict] = []
    try:
        clean_text = re.sub(r'=== [^=]+ ===', '', job_data["raw_text"] or "")
        clean_text = re.sub(r'--- [^\n]+ ---', '', clean_text).strip()
        if len(clean_text) < 15:
            raise ValueError("Распознанный текст слишком короткий или пуст (менее 15 знаков). Похоже, в документе нет текста.")

        if job_data.get("telegram_id"):
            await send_worker_telegram_push(
                job_data["telegram_id"],
                f"Материал «<b>{html.escape(str(job_data['theme']))}</b>» взят в обработку.\n"
                "ИИ строит карту предмета, уроки и карточки. Это займёт несколько минут.",
            )

        try:
            result = await build_learning_path(job_data["raw_text"], job_data["subject"], calls=calls)
        finally:
            await _record_path_calls(job_id, job_data["user_id"], calls)

        async with AsyncSessionLocal() as db:
            j = (await db.execute(select(GenerationJob).filter(GenerationJob.id == job_id))).scalar_one_or_none()
            if not j or j.status == "cancelled":
                print(f"[Generation Worker] Задача #{job_id} отменена пользователем. Результат не сохраняется.", flush=True)
                return
            stats = await save_learning_path(db, job_data["user_id"], job_data["subject"], result)
            j.theme = stats["title"]
            j.cards_count = stats["cards"]
            j.status = "completed"
            j.processed_at = utc_now()
            j.char_count = char_count
            j.execution_time_ms = int((time.time() - job_start_time) * 1000)
            j.error_message = None
            j.error_trace = None
            await db.commit()

        cost = sum(c.get("cost_usd", 0.0) for c in calls)
        print(
            f"[Generation Worker] Задача #{job_id} готова за {(time.time() - job_start_time):.0f} с: "
            f"{stats['nodes']} узлов, {stats['cards']} карточек, ${cost:.4f}.", flush=True
        )

        if job_data.get("telegram_id"):
            webapp_url = getattr(settings, "WEBAPP_URL", "https://datagrinder.site")
            markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text="[ОТКРЫТЬ ПУТЬ ЗНАНИЙ]",
                web_app=WebAppInfo(url=f"{webapp_url}#path_{job_data['subject']}"),
            )]])
            await send_worker_telegram_push(
                job_data["telegram_id"],
                f"Путь знаний «<b>{html.escape(stats['title'])}</b>» готов: "
                f"{stats['nodes']} тем, {stats['cards']} карточек.\nНачни с основ.",
                reply_markup=markup,
            )

    except Exception as proc_err:
        import traceback
        trace_err = traceback.format_exc()
        execution_time_ms = int((time.time() - job_start_time) * 1000)
        print(f"[Generation Worker ERROR] Сбой задачи #{job_id} ({execution_time_ms} мс): {proc_err}\n{trace_err}", flush=True)
        async with AsyncSessionLocal() as db:
            j = (await db.execute(select(GenerationJob).filter(GenerationJob.id == job_id))).scalar_one_or_none()
            if j:
                j.status = "failed"
                j.error_message = str(proc_err)[:500]
                j.error_trace = trace_err
                j.char_count = char_count
                j.execution_time_ms = execution_time_ms
                j.processed_at = utc_now()
                await db.commit()
        if job_data.get("telegram_id"):
            await send_worker_telegram_push(
                job_data["telegram_id"],
                f"Не удалось обработать «{html.escape(str(job_data['theme']))}»: {html.escape(str(proc_err)[:200])}",
            )

async def reset_stalled_jobs():
    """Сбрасывает зависшие задачи 'processing' обратно в 'pending' при старте сервера."""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(GenerationJob)
                .filter(GenerationJob.status == "processing")
                .values(status="pending")
            )
            await db.commit()
    except Exception as e:
        print(f"[Generation Worker] Замечание при сбросе очереди: {e}")

async def claim_next_pending_job(is_offpeak: bool) -> int | None:
    """Атомарно захватывает следующую подходящую задачу из БД в статус 'processing'."""
    try:
        async with AsyncSessionLocal() as db:
            # Атомарный UPDATE ... RETURNING id исключает гонки нескольких воркеров
            # В непиковое время (скидка 50%) берем любую задачу, в дневное — только is_deferred == False
            if is_offpeak:
                sub_condition = "status = 'pending'"
            else:
                sub_condition = "status = 'pending' AND (is_deferred = 0 OR is_deferred IS NULL)"

            claim_query = text(f"""
                UPDATE generation_jobs
                SET status = 'processing'
                WHERE id = (
                    SELECT id FROM generation_jobs
                    WHERE {sub_condition}
                    ORDER BY id ASC
                    LIMIT 1
                )
                RETURNING id
            """)
            res = await db.execute(claim_query)
            row = res.fetchone()
            await db.commit()
            if row:
                return row[0]
            return None
    except Exception as e:
        # Фолбэк для СУБД без поддержки RETURNING
        try:
            async with AsyncSessionLocal() as db:
                stmt = select(GenerationJob).filter(GenerationJob.status == "pending")
                if not is_offpeak:
                    stmt = stmt.filter(GenerationJob.is_deferred == False)
                stmt = stmt.order_by(GenerationJob.id.asc()).limit(1)
                job = (await db.execute(stmt)).scalar_one_or_none()
                if job:
                    job.status = "processing"
                    await db.commit()
                    return job.id
                return None
        except Exception as fallback_err:
            print(f"[Generation Worker] Ошибка захвата задачи: {fallback_err}")
            return None

async def generation_worker_loop():
    """Главный фоновый цикл обработки задач очереди ночного и фонового грайнда."""
    print("[Generation Worker] Фоновый воркер генерации запущен (скидка DeepSeek: всё время, кроме пн–пт 04:00–07:00 и 09:00–13:00 МСК).")
    await reset_stalled_jobs()

    while True:
        try:
            is_offpeak = is_deepseek_offpeak()
            claimed_job_id = await claim_next_pending_job(is_offpeak)
            if claimed_job_id is not None:
                await process_generation_job(claimed_job_id, is_offpeak)
                continue
        except Exception as loop_err:
            print(f"[Generation Worker] Ошибка в рабочем цикле: {loop_err}")

        await asyncio.sleep(3)  # Быстрая проверка каждые 3 секунды для мгновенной реакции
