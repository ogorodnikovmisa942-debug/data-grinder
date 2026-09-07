# /root/GRINDER/bot.py
import os
import asyncio
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Загружаем .env из директории скрипта (работает и на Windows, и на Linux)
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.session.aiohttp import AiohttpSession  
from app.database.session import AsyncSessionLocal
from app.database.models import UserSession, GenerationJob
from app.api.endpoints.management import save_cards_to_database
from app.services.ai_gateway import parse_raw_text
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
        await asyncio.sleep(20)  # Проверка очереди каждые 20 секунд
        
        # Проверяем, наступило ли внепиковое окно скидки
        if not is_deepseek_offpeak():
            continue

        job_data = None
        try:
            async with AsyncSessionLocal() as db:
                stmt = select(GenerationJob).filter(GenerationJob.status == "pending").order_by(GenerationJob.id.asc()).limit(1)
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
                        "custom_instruction": job.custom_instruction
                    }
        except Exception as db_err:
            print(f"[Night Grind] Ошибка чтения базы данных: {db_err}")
            continue

        if not job_data:
            continue

        print(f"[Night Grind] Старт обработки задачи #{job_data['id']} («{job_data['theme']}») по ночному тарифу DeepSeek...")
        try:
            parsed = await parse_raw_text(
                text=job_data["raw_text"],
                target_subject=job_data["subject"],
                density=job_data["density"],
                volume=job_data["volume"],
                granularity_mode=job_data["granularity_mode"],
                custom_instruction=job_data["custom_instruction"]
            )
            cards = parsed.get("cards", []) if isinstance(parsed, dict) else []
            if not cards:
                raise ValueError("ИИ не смог выделить карточки из переданного материала.")

            theme_name = parsed.get("phrase_title") or job_data["theme"]

            created_count = 0
            async with AsyncSessionLocal() as db:
                stmt = select(GenerationJob).filter(GenerationJob.id == job_data["id"])
                j = (await db.execute(stmt)).scalar_one_or_none()
                if not j or j.status == "cancelled":
                    print(f"[Night Grind] Задача #{job_data['id']} была отменена пользователем. Карточки не сохраняются.")
                    continue

                created_count, _, _ = await save_cards_to_database(
                    cards_data=cards,
                    subject_slug=job_data["subject"],
                    phrase_title=theme_name,
                    user_id=job_data["user_id"],
                    db=db
                )
                j.status = "completed"
                j.cards_count = created_count
                j.processed_at = datetime.utcnow()
                await db.commit()

            print(f"[Night Grind] Задача #{job_data['id']} выполнена! Создано карточек: {created_count}.")

            # Отправка Telegram Push пользователю (безопасный HTML без сбоев на спецсимволах)
            if job_data.get("telegram_id"):
                try:
                    import html
                    chat_id = int(job_data["telegram_id"])
                    escaped_theme = html.escape(str(theme_name))
                    escaped_sub = html.escape(str(job_data['subject']))
                    msg_text = (
                        "<b>[DATA GRINDER: НОЧНОЙ ЦИКЛ ЗАВЕРШЕН]</b>\n\n"
                        f"Материал «{escaped_theme}» деконструирован по ночному тарифу (-50% стоимости).\n"
                        f"Сформировано: <b>{created_count} новых карточек</b> по предмету <code>{escaped_sub}</code>.\n\n"
                        "Карточки размещены в базе знаний и готовы к интерливингу."
                    )
                    markup = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="[ОТКРЫТЬ ГРИНДЕР]", web_app=WebAppInfo(url=WEBAPP_URL))]
                    ])
                    await bot.send_message(chat_id=chat_id, text=msg_text, reply_markup=markup, parse_mode="HTML")
                    print(f"[Night Grind] Push успешно доставлен пользователю {chat_id}.")
                except Exception as tg_err:
                    print(f"[Night Grind] Ошибка отправки push: {tg_err}")

        except Exception as proc_err:
            print(f"[Night Grind ERROR] Сбой задачи #{job_data['id']}: {proc_err}")
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