# app/bot/keyboards.py
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.core.config import settings


def build_admin_keyboard(phase: int, ai_provider: str | None = None, ai_model: str | None = None) -> InlineKeyboardMarkup:
    model = ai_model or (ai_provider if ai_provider in ("deepseek-flash", "deepseek-chat", "deepseek-reasoner") else settings.DEEPSEEK_MODEL) or "deepseek-flash"
    phase_toggle = (
        InlineKeyboardButton(text="🔓 Переключить на Фазу 2", callback_data="admin_phase_2")
        if phase == 1 else
        InlineKeyboardButton(text="🔒 Переключить на Фазу 1", callback_data="admin_phase_1")
    )
    next_model = "deepseek-chat" if model == "deepseek-flash" else ("deepseek-reasoner" if model == "deepseek-chat" else "deepseek-flash")
    ai_toggle = InlineKeyboardButton(text=f"🤖 Модель: {model} ➔ {next_model}", callback_data="admin_ai_model_cycle")
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Все пользователи", callback_data="admin_users"),
            InlineKeyboardButton(text="🔬 Участники", callback_data="admin_participants")
        ],
        [
            InlineKeyboardButton(text="🎟 Инвайты", callback_data="admin_invites"),
            InlineKeyboardButton(text="📤 Моя колода (JSON)", callback_data="admin_export_my_deck")
        ],
        [
            InlineKeyboardButton(text="📦 Раздача колоды (выбор курса)", callback_data="admin_distribute_deck")
        ],
        [
            InlineKeyboardButton(text="📊 Датасет логов (CSV)", callback_data="admin_export_dataset"),
            InlineKeyboardButton(text="👥 База юзеров (CSV)", callback_data="admin_export_users_csv")
        ],
        [
            InlineKeyboardButton(text="🤖 Телеметрия ИИ (CSV)", callback_data="admin_export_telemetry")
        ],
        [ai_toggle],
        [phase_toggle],
        [
            InlineKeyboardButton(text="🏁 Завершить эксперимент (освободить всех)", callback_data="admin_finish_experiment")
        ],
        [
            InlineKeyboardButton(text="🕊 Освободить меня от эксперимента", callback_data="admin_free_me")
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить сводку", callback_data="admin_refresh")
        ]
    ])


def render_admin_dashboard_text(d: dict) -> str:
    phase_str = f"Фаза 1 (Изоляция колод, лимит {settings.EXPERIMENT_DAILY_LIMIT} карт)" if d['phase'] == 1 else "Фаза 2 (Свободный режим, ночная нарезка)"
    model_name = d.get('ai_model') or settings.DEEPSEEK_MODEL or "deepseek-flash"
    key_badge = "🔑 Ключ: OK" if d.get('has_key') else "⚠️ Ключ: НЕ ЗАДАН (.env)"
    total_u = d.get('total_users', d.get('participants', 0))
    part_u = d.get('participants', 0)
    return (
        "🛠 <b>ПАНЕЛЬ УПРАВЛЕНИЯ СИСТЕМОЙ</b>\n\n"
        f"🔬 <b>Текущий режим:</b> {phase_str}\n"
        f"🤖 <b>Активный ИИ:</b> <code>DeepSeek</code> ({model_name}) [{key_badge}]\n"
        f"👥 <b>Пользователей всего:</b> <code>{total_u}</code> (в эксперименте: <code>{part_u}</code>)\n"
        f"🎟 <b>Активных инвайтов:</b> <code>{d['invites_active']}</code>\n"
        f"🗂 <b>Всего карточек в базе:</b> <code>{d['cards']}</code>\n"
        f"📝 <b>Повторений в логах:</b> <code>{d['reviews']}</code>\n"
        f"⚠️ <b>Выбросов (&lt;600мс / &gt;30с):</b> <code>{d['outliers']}</code>\n"
        f"⚡ <b>Очередь генерации:</b> <code>{d['pending_jobs']}</code> задач\n\n"
        "<i>Выберите необходимое действие в меню ниже:</i>"
    )
