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
from app.database.models import UserSession
from sqlalchemy import select

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
        "🧠 **DATA GRINDER v1.0** приветствует тебя.\n\n"
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
                    text="🍅 **17 минут отдыха подошли к концу!**\n\n"
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

async def main():
    asyncio.create_task(pomodoro_push_observer())  # Корректный вызов без лишних аргументов
    print("[Grinder Bot] Фоновый пушер и обработчик команд инициализированы успешно.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("[Grinder Bot] Работа бота остановлена.")