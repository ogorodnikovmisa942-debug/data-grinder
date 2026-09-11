import os
import asyncio
import time
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Загружаем .env из директории скрипта (работает и на Windows, и на Linux)
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

import json
import csv
import io
import html
import secrets
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, BufferedInputFile, CallbackQuery
from aiogram.client.session.aiohttp import AiohttpSession  
from app.database.session import AsyncSessionLocal
from app.database.models import UserSession, GenerationJob, UserSetting, InviteCode, Card, ReviewLog, AiTelemetryLog, Phrase
from app.api.endpoints.management import save_cards_to_database, append_or_sync_cards_to_database
from app.services.ai_gateway import parse_raw_text, split_text_into_chunks
from app.core.config import settings
from sqlalchemy import select, update, func, text, delete

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

# --- АДМИНИСТРАТИВНЫЕ ПРАВА И СЕССИЯ ---
ADMIN_USERS: set[int] = set()
if getattr(settings, "ADMIN_TELEGRAM_ID", None):
    for aid in str(settings.ADMIN_TELEGRAM_ID).split(","):
        aid = aid.strip()
        if aid.isdigit():
            ADMIN_USERS.add(int(aid))

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USERS


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id_str = str(message.from_user.id)
    username = message.from_user.username
    full_name = message.from_user.full_name
    
    args = message.text.split(maxsplit=1)
    payload = args[1].strip() if len(args) > 1 else ""
    invite_code_clean = payload.replace("inv_", "").strip() if payload.startswith("inv_") else payload

    invite_enrolled = False
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
                stmt_inv = select(InviteCode).filter(InviteCode.code == invite_code_clean)
                inv = (await db.execute(stmt_inv)).scalar_one_or_none()
                if inv and not inv.is_used:
                    inv.is_used = True
                    inv.used_by_user_id = user_id_str
                    inv.used_by_username = f"@{username}" if username else user_id_str
                    inv.used_at = datetime.utcnow()

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
                    invite_enrolled = True
                    print(f"[Bot] Инвайт {invite_code_clean} успешно активирован для @{username} ({user_id_str})")

                    # Авто-загрузка эталонного пакета карточек по судоустройству для нового студента
                    preset_path = Path("app/static/presets/sudoustroystvo.json")
                    if preset_path.exists():
                        try:
                            preset_data = json.loads(preset_path.read_text(encoding="utf-8"))
                            p_cards = preset_data.get("cards", [])
                            p_title = preset_data.get("phrase_title", "Судоустройство: Основной курс")
                            p_sub = preset_data.get("subject_slug", "sudoustroystvo")
                            if p_cards:
                                has_cards = (await db.execute(
                                    select(func.count(Card.id)).filter(Card.user_id == user_id_str, Card.subject == p_sub)
                                )).scalar() or 0
                                if has_cards == 0:
                                    await save_cards_to_database(
                                        cards_data=p_cards,
                                        subject_slug=p_sub,
                                        phrase_title=p_title,
                                        user_id=user_id_str,
                                        db=db
                                    )
                                    print(f"[Bot] Автоматически залито {len(p_cards)} карточек для нового участника {user_id_str}")
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
    elif invite_enrolled:
        welcome_text = (
            "🎉 <b>Добро пожаловать в научный эксперимент Data Grinder!</b>\n\n"
            f"Инвайт-код <code>{invite_code_clean}</code> успешно подтвержден.\n"
            f"Вам назначен статус участника исследования:\n"
            f"• <b>Предмет:</b> Судоустройство (sudoustroystvo)\n"
            f"• <b>Дневной режим:</b> {settings.EXPERIMENT_DAILY_LIMIT} карточек\n"
            f"• <b>Фаза:</b> 1 (Экспериментальная изоляция FSRS)\n\n"
            "Нажмите кнопку ниже для запуска персональной учебной сессии:"
        )
    else:
        welcome_text = (
            "<b>[DATA GRINDER v1.0]</b> приветствует тебя.\n\n"
            "Интерфейс когнитивного заучивания и FSRS-интерливинга готов к работе. "
            "Нажми кнопку ниже для старта рабочей сессии."
        )

    await message.answer(welcome_text, reply_markup=markup, parse_mode="HTML")


# --- ПАНЕЛЬ УПРАВЛЕНИЯ ИССЛЕДОВАНИЕМ (TELEGRAM ADMIN PANEL) ---

async def get_admin_dashboard_data():
    async with AsyncSessionLocal() as db:
        sess_sample = (await db.execute(
            select(UserSession).filter(UserSession.is_experiment_participant == True).limit(1)
        )).scalar_one_or_none()
        current_phase = sess_sample.experiment_phase if sess_sample else 1
        
        part_count = (await db.execute(
            select(func.count(UserSession.id)).filter(UserSession.is_experiment_participant == True)
        )).scalar() or 0
        
        cards_count = (await db.execute(
            select(func.count(Card.id)).filter(Card.subject == "sudoustroystvo")
        )).scalar() or 0
        
        reviews_count = (await db.execute(select(func.count(ReviewLog.id)))).scalar() or 0
        
        outliers_count = (await db.execute(
            select(func.count(ReviewLog.id)).filter(ReviewLog.is_outlier == True)
        )).scalar() or 0
        
        pending_jobs = (await db.execute(
            select(func.count(GenerationJob.id)).filter(GenerationJob.status == "pending")
        )).scalar() or 0

        invites_active = (await db.execute(
            select(func.count(InviteCode.id)).filter(InviteCode.is_used == False)
        )).scalar() or 0
        
        return {
            "phase": current_phase,
            "participants": part_count,
            "cards": cards_count,
            "reviews": reviews_count,
            "outliers": outliers_count,
            "pending_jobs": pending_jobs,
            "invites_active": invites_active
        }

def build_admin_keyboard(phase: int) -> InlineKeyboardMarkup:
    phase_toggle = (
        InlineKeyboardButton(text="🔓 Переключить на Фазу 2", callback_data="admin_phase_2")
        if phase == 1 else
        InlineKeyboardButton(text="🔒 Переключить на Фазу 1", callback_data="admin_phase_1")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Участники (@)", callback_data="admin_participants"),
            InlineKeyboardButton(text="🎟 Инвайты", callback_data="admin_invites")
        ],
        [
            InlineKeyboardButton(text="📤 Экспорт моих карточек (JSON)", callback_data="admin_export_my_deck")
        ],
        [
            InlineKeyboardButton(text="📦 Раздача колоды (дозагрузка / сброс)", callback_data="admin_distribute_deck")
        ],
        [
            InlineKeyboardButton(text="📥 Датасет (CSV)", callback_data="admin_export_dataset"),
            InlineKeyboardButton(text="🤖 Телеметрия (CSV)", callback_data="admin_export_telemetry")
        ],
        [phase_toggle],
        [
            InlineKeyboardButton(text="🔄 Обновить сводку", callback_data="admin_refresh")
        ]
    ])

def render_admin_dashboard_text(d: dict) -> str:
    phase_str = "Фаза 1 (Изоляция колод, лимит 20 карт)" if d['phase'] == 1 else "Фаза 2 (Свободный режим, ночная нарезка)"
    return (
        "🛠 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ЭКСПЕРИМЕНТОМ</b>\n\n"
        f"🔬 <b>Текущий режим:</b> {phase_str}\n"
        f"👥 <b>Участников:</b> <code>{d['participants']}</code>\n"
        f"🎟 <b>Активных инвайтов:</b> <code>{d['invites_active']}</code>\n"
        f"🗂 <b>Карточек (судоустройство):</b> <code>{d['cards']}</code>\n"
        f"📝 <b>Повторений в логах:</b> <code>{d['reviews']}</code>\n"
        f"⚠️ <b>Выбросов (&lt;600мс / &gt;30с):</b> <code>{d['outliers']}</code>\n"
        f"⚡ <b>Очередь ночной нарезки:</b> <code>{d['pending_jobs']}</code> в ожидании\n\n"
        "<i>Выберите необходимое действие в меню ниже:</i>"
    )

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    token = args[1].strip() if len(args) > 1 else ""
    
    if token and token == settings.ADMIN_TOKEN:
        ADMIN_USERS.add(user_id)
        await message.answer("🔑 <b>Авторизация администратора успешно пройдена!</b>", parse_mode="HTML")
    elif not is_admin(user_id):
        await message.answer(
            "🔒 <b>Доступ запрещен.</b>\n\n"
            "Для входа в панель администратора отправьте команду с токеном:\n"
            "<code>/admin secret-admin-token</code>",
            parse_mode="HTML"
        )
        return
        
    data = await get_admin_dashboard_data()
    text_content = render_admin_dashboard_text(data)
    await message.answer(text_content, reply_markup=build_admin_keyboard(data["phase"]), parse_mode="HTML")

@dp.message(Command("export"))
async def cmd_export(message: types.Message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("🔒 Доступ разрешен только администраторам.")
        return

    user_id_str = str(user_id)
    async with AsyncSessionLocal() as db:
        stmt = select(Card).filter(Card.user_id == user_id_str).order_by(Card.id.asc())
        cards = (await db.execute(stmt)).scalars().all()
        if not cards:
            stmt_def = select(Card).filter(Card.user_id == "default_user").order_by(Card.id.asc())
            cards = (await db.execute(stmt_def)).scalars().all()

        if not cards:
            await message.answer(
                "ℹ️ В вашей базе пока нет карточек для экспорта.\n\n"
                "Создайте или импортируйте карточки в веб-приложении и отправьте команду /export повторно.",
                parse_mode="HTML"
            )
            return

        phrase_title = "Судоустройство: Основной курс"
        subject_slug = cards[0].subject if cards else "sudoustroystvo"

        p_stmt = select(Phrase.text).filter(Phrase.user_id == cards[0].user_id)
        found_title = (await db.execute(p_stmt)).scalar()
        if found_title:
            phrase_title = found_title

        payload = {
            "phrase_title": phrase_title,
            "subject_slug": subject_slug,
            "exported_at": datetime.utcnow().isoformat(),
            "total_cards": len(cards),
            "cards": [
                {
                    "text": c.text,
                    "secondary_text": c.secondary_text or "",
                    "translation": c.translation,
                    "example": c.example or "",
                    "mnemonic": c.mnemonic
                }
                for c in cards
            ]
        }

        save_preset_path = Path("app/static/presets/sudoustroystvo.json")
        save_preset_path.parent.mkdir(parents=True, exist_ok=True)
        save_preset_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    now_tag = datetime.utcnow().strftime('%Y%m%d_%H%M')
    file_obj = BufferedInputFile(json_bytes, filename=f"deck_{subject_slug}_{len(cards)}_cards_{now_tag}.json")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"➕ Дозагрузить всем ({len(cards)} карт, без сброса)", callback_data="admin_distribute_append")],
        [InlineKeyboardButton(text="⚠️ Сбросить и перезаписать всем", callback_data="admin_distribute_overwrite_confirm")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")]
    ])

    await message.answer_document(
        document=file_obj,
        caption=(
            f"📤 <b>Ваша колода карточек ({len(cards)} шт.) успешно выгружена!</b>\n\n"
            f"• <b>Предмет:</b> <code>{subject_slug}</code>\n"
            f"• <b>Тема:</b> «{phrase_title}»\n"
            f"• <b>Файл сохранен на сервере:</b> <code>app/static/presets/sudoustroystvo.json</code>\n\n"
            f"Вы можете сохранить этот JSON-файл к себе или сразу раздать его всем участникам кнопками ниже:"
        ),
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("admin_"))
async def handle_admin_callbacks(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_admin(user_id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    action = callback.data

    if action == "admin_refresh" or action == "admin_menu":
        data = await get_admin_dashboard_data()
        await callback.message.edit_text(
            render_admin_dashboard_text(data),
            reply_markup=build_admin_keyboard(data["phase"]),
            parse_mode="HTML"
        )
        await callback.answer("Сводка обновлена")

    elif action == "admin_participants":
        async with AsyncSessionLocal() as db:
            stmt = select(UserSession).filter(UserSession.is_experiment_participant == True).order_by(UserSession.id.asc())
            users = (await db.execute(stmt)).scalars().all()
            
            lines = ["👥 <b>УЧАСТНИКИ НАУЧНОГО ЭКСПЕРИМЕНТА:</b>\n"]
            for idx, u in enumerate(users, 1):
                uname = f"@{u.username}" if u.username else "<i>(без юзернейма)</i>"
                fname = f" — {u.full_name}" if u.full_name else ""
                
                # Считаем карточки и повторения
                c_cnt = (await db.execute(select(func.count(Card.id)).filter(Card.user_id == u.user_id))).scalar() or 0
                r_cnt = (await db.execute(select(func.count(ReviewLog.id)).filter(ReviewLog.user_id == u.user_id))).scalar() or 0
                
                lines.append(f"<b>{idx}. {uname}</b>{fname}\n   ID: <code>{u.telegram_id}</code> | Фаза: {u.experiment_phase} | Карт: {c_cnt} | Логов: {r_cnt}")
            
            if not users:
                lines.append("<i>Пока нет зарегистрированных участников. Создайте инвайт ниже.</i>")
                
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎟 Создать инвайт", callback_data="admin_create_invite")],
                [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="admin_menu")]
            ])
            await callback.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
            await callback.answer()

    elif action == "admin_invites":
        async with AsyncSessionLocal() as db:
            stmt = select(InviteCode).order_by(InviteCode.id.desc()).limit(15)
            invites = (await db.execute(stmt)).scalars().all()
            
            lines = ["🎟 <b>СИСТЕМА ИНВАЙТ-КОДОВ:</b>\n"]
            active = [i for i in invites if not i.is_used]
            used = [i for i in invites if i.is_used]
            
            if active:
                lines.append("🟢 <b>Активные (ожидают перехода):</b>")
                for a in active:
                    lines.append(f"• <code>{a.code}</code>")
                lines.append("")
            
            if used:
                lines.append("⚪ <b>Использованные:</b>")
                for u in used:
                    who = u.used_by_username or u.used_by_user_id or "участник"
                    lines.append(f"• <code>{u.code}</code> → {who}")
                lines.append("")
                
            if not invites:
                lines.append("<i>Инвайт-коды еще не создавались.</i>\n")
                
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать новый инвайт", callback_data="admin_create_invite")],
                [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="admin_menu")]
            ])
            await callback.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
            await callback.answer()

    elif action == "admin_create_invite":
        code = f"INV-{secrets.token_hex(4).upper()}"
        async with AsyncSessionLocal() as db:
            inv = InviteCode(code=code, created_by=str(callback.from_user.id))
            db.add(inv)
            await db.commit()
            
        bot_info = await bot.get_me()
        deep_link = f"https://t.me/{bot_info.username}?start=inv_{code}"
        
        text_resp = (
            f"🎟 <b>НОВЫЙ ИНВАЙТ СОЗДАН!</b>\n\n"
            f"Код: <code>{code}</code>\n\n"
            f"🔗 <b>Персональная ссылка для студента:</b>\n"
            f"<code>{deep_link}</code>\n\n"
            f"<i>Студенту достаточно кликнуть по ссылке и нажать Start. Он сразу будет добавлен в Фазу 1 исследования.</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Еще один инвайт", callback_data="admin_create_invite")],
            [InlineKeyboardButton(text="📋 К списку инвайтов", callback_data="admin_invites")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")]
        ])
        await callback.message.edit_text(text_resp, reply_markup=kb, parse_mode="HTML")
        await callback.answer("Инвайт создан!")

    elif action == "admin_export_dataset":
        await callback.answer("Генерирую датасет CSV...")
        async with AsyncSessionLocal() as db:
            query = text("""
                SELECT 
                    r.user_id,
                    r.id AS log_id,
                    r.card_id,
                    c.subject AS subject_id,
                    r.rating,
                    r.response_time,
                    COALESCE(r.is_outlier, 0) AS is_outlier,
                    COALESCE(r.is_cram, 0) AS is_cram,
                    ROUND(r.stability, 4) AS stability,
                    ROUND(r.difficulty, 4) AS difficulty,
                    r.elapsed_days,
                    r.scheduled_days,
                    COALESCE(d.mental_effort, '') AS mental_effort,
                    COALESCE(d.perceived_retention, '') AS perceived_retention,
                    COALESCE(d.true_retention, '') AS true_retention,
                    COALESCE(d.session_duration, '') AS session_duration,
                    r.review_time
                FROM review_logs r
                JOIN cards c ON r.card_id = c.id
                JOIN user_sessions u ON r.user_id = u.user_id
                LEFT JOIN daily_sessions d ON (
                    r.user_id = d.user_id 
                    AND date(r.review_time) = date(d.timestamp)
                )
                WHERE u.is_experiment_participant = 1
                ORDER BY r.review_time ASC
            """)
            result = await db.execute(query)
            rows = result.mappings().all()

        output = io.StringIO()
        output.write('\ufeff')
        fieldnames = [
            "user_id", "log_id", "card_id", "subject_id", "rating",
            "response_time", "is_outlier", "is_cram", "stability",
            "difficulty", "elapsed_days", "scheduled_days",
            "mental_effort", "perceived_retention", "true_retention",
            "session_duration", "review_time"
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))

        csv_bytes = output.getvalue().encode("utf-8-sig")
        now_tag = datetime.utcnow().strftime('%Y%m%d_%H%M')
        file_obj = BufferedInputFile(csv_bytes, filename=f"experiment_dataset_{now_tag}.csv")
        await callback.message.answer_document(
            document=file_obj,
            caption=f"📊 <b>Датасет научного эксперимента</b>\nЗаписей: {len(rows)}\nФормат: CSV (UTF-8 BOM для Excel/Pandas)",
            parse_mode="HTML"
        )

    elif action == "admin_export_telemetry":
        await callback.answer("Генерирую лог телеметрии...")
        async with AsyncSessionLocal() as db:
            stmt = select(AiTelemetryLog).order_by(AiTelemetryLog.created_at.desc())
            res = await db.execute(stmt)
            logs = res.scalars().all()

        output = io.StringIO()
        output.write('\ufeff')
        fieldnames = [
            "id", "job_id", "user_id", "model_requested", "model_resolved",
            "input_chars", "prompt_tokens", "completion_tokens", "cache_hit",
            "is_truncated", "repair_successful", "cards_generated", "duration_ms",
            "status", "error_message", "created_at"
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for l in logs:
            writer.writerow({
                "id": l.id,
                "job_id": l.job_id,
                "user_id": l.user_id,
                "model_requested": l.model_requested,
                "model_resolved": l.model_resolved,
                "input_chars": l.input_chars,
                "prompt_tokens": l.prompt_tokens,
                "completion_tokens": l.completion_tokens,
                "cache_hit": l.cache_hit,
                "is_truncated": l.is_truncated,
                "repair_successful": l.repair_successful,
                "cards_generated": l.cards_generated,
                "duration_ms": l.duration_ms,
                "status": l.status,
                "error_message": l.error_message,
                "created_at": l.created_at.isoformat() if l.created_at else ""
            })

        csv_bytes = output.getvalue().encode("utf-8-sig")
        now_tag = datetime.utcnow().strftime('%Y%m%d_%H%M')
        file_obj = BufferedInputFile(csv_bytes, filename=f"ai_telemetry_{now_tag}.csv")
        await callback.message.answer_document(
            document=file_obj,
            caption=f"🤖 <b>Инженерная телеметрия ИИ-шлюза</b>\nВызовов в базе: {len(logs)}\nФормат: CSV",
            parse_mode="HTML"
        )

    elif action == "admin_export_my_deck":
        await callback.answer("Выгружаю вашу колоду...")
        user_id_str = str(callback.from_user.id)
        async with AsyncSessionLocal() as db:
            # Ищем карточки текущего администратора
            stmt = select(Card).filter(Card.user_id == user_id_str).order_by(Card.id.asc())
            res = await db.execute(stmt)
            cards = res.scalars().all()

            # Фолбэк на default_user, если админ нарезал карточки в браузере вне Telegram
            if not cards:
                stmt_def = select(Card).filter(Card.user_id == "default_user").order_by(Card.id.asc())
                cards = (await db.execute(stmt_def)).scalars().all()

            if not cards:
                await callback.message.answer(
                    "ℹ️ В вашей личной базе пока нет карточек.\n\n"
                    "Создайте или импортируйте карточки в веб-приложении, после чего нажмите эту кнопку повторно.",
                    parse_mode="HTML"
                )
                return

            phrase_title = "Судоустройство: Основной курс"
            subject_slug = cards[0].subject if cards else "sudoustroystvo"

            p_stmt = select(Phrase.text).filter(Phrase.user_id == cards[0].user_id)
            found_title = (await db.execute(p_stmt)).scalar()
            if found_title:
                phrase_title = found_title

            payload = {
                "phrase_title": phrase_title,
                "subject_slug": subject_slug,
                "exported_at": datetime.utcnow().isoformat(),
                "total_cards": len(cards),
                "cards": [
                    {
                        "text": c.text,
                        "secondary_text": c.secondary_text or "",
                        "translation": c.translation,
                        "example": c.example or "",
                        "mnemonic": c.mnemonic
                    }
                    for c in cards
                ]
            }

            # Автоматически сохраняем на сервере как актуальный пресет
            save_preset_path = Path("app/static/presets/sudoustroystvo.json")
            save_preset_path.parent.mkdir(parents=True, exist_ok=True)
            save_preset_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        now_tag = datetime.utcnow().strftime('%Y%m%d_%H%M')
        file_obj = BufferedInputFile(json_bytes, filename=f"deck_{subject_slug}_{len(cards)}_cards_{now_tag}.json")

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"➕ Дозагрузить всем ({len(cards)} карт, без сброса)", callback_data="admin_distribute_append")],
            [InlineKeyboardButton(text="⚠️ Сбросить и перезаписать всем", callback_data="admin_distribute_overwrite_confirm")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")]
        ])

        await callback.message.answer_document(
            document=file_obj,
            caption=(
                f"📤 <b>Ваша колода карточек ({len(cards)} шт.) успешно выгружена!</b>\n\n"
                f"• <b>Предмет:</b> <code>{subject_slug}</code>\n"
                f"• <b>Тема:</b> «{phrase_title}»\n"
                f"• <b>Файл сохранен на сервере:</b> <code>app/static/presets/sudoustroystvo.json</code>\n\n"
                f"Вы можете сохранить этот JSON-файл к себе или сразу раздать его всем участникам кнопками ниже:"
            ),
            reply_markup=kb,
            parse_mode="HTML"
        )

    elif action == "admin_phase_1":
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(UserSession)
                .where(UserSession.is_experiment_participant == True)
                .values(experiment_phase=1)
            )
            await db.execute(
                update(UserSetting)
                .where(UserSetting.is_experiment_participant == True)
                .values(experiment_phase=1)
            )
            await db.commit()
        await callback.answer("🔒 Фаза 1 включена для всех участников!", show_alert=True)
        data = await get_admin_dashboard_data()
        await callback.message.edit_text(
            render_admin_dashboard_text(data),
            reply_markup=build_admin_keyboard(1),
            parse_mode="HTML"
        )

    elif action == "admin_distribute_deck":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Дозагрузить новые (сохранить прогресс)", callback_data="admin_distribute_append")],
            [InlineKeyboardButton(text="⚠️ Полный сброс и перезапись", callback_data="admin_distribute_overwrite_confirm")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")]
        ])
        await callback.message.edit_text(
            "📦 <b>РАЗДАЧА КАРТОЧЕК УЧАСТНИКАМ ЭКСПЕРИМЕНТА</b>\n\n"
            "Выберите способ применения эталонного пакета судоустройства:\n\n"
            "1. <b>Дозагрузить (Append)</b> — <i>РЕКОМЕНДУЕТСЯ</i>. Добавляет только новые карточки и обновляет формулировки существующих. Весь прогресс студентов (FSRS интервалы, стабильность, статистика повторений) полностью сохраняется.\n\n"
            "2. <b>Полный сброс (Overwrite)</b> — полностью удаляет текущие карточки и заново загружает колоду со сбросом прогресса.",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()

    elif action == "admin_distribute_append":
        await callback.answer("Синхронизирую и дозагружаю карточки...")
        preset_path = Path("app/static/presets/sudoustroystvo.json")
        if not preset_path.exists():
            await callback.message.answer("❌ Файл <code>app/static/presets/sudoustroystvo.json</code> не найден на сервере!", parse_mode="HTML")
            return

        try:
            content = json.loads(preset_path.read_text(encoding="utf-8"))
            cards_to_distribute = content.get("cards", [])
            phrase_title = content.get("phrase_title", "Судоустройство: Основной курс")
            subject_slug = content.get("subject_slug", "sudoustroystvo")
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка чтения файла карточек: {e}")
            return

        async with AsyncSessionLocal() as db:
            stmt = select(UserSession).filter(UserSession.is_experiment_participant == True)
            users = (await db.execute(stmt)).scalars().all()
            if not users:
                await callback.message.answer("⚠️ В базе пока нет зарегистрированных участников исследования!")
                return

            affected = 0
            total_created = 0
            total_updated = 0
            for u in users:
                created, updated, _, _ = await append_or_sync_cards_to_database(
                    cards_data=cards_to_distribute,
                    subject_slug=subject_slug,
                    phrase_title=phrase_title,
                    user_id=u.user_id,
                    db=db
                )
                total_created += created
                total_updated += updated
                affected += 1
            await db.commit()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")]
        ])
        await callback.message.edit_text(
            f"✅ <b>Колода успешно синхронизирована (Append)!</b>\n\n"
            f"• <b>Предмет:</b> {phrase_title}\n"
            f"• <b>Всего в эталонном пакете:</b> {len(cards_to_distribute)} шт.\n"
            f"• <b>Добавлено новых карточек:</b> {total_created} шт.\n"
            f"• <b>Обновлено формулировок:</b> {total_updated} шт.\n"
            f"• <b>Участников затронуто:</b> {affected} чел.\n\n"
            f"<i>💡 FSRS-метрики (интервалы, стабильность, история повторений) участников сохранены без изменений.</i>",
            reply_markup=kb,
            parse_mode="HTML"
        )

    elif action == "admin_distribute_overwrite_confirm":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="‼️ ДА, СБРОСИТЬ ПРОГРЕСС И ПЕРЕЗАПИСАТЬ", callback_data="admin_distribute_overwrite")],
            [InlineKeyboardButton(text="🔙 Отмена (Главное меню)", callback_data="admin_menu")]
        ])
        await callback.message.edit_text(
            "⚠️ <b>ВНИМАНИЕ: ОПАСНОЕ ДЕЙСТВИЕ</b>\n\n"
            "Вы собираетесь удалить все текущие карточки по судоустройству у всех участников исследования и создать их заново.\n\n"
            "Все интервалы повторений и история будут <b>безвозвратно удалены</b> для данного предмета.\n\n"
            "Вы уверены?",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()

    elif action == "admin_distribute_overwrite":
        await callback.answer("Сбрасываю и перезаписываю колоду...")
        preset_path = Path("app/static/presets/sudoustroystvo.json")
        if not preset_path.exists():
            await callback.message.answer("❌ Файл <code>app/static/presets/sudoustroystvo.json</code> не найден на сервере!", parse_mode="HTML")
            return

        try:
            content = json.loads(preset_path.read_text(encoding="utf-8"))
            cards_to_distribute = content.get("cards", [])
            phrase_title = content.get("phrase_title", "Судоустройство: Основной курс")
            subject_slug = content.get("subject_slug", "sudoustroystvo")
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка чтения файла карточек: {e}")
            return

        async with AsyncSessionLocal() as db:
            stmt = select(UserSession).filter(UserSession.is_experiment_participant == True)
            users = (await db.execute(stmt)).scalars().all()
            if not users:
                await callback.message.answer("⚠️ В базе пока нет зарегистрированных участников исследования!")
                return

            affected = 0
            total_created = 0
            for u in users:
                await db.execute(delete(Card).filter(Card.user_id == u.user_id, Card.subject == subject_slug))
                await db.execute(delete(Phrase).filter(Phrase.user_id == u.user_id, Phrase.subject == subject_slug))
                await db.commit()

                created, _, _ = await save_cards_to_database(
                    cards_data=cards_to_distribute,
                    subject_slug=subject_slug,
                    phrase_title=phrase_title,
                    user_id=u.user_id,
                    db=db
                )
                total_created += created
                affected += 1
            await db.commit()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")]
        ])
        await callback.message.edit_text(
            f"✅ <b>Колода полностью перезаписана (Overwrite)!</b>\n\n"
            f"• <b>Тема:</b> {phrase_title}\n"
            f"• <b>Создано карточек:</b> {total_created} шт.\n"
            f"• <b>Участников:</b> {affected} чел.\n\n"
            f"<i>Все студенты начинают с нулевой очереди FSRS.</i>",
            reply_markup=kb,
            parse_mode="HTML"
        )

    elif action == "admin_phase_2":
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(UserSession)
                .where(UserSession.is_experiment_participant == True)
                .values(experiment_phase=2)
            )
            await db.execute(
                update(UserSetting)
                .where(UserSetting.is_experiment_participant == True)
                .values(experiment_phase=2)
            )
            await db.commit()
        await callback.answer("🔓 Фаза 2 (свободный режим) включена!", show_alert=True)
        data = await get_admin_dashboard_data()
        await callback.message.edit_text(
            render_admin_dashboard_text(data),
            reply_markup=build_admin_keyboard(2),
            parse_mode="HTML"
        )


@dp.message(F.document)
async def handle_admin_document_upload(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    doc = message.document
    if not doc.file_name or not doc.file_name.endswith(".json"):
        await message.answer("ℹ️ Для загрузки базы карточек отправьте файл в формате <code>.json</code> (например, <code>sudoustroystvo.json</code>).", parse_mode="HTML")
        return

    try:
        file = await bot.get_file(doc.file_id)
        file_bytes = await bot.download_file(file.file_path)
        content_text = file_bytes.read().decode("utf-8")
        parsed_json = json.loads(content_text)

        cards = parsed_json.get("cards", [])
        if not cards:
            await message.answer("❌ В переданном JSON-файле не найден массив <code>cards</code>.", parse_mode="HTML")
            return

        # Сохраняем в app/static/presets/sudoustroystvo.json
        save_path = Path("app/static/presets/sudoustroystvo.json")
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(parsed_json, ensure_ascii=False, indent=2), encoding="utf-8")

        phrase_title = parsed_json.get("phrase_title", "Судоустройство: Основной курс")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"➕ Дозагрузить ({len(cards)} карт, без сброса)", callback_data="admin_distribute_append")],
            [InlineKeyboardButton(text="⚠️ Сбросить и перезаписать всем", callback_data="admin_distribute_overwrite_confirm")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")]
        ])

        await message.answer(
            f"📥 <b>Файл карточек успешно принят и сохранен!</b>\n\n"
            f"• <b>Тема:</b> «{phrase_title}»\n"
            f"• <b>Количество карточек:</b> {len(cards)}\n"
            f"• <b>Файл на сервере:</b> <code>app/static/presets/sudoustroystvo.json</code>\n\n"
            f"Все новые студенты будут автоматически получать этот набор при переходе по инвайту.\n"
            f"Чтобы загрузить его уже зарегистрированным участникам, выберите действие:",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as err:
        await message.answer(f"❌ Ошибка обработки JSON файла: {err}")


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
from app.services.generation_worker import is_deepseek_offpeak, generation_worker_loop

night_grind_worker = generation_worker_loop


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