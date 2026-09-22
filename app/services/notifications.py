# app/services/notifications.py
import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from app.database.session import AsyncSessionLocal
from app.database.models import UserSession, Card, utc_now
from app.core.config import settings
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.session.aiohttp import AiohttpSession

# Глобальные отметки отправки для предотвращения спама
last_morning_sent = None
last_evening_sent = None
last_due_notified = {}  # telegram_id -> (count, timestamp)

async def send_telegram_alert(chat_id: str, text: str, bot: Bot = None):
    if not settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_BOT_TOKEN == "placeholder_bot_token":
        print(f"[Notifier] Пропуск отправки (токен не настроен): {text}")
        return
    created_locally = False
    try:
        if bot is None:
            # Инициализируем сессию с коротким тайм-аутом 10 секунд
            session = AiohttpSession(timeout=10)
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, session=session)
            created_locally = True
        
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
    except Exception as e:
        print(f"[Notifier] Ошибка отправки уведомления в Телеграм: {e}")
    finally:
        if created_locally and bot and bot.session:
            await bot.session.close()

async def check_and_send_alerts():
    now_utc = utc_now()
    now_local = now_utc + timedelta(hours=3)  # Время по МСК/Минску (UTC+3)
    date_str = now_local.strftime("%Y-%m-%d")
    hour = now_local.hour

    bot = None
    session = None
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_TOKEN != "placeholder_bot_token":
        session = AiohttpSession(timeout=10)
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, session=session)

    try:
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(UserSession))
            users = res.scalars().all()
            if not users:
                return

            # Grouped total cards per user
            total_cards_stmt = select(Card.user_id, func.count(Card.id)).group_by(Card.user_id)
            total_cards_map = dict((await db.execute(total_cards_stmt)).all())

            # Grouped due cards per user
            due_cards_stmt = select(Card.user_id, func.count(Card.id)).filter(
                Card.state.in_([1, 2, 3]),
                Card.next_review <= now_utc
            ).group_by(Card.user_id)
            due_cards_map = dict((await db.execute(due_cards_stmt)).all())

            for user in users:
                user_total_cards = total_cards_map.get(user.telegram_id, 0)
                user_due_count = due_cards_map.get(user.telegram_id, 0)

                # 1. Утреннее уведомление (09:00 - 09:59)
                if hour == 9 and user.last_morning_sent != date_str:
                    if user_total_cards > 0:
                        user.last_morning_sent = date_str
                        await db.commit()  # Фиксация в БД
                        text = (
                            "**[DATA GRINDER: УТРЕННИЙ РАУНД]**\n\n"
                            "Новые знания готовы к заучиванию. Начни день с продуктивной сессии повторения!"
                        )
                        await send_telegram_alert(user.telegram_id, text, bot=bot)

                # 2. Вечернее уведомление (21:00 - 21:59)
                if hour == 21 and user.last_evening_sent != date_str:
                    if user_due_count > 0:
                        user.last_evening_sent = date_str
                        await db.commit()
                        text = (
                            "**[DATA GRINDER: ВЕЧЕРНИЙ СЕАНС]**\n\n"
                            f"У вас осталось *{user_due_count}* карточек к повторению. Закройте хвосты перед сном!"
                        )
                        await send_telegram_alert(user.telegram_id, text, bot=bot)

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
                            "**[DATA GRINDER: ОЧЕРЕДЬ ПОВТОРЕНИЯ]**\n\n"
                            f"В вашем пуле появились новые карты, готовые к повторению ({user_due_count} шт.)."
                        )
                        await send_telegram_alert(user.telegram_id, text, bot=bot)
                else:
                    if user.last_due_count != 0:
                        user.last_due_count = 0
                        await db.commit()
    except Exception as e:
        print(f"[Notifier] Ошибка при проверке/отправке уведомлений: {e}")
    finally:
        if session:
            await session.close()

async def notification_scheduler_loop():
    print("[Notifier] Фоновый планировщик уведомлений успешно запущен.")
    while True:
        await check_and_send_alerts()
        await asyncio.sleep(60)  # Проверяем раз в минуту
