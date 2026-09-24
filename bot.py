import os
import asyncio
import json
import html
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Загружаем .env из директории скрипта (работает и на Windows, и на Linux)
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp
from aiogram.client.session.aiohttp import AiohttpSession  
from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.database.models import UserSession, UserSetting, InviteCode, utc_now
from app.services.card_db_sync import save_cards_to_database, append_or_sync_cards_to_database
from app.services.generation_worker import generation_worker_loop
from app.core.config import settings

# Модуль админ-панели и клавиатур
from app.bot.keyboards import build_admin_keyboard, render_admin_dashboard_text
from app.bot.admin_handlers import (
    admin_router,
    get_admin_dashboard_data,
    is_admin,
    ADMIN_USERS
)

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
dp.include_router(admin_router)


# --- ОБРАБОТЧИК СТАРТА И РЕГИСТРАЦИИ ПОЛЬЗОВАТЕЛЕЙ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id_str = str(message.from_user.id)
    username = message.from_user.username
    full_name = message.from_user.full_name
    
    args = message.text.split(maxsplit=1)
    payload = args[1].strip() if len(args) > 1 else ""
    invite_code_clean = ""
    low_payload = payload.lower()
    if low_payload.startswith("inv_") or low_payload.startswith("inv-"):
        remainder = payload[4:].strip().upper()
        invite_code_clean = remainder if remainder.startswith("INV-") else f"INV-{remainder}"
    elif low_payload.startswith("inv"):
        invite_code_clean = payload.strip().upper()

    invite_status = None # "enrolled" | "already_enrolled" | "already_used" | "not_found"
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(UserSession).filter(UserSession.telegram_id == user_id_str))
            session = result.scalar_one_or_none()
            if not session:
                session = UserSession(telegram_id=user_id_str, user_id=user_id_str)
                db.add(session)
            
            session.username = username
            session.full_name = full_name
            
            # Проверка и активация инвайт-кода
            if invite_code_clean:
                if session.is_experiment_participant:
                    invite_status = "already_enrolled"
                else:
                    stmt_inv = select(InviteCode).filter(InviteCode.code == invite_code_clean)
                    inv = (await db.execute(stmt_inv)).scalar_one_or_none()
                    if inv:
                        if not inv.is_used:
                            inv.is_used = True
                            inv.used_by_user_id = user_id_str
                            inv.used_by_username = f"@{username}" if username else user_id_str
                            inv.used_at = utc_now()

                            session.is_experiment_participant = True
                            session.experiment_phase = 1

                            stmt_set = select(UserSetting).filter(UserSetting.user_id == user_id_str)
                            user_set = (await db.execute(stmt_set)).scalar_one_or_none()
                            if user_set:
                                user_set.is_experiment_participant = True
                                user_set.experiment_phase = 1
                            else:
                                user_set = UserSetting(
                                    user_id=user_id_str,
                                    daily_limit=settings.EXPERIMENT_DAILY_LIMIT,
                                    is_experiment_participant=True,
                                    experiment_phase=1
                                )
                                db.add(user_set)
                            invite_status = "enrolled"
                            print(f"[Bot] Инвайт {invite_code_clean} успешно активирован для @{username} ({user_id_str})")
                        else:
                            invite_status = "already_used"
                    else:
                        invite_status = "not_found"

            # Проверка и подключение колоды по шеринг-ссылке (?start=deck_...)
            shared_deck_info = None
            if payload.startswith("deck_"):
                deck_token = payload.replace("deck_", "").strip()
                share_file = Path("app/static/presets/shares") / f"{deck_token}.json"
                preset_file = Path("app/static/presets") / f"{deck_token}.json"

                target_file = share_file if share_file.exists() else (preset_file if preset_file.exists() else None)
                if target_file and target_file.exists():
                    try:
                        deck_json = json.loads(target_file.read_text(encoding="utf-8"))
                        d_cards = deck_json.get("cards", [])
                        d_sub = deck_json.get("subject_slug", "shared_deck")
                        d_title = deck_json.get("phrase_title", "Общая колода")
                        if d_cards:
                            created_c, updated_c, _, _ = await append_or_sync_cards_to_database(
                                cards_data=d_cards,
                                subject_slug=d_sub,
                                phrase_title=d_title,
                                user_id=user_id_str,
                                db=db
                            )
                            shared_deck_info = {
                                "title": d_title,
                                "subject": d_sub,
                                "created": created_c,
                                "updated": updated_c,
                                "total": len(d_cards)
                            }
                            print(f"[Bot] Колода {deck_token} успешно синхронизирована для {user_id_str} (+{created_c} новых, {updated_c} обновлено)")
                    except Exception as d_err:
                        print(f"[Bot WARN] Ошибка загрузки общей колоды {deck_token}: {d_err}")

            await db.commit()
    except Exception as db_err:
        print(f"[Bot] Ошибка работы с БД при /start: {db_err}")

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="[ЗАПУСТИТЬ ГРИНДЕР]", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    
    if shared_deck_info:
        welcome_text = (
            f"🎉 <b>Колода «{html.escape(shared_deck_info['title'])}» успешно подключена!</b>\n\n"
            f"• <b>Предмет:</b> <code>{shared_deck_info['subject']}</code>\n"
            f"• <b>Добавлено новых карточек:</b> <b>{shared_deck_info['created']} шт.</b>\n"
            f"• <b>Всего в колоде:</b> {shared_deck_info['total']} шт.\n\n"
            "Все карточки бережно добавлены в вашу очередь FSRS без сброса накопленного прогресса.\n\n"
            "Нажмите кнопку ниже для старта:"
        )
    elif invite_status == "enrolled":
        welcome_text = (
            "🎉 <b>Добро пожаловать в научный эксперимент Data Grinder!</b>\n\n"
            f"Инвайт-код <code>{invite_code_clean}</code> успешно подтвержден.\n"
            f"Вам назначен статус участника исследования:\n"
            f"• <b>Дневной режим:</b> {settings.EXPERIMENT_DAILY_LIMIT} карточек\n"
            f"• <b>Фаза:</b> 1 (Экспериментальная изоляция FSRS)\n\n"
            "Нажмите кнопку ниже для запуска персональной учебной сессии:"
        )
    elif invite_status == "already_enrolled":
        welcome_text = (
            "ℹ️ <b>Вы уже являетесь активным участником исследования!</b>\n\n"
            "Ваш профиль и настройки FSRS сохранены в базе.\n"
            "Нажмите кнопку ниже для продолжения учебной сессии:"
        )
    elif invite_status == "already_used":
        welcome_text = (
            "⚠️ <b>Этот инвайт-код уже был активирован ранее.</b>\n\n"
            f"Код <code>{invite_code_clean}</code> является одноразовым и уже использован другим участником.\n"
            "Вы можете продолжить работу в стандартном режиме:"
        )
    elif invite_status == "not_found":
        welcome_text = (
            "⚠️ <b>Инвайт-код не найден или устарел.</b>\n\n"
            f"Код <code>{invite_code_clean}</code> не зарегистрирован в системе.\n"
            "Нажмите кнопку ниже для старта:"
        )
    else:
        welcome_text = (
            "<b>[DATA GRINDER v1.0]</b> приветствует тебя.\n\n"
            "Интерфейс когнитивного заучивания и FSRS-интерливинга готов к работе. "
            "Нажми кнопку ниже для старта рабочей сессии."
        )

    await message.answer(welcome_text, reply_markup=markup, parse_mode="HTML")


# --- ФОНОВЫЙ ПРОЦЕСС МОНИТОРИНГА ТАЙМЕРА (БЕЗ ДУБЛИКАТОВ И СПАМА) ---
async def pomodoro_push_observer():
    print("[Pomodoro Observer] Фоновый пушер успешно запущен по точной схеме бэкенда.")
    while True:
        await asyncio.sleep(10)  # Проверка базы каждые 10 секунд
        
        expired_data = []
        try:
            async with AsyncSessionLocal() as db:
                now = utc_now()
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


# Фоновый воркер ночной нарезки (делегирован main.py, запускается в bot.py только при флаге RUN_WORKER_IN_BOT=true)
night_grind_worker = generation_worker_loop


async def main():
    asyncio.create_task(pomodoro_push_observer())
    if os.getenv("RUN_WORKER_IN_BOT", "false").lower() in ("true", "1"):
        asyncio.create_task(night_grind_worker())
        print("[Grinder Bot] Фоновый пушер и ночной воркер инициализированы успешно.")
    else:
        print("[Grinder Bot] Фоновый пушер инициализирован (ночной воркер делегирован grinder-web / main.py).")
    
    # Автоматическая настройка кнопки меню WebApp для запуска приложения в Telegram
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="[ГРИНДЕР]", web_app=WebAppInfo(url=WEBAPP_URL))
        )
        print(f"[Grinder Bot] Кнопка меню WebApp успешно настроена: {WEBAPP_URL}")
    except Exception as mb_err:
        print(f"[Grinder Bot WARN] Ошибка установки MenuButtonWebApp: {mb_err}")

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("[Grinder Bot] Работа бота остановлена.")