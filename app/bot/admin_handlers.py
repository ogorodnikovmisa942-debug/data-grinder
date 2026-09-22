# app/bot/admin_handlers.py
import json
import csv
import io
import secrets
from pathlib import Path
from datetime import datetime

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, CallbackQuery
)
from sqlalchemy import select, update, func, text, delete

from app.database.session import AsyncSessionLocal
from app.database.models import (
    UserSession, GenerationJob, UserSetting, InviteCode, Card, ReviewLog, AiTelemetryLog, Phrase, utc_now
)
from app.services.card_db_sync import save_cards_to_database, append_or_sync_cards_to_database
from app.core.config import settings
from app.bot.keyboards import build_admin_keyboard, render_admin_dashboard_text

admin_router = Router(name="admin_router")

# --- АДМИНИСТРАТИВНЫЕ ПРАВА И СЕССИЯ ---
ADMIN_USERS: set[int] = set()
if getattr(settings, "ADMIN_TELEGRAM_ID", None):
    for aid in str(settings.ADMIN_TELEGRAM_ID).split(","):
        aid = aid.strip()
        if aid.isdigit():
            ADMIN_USERS.add(int(aid))


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USERS


async def get_admin_dashboard_data() -> dict:
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
        
        ai_provider = "deepseek"
        ai_model = settings.DEEPSEEK_MODEL or "deepseek-flash"
        has_key = bool(settings.DEEPSEEK_API_KEY)
        
        return {
            "phase": current_phase,
            "participants": part_count,
            "cards": cards_count,
            "reviews": reviews_count,
            "outliers": outliers_count,
            "pending_jobs": pending_jobs,
            "invites_active": invites_active,
            "ai_provider": ai_provider,
            "ai_model": ai_model,
            "has_key": has_key
        }


@admin_router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    token = args[1].strip() if len(args) > 1 else ""
    
    # 1. Проверяем токен из команды
    if token and token == settings.ADMIN_TOKEN:
        ADMIN_USERS.add(user_id)
        await message.answer("🔑 <b>Авторизация администратора успешно пройдена!</b>", parse_mode="HTML")
    # 2. Проверяем, совпадает ли user_id с ADMIN_TELEGRAM_ID из настроек
    elif getattr(settings, "ADMIN_TELEGRAM_ID", None) and str(user_id) in [x.strip() for x in str(settings.ADMIN_TELEGRAM_ID).split(",") if x.strip()]:
        ADMIN_USERS.add(user_id)
    # 3. Если не админ, выводим понятную инструкцию с кликабельным токеном и его ID
    elif not is_admin(user_id):
        curr_token = settings.ADMIN_TOKEN or "secret-admin-token"
        await message.answer(
            "🔒 <b>Панель администратора Data Grinder</b>\n\n"
            "Доступ ограничен. Для входа скопируйте и отправьте команду:\n"
            f"<code>/admin {curr_token}</code>\n\n"
            f"<i>Ваш Telegram ID: <code>{user_id}</code>\n"
            f"(Вы также можете прописать его в файле .env: ADMIN_TELEGRAM_ID=\"{user_id}\")</i>",
            parse_mode="HTML"
        )
        return
        
    data = await get_admin_dashboard_data()
    text_content = render_admin_dashboard_text(data)
    await message.answer(text_content, reply_markup=build_admin_keyboard(data["phase"], data["ai_provider"]), parse_mode="HTML")


@admin_router.message(Command("free", "unlock"))
async def cmd_free(message: types.Message):
    user_id_str = str(message.from_user.id)
    async with AsyncSessionLocal() as db:
        sess_stmt = select(UserSession).filter(UserSession.telegram_id == user_id_str)
        sess = (await db.execute(sess_stmt)).scalar_one_or_none()

        set_stmt = select(UserSetting).filter(UserSetting.user_id == user_id_str)
        user_set = (await db.execute(set_stmt)).scalar_one_or_none()

        if sess:
            sess.is_experiment_participant = False
            sess.experiment_phase = 2
        if user_set:
            user_set.is_experiment_participant = False
            user_set.experiment_phase = 2

        await db.commit()

    await message.answer(
        "🕊 <b>Вы полностью освобождены от ограничений эксперимента!</b>\n\n"
        "• Статус участника: <b>Отключен</b>\n"
        "• Режим: <b>Фаза 2 (Свободный доступ)</b>\n"
        "• Теперь вам снова доступны: удаление карточек, создание и импорт новых предметов, свободное изменение лимитов и сброс прогресса.",
        parse_mode="HTML"
    )


@admin_router.message(Command("export"))
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
            "exported_at": utc_now().isoformat(),
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

        presets = list(Path("app/static/presets").glob("*.json"))
        preset_filename = f"{subject_slug}.json" if subject_slug else (presets[0].name if presets else "sudoustroystvo.json")
        save_preset_path = Path("app/static/presets") / preset_filename
        save_preset_path.parent.mkdir(parents=True, exist_ok=True)
        save_preset_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    now_tag = utc_now().strftime('%Y%m%d_%H%M')
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
            f"• <b>Файл сохранен на сервере:</b> <code>{save_preset_path.as_posix()}</code>\n\n"
            f"Вы можете сохранить этот JSON-файл к себе или сразу раздать его всем участникам кнопками ниже:"
        ),
        reply_markup=kb,
        parse_mode="HTML"
    )


@admin_router.callback_query(F.data.startswith("admin_"))
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
            reply_markup=build_admin_keyboard(data["phase"], data["ai_provider"]),
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
            
        bot_info = await callback.bot.get_me()
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
        now_tag = utc_now().strftime('%Y%m%d_%H%M')
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
        now_tag = utc_now().strftime('%Y%m%d_%H%M')
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
            stmt = select(Card).filter(Card.user_id == user_id_str).order_by(Card.id.asc())
            res = await db.execute(stmt)
            cards = res.scalars().all()

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
                "exported_at": utc_now().isoformat(),
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

            presets = list(Path("app/static/presets").glob("*.json"))
            preset_filename = f"{subject_slug}.json" if subject_slug else (presets[0].name if presets else "sudoustroystvo.json")
            save_preset_path = Path("app/static/presets") / preset_filename
            save_preset_path.parent.mkdir(parents=True, exist_ok=True)
            save_preset_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        now_tag = utc_now().strftime('%Y%m%d_%H%M')
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
                f"• <b>Файл сохранен на сервере:</b> <code>{save_preset_path.as_posix()}</code>\n\n"
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
        try:
            await callback.message.edit_text(
                render_admin_dashboard_text(data),
                reply_markup=build_admin_keyboard(1, data["ai_provider"]),
                parse_mode="HTML"
            )
        except Exception:
            pass

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
        presets = list(Path("app/static/presets").glob("*.json"))
        preset_path = presets[0] if presets else Path("app/static/presets/sudoustroystvo.json")
        if not preset_path.exists():
            await callback.message.answer(f"❌ Файл <code>{preset_path.as_posix()}</code> не найден на сервере!", parse_mode="HTML")
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
        presets = list(Path("app/static/presets").glob("*.json"))
        preset_path = presets[0] if presets else Path("app/static/presets/sudoustroystvo.json")
        if not preset_path.exists():
            await callback.message.answer(f"❌ Файл <code>{preset_path.as_posix()}</code> не найден на сервере!", parse_mode="HTML")
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
            reply_markup=build_admin_keyboard(2, data["ai_provider"]),
            parse_mode="HTML"
        )

    elif action == "admin_ai_model_cycle":
        from app.api.endpoints.admin import set_active_ai_provider
        curr = settings.DEEPSEEK_MODEL or "deepseek-flash"
        next_model = "deepseek-chat" if curr == "deepseek-flash" else ("deepseek-reasoner" if curr == "deepseek-chat" else "deepseek-flash")
        set_active_ai_provider("deepseek", next_model)
        data = await get_admin_dashboard_data()
        try:
            await callback.message.edit_text(
                render_admin_dashboard_text(data),
                reply_markup=build_admin_keyboard(data["phase"], ai_model=data["ai_model"]),
                parse_mode="HTML"
            )
        except Exception:
            pass
        key_warn = "" if bool(settings.DEEPSEEK_API_KEY) else "\n⚠️ Внимание: DEEPSEEK_API_KEY не задан в .env!"
        await callback.answer(f"🤖 Модель DeepSeek переключена на {next_model}!{key_warn}", show_alert=True)

    elif action in ("admin_ai_mimo", "admin_ai_deepseek"):
        from app.api.endpoints.admin import set_active_ai_provider
        set_active_ai_provider("deepseek", "deepseek-flash")
        data = await get_admin_dashboard_data()
        try:
            await callback.message.edit_text(
                render_admin_dashboard_text(data),
                reply_markup=build_admin_keyboard(data["phase"], ai_model=data["ai_model"]),
                parse_mode="HTML"
            )
        except Exception:
            pass
        await callback.answer(f"🤖 Активная модель: DeepSeek ({settings.DEEPSEEK_MODEL})", show_alert=True)

    elif action == "admin_free_me":
        user_id_str = str(callback.from_user.id)
        async with AsyncSessionLocal() as db:
            sess_stmt = select(UserSession).filter(UserSession.telegram_id == user_id_str)
            sess = (await db.execute(sess_stmt)).scalar_one_or_none()

            set_stmt = select(UserSetting).filter(UserSetting.user_id == user_id_str)
            user_set = (await db.execute(set_stmt)).scalar_one_or_none()

            if sess:
                sess.is_experiment_participant = False
                sess.experiment_phase = 2
            if user_set:
                user_set.is_experiment_participant = False
                user_set.experiment_phase = 2
            await db.commit()

        await callback.answer("🕊 Ваш аккаунт успешно освобожден от ограничений эксперимента!", show_alert=True)
        data = await get_admin_dashboard_data()
        try:
            await callback.message.edit_text(
                render_admin_dashboard_text(data),
                reply_markup=build_admin_keyboard(data["phase"], data["ai_provider"], data["ai_model"]),
                parse_mode="HTML"
            )
        except Exception:
            pass


@admin_router.message(F.document)
async def handle_admin_document_upload(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    doc = message.document
    if not doc.file_name or not doc.file_name.endswith(".json"):
        await message.answer("ℹ️ Для загрузки базы карточек отправьте файл в формате <code>.json</code>.", parse_mode="HTML")
        return

    try:
        file = await message.bot.get_file(doc.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        content_text = file_bytes.read().decode("utf-8")
        parsed_json = json.loads(content_text)

        cards = parsed_json.get("cards", [])
        if not cards:
            await message.answer("❌ В переданном JSON-файле не найден массив <code>cards</code>.", parse_mode="HTML")
            return

        subject_slug = parsed_json.get("subject_slug") or (doc.file_name.rsplit(".", 1)[0] if doc.file_name else "sudoustroystvo")
        save_path = Path(f"app/static/presets/{subject_slug}.json")
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(parsed_json, ensure_ascii=False, indent=2), encoding="utf-8")

        phrase_title = parsed_json.get("phrase_title", subject_slug.replace("_", " ").title())
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"➕ Дозагрузить ({len(cards)} карт, без сброса)", callback_data="admin_distribute_append")],
            [InlineKeyboardButton(text="⚠️ Сбросить и перезаписать всем", callback_data="admin_distribute_overwrite_confirm")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_menu")]
        ])

        await message.answer(
            f"📥 <b>Файл карточек успешно принят и сохранен!</b>\n\n"
            f"• <b>Тема:</b> «{phrase_title}»\n"
            f"• <b>Количество карточек:</b> {len(cards)}\n"
            f"• <b>Файл на сервере:</b> <code>{save_path.as_posix()}</code>\n\n"
            f"Все новые студенты будут автоматически получать этот набор при переходе по инвайту.\n"
            f"Чтобы загрузить его уже зарегистрированным участникам, выберите действие:",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as err:
        await message.answer(f"❌ Ошибка обработки JSON файла: {err}")
