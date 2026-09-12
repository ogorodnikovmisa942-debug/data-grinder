# app/services/generation_worker.py
import os
import re
import json
import time
import html
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select, update, text
from app.database.session import AsyncSessionLocal
from app.database.models import GenerationJob, TopicKnowledgeGraph
from app.services.ai_gateway import parse_raw_text, split_text_into_chunks
from app.services.graph_service import consolidate_knowledge_graphs
from app.core.config import settings
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.session.aiohttp import AiohttpSession

def is_deepseek_offpeak() -> bool:
    """Проверяет, активно ли внепиковое окно со скидкой 50% у DeepSeek (16:30-00:30 UTC / 19:30-03:30 МСК).
    Синхронизировано строго по UTC (всемирному координированному времени):
    - 16:30-00:30 UTC строго соответствует 19:30-03:30 МСК (UTC+3).
    - Для сервера в Нидерландах (CET/CEST): это 17:30-01:30 (зима UTC+1) или 18:30-02:30 (лето UTC+2).
    Использование UTC гарантирует 100% совпадение с биллинговым окном DeepSeek независимо от расположения сервера.
    """
    try:
        now_utc = datetime.now(timezone.utc)
        if isinstance(now_utc.hour, int):
            minutes = now_utc.hour * 60 + now_utc.minute
            return minutes >= 990 or minutes < 30
    except Exception:
        pass
    now_utc = datetime.utcnow()
    minutes = now_utc.hour * 60 + now_utc.minute
    # 16:30 UTC = 990 мин, 00:30 UTC = 30 мин
    return minutes >= 990 or minutes < 30

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

def normalize_front(txt: str) -> str:
    """Очищает front текст от разметки cloze и знаков препинания для дедупликации."""
    t = re.sub(r'\{\{c\d+::(.*?)(?:::.*?)?\}\}', r'\1', txt)
    return re.sub(r'[^a-zA-Zа-яА-Я0-9]', '', t.lower())

async def process_generation_job(job_id: int, is_offpeak: bool):
    """Выполняет полный цикл нарезки карточек для задачи с id = job_id."""
    job_start_time = time.time()
    
    # 1. Загружаем данные задачи
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(GenerationJob).filter(GenerationJob.id == job_id))
        job = res.scalar_one_or_none()
        if not job:
            return
        job_data = {
            "id": job.id,
            "user_id": job.user_id,
            "telegram_id": job.telegram_id,
            "subject": job.subject,
            "theme": job.theme,
            "raw_text": job.raw_text,
            "granularity_mode": job.granularity_mode,
            "density": job.density,
            "volume": job.volume,
            "custom_instruction": job.custom_instruction,
            "is_deferred": getattr(job, "is_deferred", False)
        }

    char_count = len(job_data.get("raw_text", ""))
    tariff_label = "ночному тарифу (-50% стоимости)" if is_offpeak else "дневному фоновому тарифу"
    print(f"[Generation Worker] Старт обработки задачи #{job_data['id']} («{job_data['theme']}», {char_count} зн.) по {tariff_label}...", flush=True)

    try:
        clean_text_no_headers = re.sub(r'=== [^=]+ ===', '', job_data["raw_text"]).strip()
        clean_text_no_headers = re.sub(r'--- [^\n]+ ---', '', clean_text_no_headers).strip()
        if len(clean_text_no_headers) < 15:
            raise ValueError("Распознанный текст слишком короткий или пуст (менее 15 знаков). Похоже, в документе нет текста.")

        # Умное разбиение на сбалансированные смысловые блоки (~12-14 страниц / 20 000 - 24 000 знаков)
        chunks = split_text_into_chunks(job_data["raw_text"], max_chunk_chars=24000, overlap_chars=1200)
        total_chunks = len(chunks)
        print(f"[Generation Worker] Задача #{job_data['id']}: материал разбит на {total_chunks} частей для 100% охвата.", flush=True)

        # Отправляем мгновенное уведомление в Telegram о старте генерации
        if job_data.get("telegram_id"):
            try:
                escaped_theme = html.escape(str(job_data['theme']))
                discount_badge = "🔥 <b>Скидка 50% активна!</b>\n" if is_offpeak else "☀️ <b>Дневная обработка</b>\n"
                start_msg = (
                    f"{discount_badge}"
                    f"🚀 <b>Документ взят в обработку ИИ!</b>\n\n"
                    f"Материал: «<b>{escaped_theme}</b>»\n"
                    f"Объем: <b>{char_count:,} знаков</b> (~{total_chunks} смысловых блоков)\n"
                    f"Тариф: <i>{tariff_label}</i>\n\n"
                    f"⏳ <i>ИИ нарезает карточки. По готовности пришлю кнопку для разбора в Песочнице!</i>"
                )
                await send_worker_telegram_push(job_data["telegram_id"], start_msg)
            except Exception as start_push_err:
                print(f"[Generation Worker] Ошибка отправки стартового push: {start_push_err}")

        all_collected_cards = []
        all_chunk_graphs = []
        seen_card_texts = set()
        extracted_theme = job_data["theme"]
        any_fallback_used = False
        any_json_repair_applied = False

        # Конкурентная нарезка чанков с семафором (2 параллельных запроса для ускорения без исчерпания RPM)
        semaphore = asyncio.Semaphore(2)

        async def process_chunk(chunk_idx: int, chunk_text: str):
            async with semaphore:
                print(f"[Generation Worker] Задача #{job_data['id']}: нарезка блока {chunk_idx}/{total_chunks} ({len(chunk_text)} знаков)...", flush=True)
                try:
                    parsed = await parse_raw_text(
                        text=chunk_text,
                        target_subject=job_data["subject"],
                        density=job_data["density"],
                        volume=job_data["volume"],
                        granularity_mode=job_data["granularity_mode"],
                        custom_instruction=job_data["custom_instruction"],
                        user_id=job_data.get("user_id", "default_user"),
                        job_id=str(job_data["id"])
                    )
                    return chunk_idx, parsed
                except Exception as chunk_err:
                    print(f"[Generation Worker WARN] Ошибка в блоке {chunk_idx}/{total_chunks}: {chunk_err}", flush=True)
                    return chunk_idx, None

        chunk_tasks = [process_chunk(idx, ch) for idx, ch in enumerate(chunks, 1)]
        results = await asyncio.gather(*chunk_tasks)

        # Сортируем результаты по исходному порядку чанков
        results.sort(key=lambda x: x[0])

        for chunk_idx, parsed in results:
            if not parsed or not isinstance(parsed, dict):
                continue
            if parsed.get("fallback_used"):
                any_fallback_used = True
            if parsed.get("json_repair_applied"):
                any_json_repair_applied = True
            if parsed.get("phrase_title") and extracted_theme in ("Новый блок знаний", "Материал", ""):
                extracted_theme = parsed["phrase_title"]
            chunk_cards = parsed.get("cards", [])
            if isinstance(chunk_cards, list):
                for c in chunk_cards:
                    raw_c_text = (c.get("text") or "").strip()
                    norm_key = normalize_front(raw_c_text)
                    if norm_key and norm_key not in seen_card_texts:
                        seen_card_texts.add(norm_key)
                        all_collected_cards.append(c)

            chunk_graph = parsed.get("knowledge_graph")
            if chunk_graph and isinstance(chunk_graph, dict):
                all_chunk_graphs.append(chunk_graph)

        if not all_collected_cards:
            raise ValueError("ИИ не смог выделить карточки из переданного материала.")

        execution_time_ms = int((time.time() - job_start_time) * 1000)
        decomp_rate = len(all_collected_cards) / (char_count / 1000.0) if char_count > 0 else 0.0

        async with AsyncSessionLocal() as db:
            stmt = select(GenerationJob).filter(GenerationJob.id == job_data["id"])
            j = (await db.execute(stmt)).scalar_one_or_none()
            if not j or j.status == "cancelled":
                print(f"[Generation Worker] Задача #{job_data['id']} была отменена пользователем. Карточки не сохраняются.", flush=True)
                return

            j.result_cards_json = json.dumps(all_collected_cards, ensure_ascii=False)
            j.cards_count = len(all_collected_cards)
            j.theme = extracted_theme
            j.status = "ready_for_review"
            j.processed_at = datetime.utcnow()
            j.char_count = char_count
            j.execution_time_ms = execution_time_ms
            j.fallback_used = any_fallback_used
            j.json_repair_applied = any_json_repair_applied
            j.error_trace = None
            await db.commit()

        print(f"[Generation Worker] Задача #{job_data['id']} успешно выполнена за {execution_time_ms / 1000:.1f} сек! Сформировано {len(all_collected_cards)} карточек ({decomp_rate:.2f} карт/1000 зн.).", flush=True)

        # Консолидация и сохранение семантического графа знаний (Milestone 2)
        total_graph_nodes = 0
        if all_chunk_graphs:
            try:
                consolidated = consolidate_knowledge_graphs(
                    chunk_graphs=all_chunk_graphs,
                    fallback_title=extracted_theme or job_data["subject"] or "Каркас знаний"
                )
                graph_nodes = consolidated.get("nodes", [])
                graph_edges = consolidated.get("edges", [])
                tree_data = consolidated.get("tree_data")

                if graph_nodes:
                    async with AsyncSessionLocal() as db:
                        subj = job_data["subject"]
                        u_id = job_data.get("user_id", "default_user")
                        stmt = select(TopicKnowledgeGraph).where(
                            TopicKnowledgeGraph.user_id == u_id,
                            TopicKnowledgeGraph.subject == subj
                        )
                        res = await db.execute(stmt)
                        rec = res.scalars().first()
                        final_graph_data = {"nodes": graph_nodes, "edges": graph_edges}
                        if rec and rec.graph_data:
                            existing_chunk = {
                                "nodes": rec.graph_data.get("nodes", []),
                                "edges": rec.graph_data.get("edges", [])
                            }
                            if existing_chunk.get("nodes"):
                                merged = consolidate_knowledge_graphs(
                                    chunk_graphs=[existing_chunk, final_graph_data],
                                    fallback_title=extracted_theme or subj or "Каркас знаний"
                                )
                                final_graph_data = {"nodes": merged.get("nodes", []), "edges": merged.get("edges", [])}
                                tree_data = merged.get("tree_data")
                            rec.graph_data = final_graph_data
                            rec.tree_data = tree_data
                            rec.updated_at = datetime.utcnow()
                        elif rec:
                            rec.graph_data = final_graph_data
                            rec.tree_data = tree_data
                            rec.updated_at = datetime.utcnow()
                        else:
                            rec = TopicKnowledgeGraph(
                                user_id=u_id,
                                subject=subj,
                                graph_data=final_graph_data,
                                tree_data=tree_data,
                                created_at=datetime.utcnow(),
                                updated_at=datetime.utcnow()
                            )
                            db.add(rec)
                        try:
                            await db.commit()
                            total_graph_nodes = len(final_graph_data["nodes"])
                            print(f"[Generation Worker] Граф знаний для '{subj}' сохранен: {total_graph_nodes} узлов, {len(final_graph_data['edges'])} связей.", flush=True)
                        except Exception as ce:
                            await db.rollback()
                            res = await db.execute(stmt)
                            rec = res.scalars().first()
                            if rec:
                                rec.graph_data = final_graph_data
                                rec.tree_data = tree_data
                                rec.updated_at = datetime.utcnow()
                                await db.commit()
                                total_graph_nodes = len(final_graph_data["nodes"])
            except Exception as graph_err:
                print(f"[Generation Worker WARN] Сбой консолидации графа знаний: {graph_err}", flush=True)

        # Отправка Telegram Push пользователю с кнопкой перехода в Песочницу
        if job_data.get("telegram_id"):
            try:
                chat_id = int(job_data["telegram_id"])
                escaped_theme = html.escape(str(extracted_theme))
                escaped_sub = html.escape(str(job_data['subject']))
                total_cards = len(all_collected_cards)
                webapp_url = getattr(settings, "WEBAPP_URL", "https://datagrinder.site")

                graph_line = f"Каркас знаний: <b>{total_graph_nodes} концептов</b> (инстанции, условия, исключения).\n" if total_graph_nodes > 0 else ""
                msg_text = (
                    "<b>[DATA GRINDER: МАТЕРИАЛ ОБРАБОТАН]</b>\n\n"
                    f"Тема: «<b>{escaped_theme}</b>»\n"
                    f"Предмет: <code>{escaped_sub}</code>\n"
                    f"ИИ сформировал: <b>{total_cards} ситуационных карточек</b>.\n"
                    f"{graph_line}"
                    f"Время обработки: <b>{execution_time_ms / 1000:.1f} сек</b>.\n\n"
                    "Нажмите кнопку ниже, чтобы открыть Песочницу и разобрать карточки (свайпы влево/вправо)."
                )
                markup = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=f"[◈ РАЗОБРАТЬ КАРТОЧКИ ({total_cards} ШТ.)]",
                        web_app=WebAppInfo(url=f"{webapp_url}#staging_job_{job_data['id']}")
                    )]
                ])
                await send_worker_telegram_push(chat_id, msg_text, reply_markup=markup)
                print(f"[Generation Worker] Push с кнопкой разбора карточек доставлен пользователю {chat_id}.", flush=True)
            except Exception as tg_err:
                print(f"[Generation Worker] Ошибка отправки финального push: {tg_err}", flush=True)

    except Exception as proc_err:
        import traceback
        trace_err = traceback.format_exc()
        execution_time_ms = int((time.time() - job_start_time) * 1000)
        print(f"[Generation Worker ERROR] Сбой задачи #{job_data['id']} ({execution_time_ms} мс): {proc_err}\n{trace_err}", flush=True)
        async with AsyncSessionLocal() as db:
            stmt = select(GenerationJob).filter(GenerationJob.id == job_data["id"])
            j = (await db.execute(stmt)).scalar_one_or_none()
            if j:
                j.status = "failed"
                j.error_message = str(proc_err)[:500]
                j.error_trace = trace_err
                j.char_count = char_count
                j.execution_time_ms = execution_time_ms
                j.processed_at = datetime.utcnow()
                await db.commit()

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
    print("[Generation Worker] Фоновый воркер генерации запущен (мониторинг скидочного окна 19:30 - 03:30 МСК).")
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
