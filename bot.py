# /root/GRINDER/bot.py
import os
import asyncio
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Загружаем .env из директории скрипта (работает и на Windows, и на Linux)
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

import json
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.session.aiohttp import AiohttpSession  
from app.database.session import AsyncSessionLocal
from app.database.models import UserSession, GenerationJob
from app.api.endpoints.management import save_cards_to_database
from app.services.ai_gateway import parse_raw_text, split_text_into_chunks
from sqlalchemy import select, update

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PROXY_URL = os.getenv("TELEGRAM_PROXY")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://datagrinder.site")  

if not BOT_TOKEN:
    raise ValueError("КРИТИЧЕСКАЯ ОШИБКА БЕЗОПАСНОСТИ: Токен TELEGRAM_BOT_TOKEN не найден в .env!")

# Настраиваем тайм-аут 30 секунд для предотвращения бесконечного зависания сети
timeout = 30

if PROXY_URL:
    session = AiohttpSession(proxy=PROXY_URL, timeout=timeout)
    bot = Bot(token=BOT_TOKEN, session=session)
    print(f"[Grinder Bot] Сеть: активирован обход блокировки через прокси {PROXY_URL}")
else:
    session = AiohttpSession(timeout=timeout)
    bot = Bot(token=BOT_TOKEN, session=session)
    print("[Grinder Bot] Сеть: запуск напрямую (без прокси)")


dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id_str = str(message.from_user.id)
    
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(UserSession).filter(UserSession.telegram_id == user_id_str))
            session = result.scalar_one_or_none()
            if not session:
                session = UserSession(telegram_id=user_id_str)
                db.add(session)
                await db.commit()
                print(f"[Bot] Создана новая сессия для пользователя {user_id_str}")
    except Exception as db_err:
        print(f"[Bot] Ошибка работы с БД при /start: {db_err}")

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="[ЗАПУСТИТЬ ГРИНДЕР]", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    
    await message.answer(
        "**[DATA GRINDER v1.0]** приветствует тебя.\n\n"
        "Интерфейс когнитивного заучивания и FSRS-интерливинга готов к работе. "
        "Нажми кнопку ниже для старта рабочей сессии.",
        reply_markup=markup,
        parse_mode="Markdown"
    )

# --- ФОНОВЫЙ ПРОЦЕСС МОНИТОРИНГА ТАЙМЕРА (БЕЗ ДУБЛИКАТОВ И СПАМА) ---
async def pomodoro_push_observer():
    print("[Pomodoro Observer] Фоновый пушер успешно запущен по точной схеме бэкенда.")
    while True:
        await asyncio.sleep(10)  # Проверка базы каждые 10 секунд
        
        # 1. Быстро считываем сессии, требующие пуша, и сразу закрываем БД сессию
        expired_data = []
        try:
            async with AsyncSessionLocal() as db:
                now = datetime.utcnow()
                result = await db.execute(
                    select(UserSession).filter(
                        UserSession.is_resting == True,
                        UserSession.rest_ends_at <= now,
                        UserSession.notified == False
                    )
                )
                expired_sessions = result.scalars().all()
                for s in expired_sessions:
                    expired_data.append((s.id, s.telegram_id))
        except Exception as db_error:
            print(f"[Observer] Сбой чтения базы данных: {db_error}")
            continue

        # 2. Отправляем сообщения в Telegram и обновляем базу для успешных отправок
        for session_id, telegram_id in expired_data:
            try:
                target_chat_id = int(telegram_id)
                
                await bot.send_message(
                    chat_id=target_chat_id,
                    text="**[ТАЙМЕР: 17 МИНУТ ОТДЫХА ЗАВЕРШЕНЫ]**\n\n"
                         "Кора головного мозга полностью восстановила ресурсы.\n"
                         "Возвращайся в консоль Data Grinder и запускай новый 52-минутный спринт.",
                    parse_mode="Markdown"
                )
                
                # Обновляем запись в БД в короткой изолированной транзакции
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(UserSession).filter(UserSession.id == session_id))
                    s = result.scalar_one_or_none()
                    if s:
                        s.is_resting = False
                        s.notified = True 
                        await db.commit()
                print(f"[Observer] Пуш успешно отправлен и зафиксирован для {target_chat_id}.")
            except Exception as e:
                print(f"[Observer] Ошибка обработки/отправки пуша для {telegram_id}: {e}")

# --- ФОНОВЫЙ ВОРКЕР «НОЧНОЙ ГРАЙНД» (ДЕКОМПОЗИЦИЯ СО СКИДКОЙ 50%) ---
def is_deepseek_offpeak() -> bool:
    """Проверяет, активно ли внепиковое окно со скидкой 50% у DeepSeek (16:30-00:30 UTC / 19:30-03:30 МСК)."""
    now_utc = datetime.utcnow()
    minutes = now_utc.hour * 60 + now_utc.minute
    # 16:30 UTC = 990 мин, 00:30 UTC = 30 мин
    return minutes >= 990 or minutes < 30

async def night_grind_worker():
    """Фоновый воркер Ночного Грайндера: обработка отложенных очередей в часы скидок."""
    print("[Night Grind Worker] Воркер ночной очереди успешно запущен (окно 19:30 - 03:30 МСК).")
    
    # Сброс зависших задач, если бот перезагружался во время обработки
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(GenerationJob)
                .filter(GenerationJob.status == "processing")
                .values(status="pending")
            )
            await db.commit()
    except Exception as reset_err:
        print(f"[Night Grind] Замечание при сбросе очереди: {reset_err}")

    while True:
        await asyncio.sleep(10)  # Проверка очереди каждые 10 секунд
        
        is_offpeak = is_deepseek_offpeak()
        
        job_data = None
        try:
            async with AsyncSessionLocal() as db:
                stmt = select(GenerationJob).filter(GenerationJob.status == "pending")
                # Если сейчас дневное пиковое время (без ночной скидки 50%),
                # обрабатываем только фоновые задачи без требования ночной скидки (is_deferred == False)
                if not is_offpeak:
                    stmt = stmt.filter(GenerationJob.is_deferred == False)
                stmt = stmt.order_by(GenerationJob.id.asc()).limit(1)

                res = await db.execute(stmt)
                job = res.scalar_one_or_none()
                if job:
                    job.status = "processing"
                    await db.commit()
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
        except Exception as db_err:
            print(f"[Night Grind] Ошибка чтения базы данных: {db_err}")
            continue

        if not job_data:
            continue

        tariff_label = "ночному тарифу (-50% стоимости)" if is_offpeak else "дневному фоновому тарифу"
        print(f"[Night Grind] Старт обработки задачи #{job_data['id']} («{job_data['theme']}») по {tariff_label}...", flush=True)
        try:
            import re
            clean_text_no_headers = re.sub(r'=== [^=]+ ===', '', job_data["raw_text"]).strip()
            clean_text_no_headers = re.sub(r'--- [^\n]+ ---', '', clean_text_no_headers).strip()
            if len(clean_text_no_headers) < 15:
                raise ValueError("Распознанный текст слишком короткий или пуст (менее 15 знаков). Похоже, в документе нет текста.")

            # Умное разбиение на смысловые чанки (~15 страниц / до 30 000 знаков)
            chunks = split_text_into_chunks(job_data["raw_text"], max_chunk_chars=30000)
            print(f"[Night Grind] Задача #{job_data['id']}: материал разбит на {len(chunks)} частей по ~15 страниц для предотвращения переполнения токенов.", flush=True)

            all_collected_cards = []
            seen_card_texts = set()
            extracted_theme = job_data["theme"]

            for chunk_idx, chunk_text in enumerate(chunks, 1):
                print(f"[Night Grind] Задача #{job_data['id']}: нарезка части {chunk_idx}/{len(chunks)} ({len(chunk_text)} знаков)...", flush=True)
                try:
                    parsed = await parse_raw_text(
                        text=chunk_text,
                        target_subject=job_data["subject"],
                        density=job_data["density"],
                        volume=job_data["volume"],
                        granularity_mode=job_data["granularity_mode"],
                        custom_instruction=job_data["custom_instruction"]
                    )
                    if isinstance(parsed, dict):
                        if parsed.get("phrase_title") and extracted_theme in ("Новый блок знаний", "Материал", ""):
                            extracted_theme = parsed["phrase_title"]
                        chunk_cards = parsed.get("cards", [])
                        if isinstance(chunk_cards, list):
                            for c in chunk_cards:
                                c_text = (c.get("text") or "").strip().lower()
                                if c_text and c_text not in seen_card_texts:
                                    seen_card_texts.add(c_text)
                                    all_collected_cards.append(c)
                except Exception as chunk_err:
                    print(f"[Night Grind WARN] Ошибка в части {chunk_idx}/{len(chunks)}: {chunk_err}", flush=True)

                if chunk_idx < len(chunks):
                    await asyncio.sleep(1.5)

            if not all_collected_cards:
                print(f"[Night Grind WARN] Задача #{job_data['id']}: ИИ не смог сформировать карточки.", flush=True)
                raise ValueError("ИИ не смог выделить карточки из переданного материала.")

            async with AsyncSessionLocal() as db:
                stmt = select(GenerationJob).filter(GenerationJob.id == job_data["id"])
                j = (await db.execute(stmt)).scalar_one_or_none()
                if not j or j.status == "cancelled":
                    print(f"[Night Grind] Задача #{job_data['id']} была отменена пользователем. Карточки не сохраняются.", flush=True)
                    continue

                j.result_cards_json = json.dumps(all_collected_cards, ensure_ascii=False)
                j.cards_count = len(all_collected_cards)
                j.theme = extracted_theme
                j.status = "ready_for_review"
                j.processed_at = datetime.utcnow()
                await db.commit()

            print(f"[Night Grind] Задача #{job_data['id']} выполнена! Сформировано {len(all_collected_cards)} карточек (статус ready_for_review).", flush=True)

            # Отправка Telegram Push пользователю с кнопкой перехода прямо в Песочницу!
            if job_data.get("telegram_id"):
                try:
                    import html
                    chat_id = int(job_data["telegram_id"])
                    escaped_theme = html.escape(str(extracted_theme))
                    escaped_sub = html.escape(str(job_data['subject']))
                    total_cards = len(all_collected_cards)

                    msg_text = (
                        "<b>[DATA GRINDER: МАТЕРИАЛ ОБРАБОТАН]</b>\n\n"
                        f"Тема: «<b>{escaped_theme}</b>»\n"
                        f"Предмет: <code>{escaped_sub}</code>\n"
                        f"ИИ сформировал: <b>{total_cards} карточек</b>.\n\n"
                        "Нажмите кнопку ниже, чтобы открыть Песочницу и разобрать карточки (свайпы влево/вправо)."
                    )
                    markup = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(
                            text=f"🔍 Разобрать карточки ({total_cards} шт.)",
                            web_app=WebAppInfo(url=f"{WEBAPP_URL}#staging_job_{job_data['id']}")
                        )]
                    ])
                    await bot.send_message(chat_id=chat_id, text=msg_text, reply_markup=markup, parse_mode="HTML")
                    print(f"[Night Grind] Push с кнопкой разбора карточек успешно доставлен пользователю {chat_id}.", flush=True)
                except Exception as tg_err:
                    print(f"[Night Grind] Ошибка отправки push: {tg_err}", flush=True)

        except Exception as proc_err:
            import traceback
            trace_err = traceback.format_exc()
            print(f"[Night Grind ERROR] Сбой задачи #{job_data['id']}: {proc_err}\n{trace_err}", flush=True)
            async with AsyncSessionLocal() as db:
                stmt = select(GenerationJob).filter(GenerationJob.id == job_data["id"])
                j = (await db.execute(stmt)).scalar_one_or_none()
                if j:
                    j.status = "failed"
                    j.error_message = str(proc_err)[:500]
                    j.processed_at = datetime.utcnow()
                    await db.commit()


async def main():
    asyncio.create_task(pomodoro_push_observer())
    asyncio.create_task(night_grind_worker())
    print("[Grinder Bot] Фоновый пушер и ночной воркер инициализированы успешно.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("[Grinder Bot] Работа бота остановлена.")