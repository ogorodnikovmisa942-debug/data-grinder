# app/services/notifications.py
import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, or_, and_
from app.database.session import AsyncSessionLocal
from app.database.models import UserSession, UserSetting, ReviewLog, Card, utc_now
from app.core.timeutil import local_now
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
            [InlineKeyboardButton(text="[ОТКРЫТЬ КАРТОЧКИ]", web_app=WebAppInfo(url=settings.WEBAPP_URL))]
        ])
        
        await bot.send_message(
            chat_id=int(chat_id), 
            text=text, 
            parse_mode="HTML",
            reply_markup=markup
        )
    except Exception as e:
        print(f"[Notifier] Ошибка отправки уведомления в Телеграм: {e}")
    finally:
        if created_locally and bot and bot.session:
            await bot.session.close()

QUIET_START_HOUR = 22   # с 22:00 до 08:00 по местному времени пользователя уведомления о повторениях не шлём
QUIET_END_HOUR = 8
INSTANT_MIN_INTERVAL = timedelta(hours=4)
ACTIVE_RECENTLY = timedelta(minutes=30)   # пока человек занимается, не отвлекаем
STALE_LEARNING = timedelta(hours=12)      # шаг заучивания (5-30 мин) не повод для пуша, пока не «забыт» на полдня


def is_quiet_hour(hour: int) -> bool:
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


async def check_and_send_alerts():
    now_utc = utc_now()

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

            # Grouped due cards per user: повторения + «забытое» заучивание (короткие шаги обучения не считаем)
            due_cards_stmt = select(Card.user_id, func.count(Card.id)).filter(
                or_(
                    and_(Card.state == 2, Card.next_review <= now_utc),
                    and_(Card.state.in_([1, 3]), Card.next_review <= now_utc - STALE_LEARNING),
                )
            ).group_by(Card.user_id)
            due_cards_map = dict((await db.execute(due_cards_stmt)).all())

            # Последняя активность и часовые пояса
            last_review_map = dict((await db.execute(
                select(ReviewLog.user_id, func.max(ReviewLog.review_time)).group_by(ReviewLog.user_id)
            )).all())
            tz_map = dict((await db.execute(select(UserSetting.user_id, UserSetting.timezone))).all())

            for user in users:
                user_total_cards = total_cards_map.get(user.telegram_id, 0)
                user_due_count = due_cards_map.get(user.telegram_id, 0)

                local = local_now(tz_map.get(user.user_id) or tz_map.get(user.telegram_id), now_utc)
                date_str = local.strftime("%Y-%m-%d")
                hour = local.hour

                # 1. Утреннее уведомление (09:00 - 09:59): только если есть что повторять
                if hour == 9 and user.last_morning_sent != date_str:
                    if user_total_cards > 0 and user_due_count > 0:
                        user.last_morning_sent = date_str
                        await db.commit()  # Фиксация в БД
                        text = (
                            f"Карточек к повторению: {user_due_count} шт.\n"
                            "Зайди уделить 2-3 минуты."
                        )
                        await send_telegram_alert(user.telegram_id, text, bot=bot)

                # 2. Вечернее уведомление (21:00 - 21:59)
                if hour == 21 and user.last_evening_sent != date_str:
                    if user_due_count > 0:
                        user.last_evening_sent = date_str
                        await db.commit()
                        text = (
                            f"Осталось повторить карточек: {user_due_count} шт.\n"
                            "Закрой дневную норму перед сном."
                        )
                        await send_telegram_alert(user.telegram_id, text, bot=bot)

                # 3. Уведомление о накопившихся просроченных: не ночью, не чаще раза в 4 часа,
                # и не пока человек сам занимается
                if user_due_count > 0:
                    last_time = user.last_due_notified_at or datetime.min
                    last_active = last_review_map.get(user.telegram_id)
                    recently_active = bool(last_active and now_utc - last_active < ACTIVE_RECENTLY)
                    if (not is_quiet_hour(hour) and not recently_active
                            and now_utc - last_time > INSTANT_MIN_INTERVAL):
                        user.last_due_count = user_due_count
                        user.last_due_notified_at = now_utc
                        await db.commit()
                        text = f"Появились карточки к повторению: {user_due_count} шт."
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
