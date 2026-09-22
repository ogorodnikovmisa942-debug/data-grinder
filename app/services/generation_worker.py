# app/services/generation_worker.py
import os
import re
import json
import time
import html
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select, update, text, delete
from app.database.session import AsyncSessionLocal
from app.database.models import GenerationJob, TopicKnowledgeGraph, utc_now
from app.services.ai_gateway import parse_raw_text, split_text_into_chunks, is_blacklisted_card, semantic_normalize_front, extract_curriculum_skeleton, analyze_source_density
from app.services.graph_service import consolidate_knowledge_graphs, resolve_subject_alias, get_all_subject_aliases
from app.services.card_db_sync import deduplicate_cards_batch
from app.services.practice_service import generate_practice_session
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
        # Поддержка мокирования datetime.utcnow в существующих тестах
        if hasattr(datetime, "utcnow") and hasattr(datetime.utcnow, "return_value"):
            now_utc = datetime.utcnow()
            minutes = now_utc.hour * 60 + now_utc.minute
            return minutes >= 990 or minutes < 30
    except Exception:
        pass
    try:
        now_utc = datetime.now(timezone.utc)
        if isinstance(now_utc.hour, int):
            minutes = now_utc.hour * 60 + now_utc.minute
            return minutes >= 990 or minutes < 30
    except Exception:
        pass
    now_utc = utc_now()
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

        # Умное адаптивное разбиение на смысловые блоки с учетом типа источника
        density_info = analyze_source_density(job_data["raw_text"])
        max_chars = density_info.get("target_chunk_chars", 40000)
        overlap = 1500
        chunks = split_text_into_chunks(job_data["raw_text"], max_chunk_chars=max_chars, overlap_chars=overlap)
        total_chunks = len(chunks)
        print(f"[Generation Worker] Задача #{job_data['id']}: тип «{density_info['archetype']}», скомпонован в {total_chunks} смысловых блоков/разделов.", flush=True)

        # Отправляем мгновенное уведомление в Telegram о старте генерации
        if job_data.get("telegram_id"):
            try:
                escaped_theme = html.escape(str(job_data['theme']))
                discount_badge = "🔥 <b>Скидка 50% активна!</b>\n" if is_offpeak else "☀️ <b>Дневная обработка</b>\n"
                archetype_ru = {
                    "slides": f"Презентация ({density_info.get('slide_count', total_chunks * 6)} слайдов)",
                    "dense_notes": "Конспект / шпаргалка",
                    "textbook": "Учебник / книга",
                    "statutory_code": "Кодекс / закон",
                    "short_article": "Статья / материал"
                }.get(density_info.get("archetype", ""), "Материал")
                start_msg = (
                    f"{discount_badge}"
                    f"🚀 <b>Документ взят в обработку ИИ!</b>\n\n"
                    f"Материал: «<b>{escaped_theme}</b>»\n"
                    f"Формат: <b>{archetype_ru}</b>\n"
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
        seen_semantic_keys = set()
        seen_short_answers = set()
        extracted_theme = job_data["theme"]
        any_fallback_used = False
        any_json_repair_applied = False

        # --- ПРОХОД 1: СИНТЕЗ КАРКАСА ДИСЦИПЛИНЫ (CURRICULUM SKELETON) ДЛЯ КРУПНЫХ ТЕКСТОВ И ПРЕЗЕНТАЦИЙ ---
        curriculum_skeleton = None
        should_run_skeleton = (
            total_chunks >= 2 
            or density_info.get("is_presentation") 
            or density_info.get("slide_count", 0) >= 8 
            or (density_info.get("is_dense_notes") and char_count >= 10000) 
            or char_count >= 25000
        )
        if should_run_skeleton:
            print(f"[Generation Worker] Задача #{job_data['id']}: Проход 1 — извлечение дерева органов и институтов...", flush=True)
            try:
                is_auto = job_data.get("volume") in ("auto", "balanced", None, "")
                if density_info.get("is_presentation"):
                    target_skel_cards = min(max(total_chunks * 6, 45), 80) if is_auto else 65
                elif density_info.get("is_dense_notes"):
                    target_skel_cards = min(max(total_chunks * 10, 45), 140) if is_auto else 80
                else:
                    target_skel_cards = min(max(total_chunks * 7, 100), 250) if is_auto else 150

                curriculum_skeleton = await extract_curriculum_skeleton(
                    text=job_data["raw_text"],
                    target_subject=job_data["subject"],
                    target_card_count=target_skel_cards,
                    user_id=job_data.get("user_id", "default_user"),
                    job_id=str(job_data["id"]),
                    force_chat_model=True
                )
                if curriculum_skeleton and curriculum_skeleton.get("phrase_title") and extracted_theme in ("Новый блок знаний", "Материал", ""):
                    extracted_theme = curriculum_skeleton["phrase_title"]
                skel_graph = curriculum_skeleton.get("graph") or curriculum_skeleton.get("knowledge_graph") if curriculum_skeleton else None
                if skel_graph and isinstance(skel_graph, dict):
                    skel_nodes = skel_graph.get("nodes") or []
                    if skel_nodes:
                        all_chunk_graphs.append(skel_graph)
            except Exception as skel_err:
                print(f"[Generation Worker WARN] Сбой Прохода 1: {skel_err}", flush=True)

        # Конкурентная нарезка чанков с семафором (4 параллельных запроса благодаря высокой пропускной способности DeepSeek-V3)
        semaphore = asyncio.Semaphore(4)

        async def process_chunk(chunk_idx: int, chunk_text: str):
            async with semaphore:
                print(f"[Generation Worker] Задача #{job_data['id']}: нарезка блока {chunk_idx}/{total_chunks} ({len(chunk_text)} знаков)...", flush=True)
                for attempt in range(1, 4):
                    try:
                        parsed = await parse_raw_text(
                            text=chunk_text,
                            target_subject=job_data["subject"],
                            density=job_data["density"],
                            volume=job_data["volume"],
                            granularity_mode=job_data["granularity_mode"],
                            custom_instruction=job_data["custom_instruction"],
                            user_id=job_data.get("user_id", "default_user"),
                            job_id=str(job_data["id"]),
                            skip_graph=bool(curriculum_skeleton and total_chunks >= 2),
                            force_chat_model=True
                        )
                        return chunk_idx, parsed
                    except Exception as chunk_err:
                        print(f"[Generation Worker WARN] Блок {chunk_idx}/{total_chunks}, попытка {attempt}/3 не удалась: {chunk_err}", flush=True)
                        if attempt < 3:
                            await asyncio.sleep(attempt * 1.5)
                        else:
                            return chunk_idx, None
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
            if parsed.get("phrase_title") and extracted_theme in ("Новый блок знаний", "Материал", "") and not (curriculum_skeleton and curriculum_skeleton.get("phrase_title")):
                extracted_theme = parsed["phrase_title"]
            chunk_cards = parsed.get("cards", [])
            if isinstance(chunk_cards, list):
                # Калибровка объема: сохраняем пропорциональное количество глубоких карточек с учетом формата материала
                is_auto_volume = job_data.get("volume") in ("auto", "balanced", None, "")
                vol = job_data.get("volume")
                if density_info.get("is_presentation"):
                    if vol in ("low", "low_5"):
                        chunk_cards = chunk_cards[:4]
                    elif vol in ("high", "high_20"):
                        chunk_cards = chunk_cards[:10]
                    elif vol == "max":
                        pass  # Без ограничений: сохраняем все карточки слайдов
                    else:
                        chunk_cards = chunk_cards[:8]
                elif density_info.get("is_dense_notes"):
                    if vol in ("low", "low_5"):
                        chunk_cards = chunk_cards[:6]
                    elif vol in ("high", "high_20"):
                        chunk_cards = chunk_cards[:18]
                    elif vol == "max":
                        pass  # Без ограничений: сохраняем все факты и ветви конспекта
                    else:
                        chunk_cards = chunk_cards[:14]
                else:
                    if vol in ("low", "low_5"):
                        chunk_cards = chunk_cards[:6]
                    elif vol in ("high", "high_20"):
                        chunk_cards = chunk_cards[:18]
                    elif vol == "max":
                        pass  # Без ограничений: сохраняем все карточки книги
                    elif is_auto_volume and total_chunks >= 3:
                        chunk_cards = chunk_cards[:14]

                for c in chunk_cards:
                    c["source_chunk_idx"] = chunk_idx
                    raw_c_text = (c.get("text") or "").strip()
                    norm_key = normalize_front(raw_c_text)
                    sem_key = semantic_normalize_front(raw_c_text)

                    # Межчанковая дедупликация коротких определений (предотвращает появление 2 одинаковых карточек "Норма права.")
                    raw_ans = (c.get("translation") or "").strip()
                    norm_ans = normalize_front(raw_ans)
                    is_short_ans = bool(norm_ans) and len(raw_ans.split()) <= 2

                    if norm_key and norm_key not in seen_card_texts and (not sem_key or sem_key not in seen_semantic_keys):
                        if is_short_ans and norm_ans in seen_short_answers:
                            continue

                        is_bl, _ = is_blacklisted_card(c, subject_domain=job_data.get("subject", "generic"))
                        if not is_bl:
                            seen_card_texts.add(norm_key)
                            if sem_key:
                                seen_semantic_keys.add(sem_key)
                            if is_short_ans:
                                seen_short_answers.add(norm_ans)
                            all_collected_cards.append(c)

            chunk_graph = parsed.get("knowledge_graph")
            if chunk_graph and isinstance(chunk_graph, dict):
                nodes = chunk_graph.get("nodes") or []
                if nodes:
                    all_chunk_graphs.append(chunk_graph)

        # Определение целевого бюджета карточек с учетом формата материала и настроек пользователя
        is_auto_volume = job_data.get("volume") in ("auto", "balanced", None, "")
        vol = job_data.get("volume")
        if is_auto_volume or vol == "max":
            # Без искусственных потолков: сохраняем все качественно извлеченные атомарные карточки из всех глав
            target_budget = len(all_collected_cards)
        else:
            vol_per_block = {
                "low": 5, "low_5": 5,
                "med_10": 10,
                "medium": 14, "med_15": 14,
                "high": 18, "high_20": 18,
            }.get(vol, len(all_collected_cards))
            target_budget = total_chunks * vol_per_block

        # СТРАТИФИЦИРОВАННАЯ КАЛИБРОВКА (Stratified Retention):
        # Если карточек больше целевого бюджета, сокращаем ПРОПОРЦИОНАЛЬНО из каждого смыслового блока/главы,
        # сохраняя 100% сквозное покрытие материала от первой до последней страницы!
        if len(all_collected_cards) > target_budget and target_budget < 99999:
            print(f"[Generation Worker] Стратифицированная калибровка: оптимизация {len(all_collected_cards)} -> {target_budget} карт с равномерным покрытием всех {total_chunks} блоков...", flush=True)
            from collections import defaultdict
            cards_by_chunk = defaultdict(list)
            for c in all_collected_cards:
                cards_by_chunk[c.get("source_chunk_idx", 1)].append(c)

            num_active_chunks = len(cards_by_chunk)
            if num_active_chunks > 0:
                base_quota = max(1, target_budget // num_active_chunks)
                remainder = target_budget % num_active_chunks

                stratified_cards = []
                overflow_pool = []
                for ch_idx in sorted(cards_by_chunk.keys()):
                    ch_cards = cards_by_chunk[ch_idx]
                    take_k = base_quota + (1 if remainder > 0 else 0)
                    if remainder > 0:
                        remainder -= 1
                    stratified_cards.extend(ch_cards[:take_k])
                    overflow_pool.extend(ch_cards[take_k:])

                if len(stratified_cards) < target_budget and overflow_pool:
                    stratified_cards.extend(overflow_pool[:target_budget - len(stratified_cards)])

                all_collected_cards = stratified_cards

        # Сквозная дедупликация карточек перед ранжированием
        all_collected_cards = deduplicate_cards_batch(all_collected_cards)

        # Топологическая пересортировка и сквозное ранжирование деки ("Graph in engine, playlist in UI"):
        # 1. Порядок глав / смысловых блоков книги (source_chunk_idx)
        # 2. Внутри главы — от фундаментального слоя (layer 0/1) к сложным условиям (layer 2)
        all_collected_cards.sort(
            key=lambda c: (
                c.get("source_chunk_idx", 1),
                int(c.get("layer", 1)) if str(c.get("layer", 1)).isdigit() else 1
            )
        )
        for rank_idx, c in enumerate(all_collected_cards, 1):
            c["topological_rank"] = rank_idx

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
            j.processed_at = utc_now()
            j.char_count = char_count
            j.execution_time_ms = execution_time_ms
            j.fallback_used = any_fallback_used
            j.json_repair_applied = any_json_repair_applied
            j.error_trace = None
            await db.commit()

        print(f"[Generation Worker] Задача #{job_data['id']} успешно выполнена за {execution_time_ms / 1000:.1f} сек! Сформировано {len(all_collected_cards)} карточек ({decomp_rate:.2f} карт/1000 зн.).", flush=True)

        # Консолидация и сохранение семантического графа знаний (Milestone 2)
        total_graph_nodes = 0
        try:
            if all_chunk_graphs:
                consolidated = consolidate_knowledge_graphs(
                    chunk_graphs=all_chunk_graphs,
                    fallback_title=extracted_theme or job_data["subject"] or "Каркас знаний"
                )
                graph_nodes = consolidated.get("nodes", [])
                graph_edges = consolidated.get("edges", [])
                tree_data = consolidated.get("tree_data")
            elif all_collected_cards:
                from app.services.graph_service import synthesize_graph_from_cards
                syn = synthesize_graph_from_cards(
                    all_collected_cards,
                    fallback_title=extracted_theme or job_data["subject"] or "Каркас знаний"
                )
                graph_nodes = syn.get("graph_data", {}).get("nodes", [])
                graph_edges = syn.get("graph_data", {}).get("edges", [])
                tree_data = syn.get("tree_data")
            else:
                graph_nodes, graph_edges, tree_data = [], [], None

            if graph_nodes:
                async with AsyncSessionLocal() as db:
                    raw_subj = job_data["subject"]
                    subj = (raw_subj or "").strip() or "general"
                    all_aliases = get_all_subject_aliases(subj)
                    u_id = job_data.get("user_id", "default_user")

                    # Исключаем конфликты записей по алиасам одного и того же предмета для одного пользователя
                    await db.execute(
                        delete(TopicKnowledgeGraph).where(
                            TopicKnowledgeGraph.user_id == u_id,
                            TopicKnowledgeGraph.subject.in_(all_aliases),
                            TopicKnowledgeGraph.subject != subj
                        )
                    )

                    stmt = select(TopicKnowledgeGraph).where(
                        TopicKnowledgeGraph.user_id == u_id,
                        TopicKnowledgeGraph.subject == subj
                    )
                    res = await db.execute(stmt)
                    rec = res.scalars().first()
                    final_graph_data = {"nodes": graph_nodes, "edges": graph_edges, "deck_size": len(all_collected_cards)}
                    if rec and rec.graph_data:
                        existing_chunk = {
                            "nodes": rec.graph_data.get("nodes", []),
                            "edges": rec.graph_data.get("edges", [])
                        }
                        # Слияние используем только для маленьких добавочных порций (< 30 карт).
                        # Если же сгенерирован полноценный курс, новый чистый граф полностью обновляет устаревший снапшот.
                        should_merge = bool(existing_chunk.get("nodes")) and len(all_collected_cards) < 30 and len(graph_nodes) < 20
                        if should_merge:
                            merged = consolidate_knowledge_graphs(
                                chunk_graphs=[existing_chunk, final_graph_data],
                                fallback_title=extracted_theme or subj or "Каркас знаний"
                            )
                            final_graph_data = {"nodes": merged.get("nodes", []), "edges": merged.get("edges", []), "deck_size": len(all_collected_cards)}
                            tree_data = merged.get("tree_data")
                        rec.graph_data = final_graph_data
                        rec.tree_data = tree_data
                        rec.updated_at = utc_now()
                    elif rec:
                        rec.graph_data = final_graph_data
                        rec.tree_data = tree_data
                        rec.updated_at = utc_now()
                    else:
                        rec = TopicKnowledgeGraph(
                            user_id=u_id,
                            subject=subj,
                            graph_data=final_graph_data,
                            tree_data=tree_data,
                            created_at=utc_now(),
                            updated_at=utc_now()
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
                            rec.updated_at = utc_now()
                            await db.commit()
                            total_graph_nodes = len(final_graph_data["nodes"])

                    # Синхронизируем интерактивные практические задания по предмету (R2.2)
                    try:
                        await generate_practice_session(user_id=u_id, subject=subj, count=10, db=db)
                        print(f"[Generation Worker] Практические задания для '{subj}' успешно синхронизированы.", flush=True)
                    except Exception as p_err:
                        print(f"[Generation Worker WARN] Сбой синхронизации практики: {p_err}", flush=True)
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
                j.processed_at = utc_now()
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
