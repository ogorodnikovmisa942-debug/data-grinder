# app/services/notifications.py
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select, func
from app.database.session import AsyncSessionLocal
from app.database.models import UserSession, Card
from app.core.config import settings
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.session.aiohttp import AiohttpSession

# Глобальные отметки отправки для предотвращения спама
last_morning_sent = None
last_evening_sent = None
last_due_notified = {}  # telegram_id -> (count, timestamp)

async def send_telegram_alert(chat_id: str, text: str):
    if not settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_BOT_TOKEN == "placeholder_bot_token":
        print(f"[Notifier] Пропуск отправки (токен не настроен): {text}")
        return
    try:
        # Инициализируем сессию с коротким тайм-аутом 10 секунд
        session = AiohttpSession(timeout=10)
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, session=session)
        
        # Создаем разметку с кнопкой запуска Mini App
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="[ЗАПУСТИТЬ ГРИНДЕР]", web_app=WebAppInfo(url=settings.WEBAPP_URL))]
        ])
        
        await bot.send_message(
            chat_id=int(chat_id), 
            text=text, 
            parse_mode="Markdown",
            reply_markup=markup
        )
        await bot.session.close()
    except Exception as e:
        print(f"[Notifier] Ошибка отправки уведомления в Телеграм: {e}")

async def check_and_send_alerts():
    now_utc = datetime.utcnow()
    now_local = now_utc + timedelta(hours=3)  # Время по МСК/Минску (UTC+3)
    date_str = now_local.strftime("%Y-%m-%d")
    hour = now_local.hour

    try:
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(UserSession))
            users = res.scalars().all()
            if not users:
                return

            for user in users:
                # Считаем карточки строго для данного пользователя
                res_cards = await db.execute(
                    select(func.count(Card.id)).filter(Card.user_id == user.telegram_id)
                )
                user_total_cards = res_cards.scalar() or 0

                res_due = await db.execute(
                    select(func.count(Card.id)).filter(
                        Card.user_id == user.telegram_id,
                        Card.state.in_([1, 2, 3]), 
                        Card.next_review <= now_utc
                    )
                )
                user_due_count = res_due.scalar() or 0

                # 1. Утреннее уведомление (09:00 - 09:59)
                if hour == 9 and user.last_morning_sent != date_str:
                    if user_total_cards > 0:
                        user.last_morning_sent = date_str
                        await db.commit()  # Фиксация в БД
                        text = (
                            "🧠 **Утренний раунд Data Grinder!**\n\n"
                            "Новые знания готовы к заучиванию. Начни день с продуктивной сессии повторения!"
                        )
                        await send_telegram_alert(user.telegram_id, text)

                # 2. Вечернее уведомление (21:00 - 21:59)
                if hour == 21 and user.last_evening_sent != date_str:
                    if user_due_count > 0:
                        user.last_evening_sent = date_str
                        await db.commit()
                        text = (
                            "🌙 **Вечерний гринд!**\n\n"
                            f"У вас осталось *{user_due_count}* карточек к повторению. Закройте хвосты перед сном!"
                        )
                        await send_telegram_alert(user.telegram_id, text)

                # 3. Моментальное уведомление о новых просроченных картах (не чаще раз в 4 часа)
                if user_due_count > 0:
                    last_time = user.last_due_notified_at or datetime.min
                    last_count = user.last_due_count or 0
                    time_elapsed = now_utc - last_time

                    if user_due_count > last_count or time_elapsed > timedelta(hours=4):
                        user.last_due_count = user_due_count
                        user.last_due_notified_at = now_utc
                        await db.commit()
                        text = (
                            "⚡ **Data Grinder Alert!**\n\n"
                            f"В вашем пуле появились новые карты, готовые к повторению ({user_due_count} шт.)."
                        )
                        await send_telegram_alert(user.telegram_id, text)
                else:
                    if user.last_due_count != 0:
                        user.last_due_count = 0
                        await db.commit()
    except Exception as e:
        print(f"[Notifier] Ошибка при проверке/отправке уведомлений: {e}")

async def notification_scheduler_loop():
    print("[Notifier] Фоновый планировщик уведомлений успешно запущен.")
    while True:
        await check_and_send_alerts()
        await asyncio.sleep(60)  # Проверяем раз в минуту
