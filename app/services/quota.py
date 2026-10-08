"""
Тарифы и месячные квоты на ИИ. Платит пользователь за объём ИИ-работы (книги, разбор билетов), а не за открытие функций:
всё, что считает код (повторения, практика, проверка письменных ответов, план экзамена, Anki), доступно всем.

Расход берётся из ai_telemetry_logs (там уже пишется стоимость каждого вызова по пользователю). Пока QUOTAS_ENABLED выключено,
ничего не ограничивается; оплаты в продукте нет, тариф «платный» выставляет администратор (POST /api/admin/users/plan).
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.config import settings
from app.database.models import AiTelemetryLog, ExamPlan, GenerationJob, UserSetting, utc_now

PLANS = ("free", "paid")


def limits_for(plan: str) -> dict:
    if plan == "paid":
        return {"plan": "paid", "book_chars": settings.PAID_BOOK_CHARS, "books_per_month": settings.PAID_BOOKS_PER_MONTH,
                "monthly_ai_usd": settings.PAID_MONTHLY_AI_USD, "instant": True, "rematch": True, "exam_plans_per_subject": None}
    return {"plan": "free", "book_chars": settings.FREE_BOOK_CHARS, "books_per_month": settings.FREE_BOOKS_PER_MONTH,
            "monthly_ai_usd": settings.FREE_MONTHLY_AI_USD, "instant": False, "rematch": False,
            "exam_plans_per_subject": settings.FREE_EXAM_PLANS_PER_SUBJECT}


def month_start(now: datetime | None = None) -> datetime:
    now = now or utc_now()
    return datetime(now.year, now.month, 1)


# --- Жёсткие потолки на платный ИИ вне нарезки (не зависят от QUOTAS_ENABLED; админ и dev без лимитов) ---------------------------------
MNEMONIC_PREFIX = "mnemonic:"
EXAM_PREFIX = "exam:"


def _is_free_of_caps(user_id: str) -> bool:
    from app.services.card_db_sync import is_admin_or_dev
    return is_admin_or_dev(user_id)


def peak_factor() -> float:
    """Потолки заданы в ценах вне пика; в пик DeepSeek та же работа вдвое дороже, потолок тоже (как Budget в нарезке)."""
    from app.services.ai_gateway.client import is_deepseek_offpeak_now
    return 1.0 if is_deepseek_offpeak_now() else 2.0


async def ai_events_today(db, user_id: str, prefix: str) -> int:
    """Сколько раз сегодня (сутки пользователя) пользователь запускал функцию с этим префиксом задачи в телеметрии."""
    from app.core.timeutil import user_day_start
    since = await user_day_start(db, user_id)
    return int((await db.execute(
        select(func.count(AiTelemetryLog.id)).where(
            AiTelemetryLog.user_id == user_id, AiTelemetryLog.job_id.like(prefix + "%"), AiTelemetryLog.created_at >= since)
    )).scalar() or 0)


async def ai_spend_today(db, user_id: str, prefix: str) -> float:
    from app.core.timeutil import user_day_start
    since = await user_day_start(db, user_id)
    return float((await db.execute(
        select(func.coalesce(func.sum(AiTelemetryLog.cost_usd), 0.0)).where(
            AiTelemetryLog.user_id == user_id, AiTelemetryLog.job_id.like(prefix + "%"), AiTelemetryLog.created_at >= since)
    )).scalar() or 0.0)


async def enforce_mnemonic(db, user_id: str) -> None:
    """«Мнемоника» — не больше MNEMONIC_PER_DAY нажатий в сутки на пользователя."""
    if _is_free_of_caps(user_id):
        return
    if await ai_events_today(db, user_id, MNEMONIC_PREFIX) >= settings.MNEMONIC_PER_DAY:
        raise HTTPException(status_code=429, detail=f"Мнемоника: не больше {settings.MNEMONIC_PER_DAY} в сутки. Завтра лимит обновится.")


def exam_plan_cap() -> float:
    return settings.EXAM_PLAN_BUDGET_USD * peak_factor()


async def enforce_exam_budget(db, user_id: str) -> None:
    """Разбор билетов (создание плана, «повторить», автопоиск): сумма за сутки на пользователя не выше EXAM_DAILY_BUDGET_USD."""
    if _is_free_of_caps(user_id):
        return
    if await ai_spend_today(db, user_id, EXAM_PREFIX) >= settings.EXAM_DAILY_BUDGET_USD * peak_factor():
        raise HTTPException(status_code=429, detail="Сегодня лимит разбора билетов исчерпан. Попробуйте завтра: уже разобранные билеты сохранены.")


async def get_plan(db, user_id: str) -> str:
    row = (await db.execute(select(UserSetting.plan, UserSetting.plan_until).where(UserSetting.user_id == user_id))).first()
    if row and row[0] == "paid" and (row[1] is None or row[1] > utc_now()):
        return "paid"
    return "free"


async def usage(db, user_id: str) -> dict:
    since = month_start()
    spent = (await db.execute(
        select(func.coalesce(func.sum(AiTelemetryLog.cost_usd), 0.0)).where(AiTelemetryLog.user_id == user_id, AiTelemetryLog.created_at >= since)
    )).scalar() or 0.0
    books = (await db.execute(
        select(func.count(GenerationJob.id)).where(
            GenerationJob.user_id == user_id, GenerationJob.created_at >= since, GenerationJob.status.notin_(("failed", "cancelled")))
    )).scalar() or 0
    return {"month_spend_usd": round(float(spent), 4), "books_this_month": int(books)}


def _blocked(message: str, code: str = "quota") -> HTTPException:
    return HTTPException(status_code=402, detail={"code": code, "message": message})


def _bypass(user_id: str) -> bool:
    from app.services.card_db_sync import is_admin_or_dev
    return not settings.QUOTAS_ENABLED or is_admin_or_dev(user_id)


async def enforce_new_job(db, user_id: str, chars: int, is_deferred: bool) -> bool:
    """Проверка перед постановкой материала в работу. Возвращает, можно ли обработать сразу (платный тариф) или только в ночной очереди
    со скидкой (бесплатный). Нарушение лимита — ответ 402 с понятным текстом."""
    if _bypass(user_id):
        return True
    plan = await get_plan(db, user_id)
    lim = limits_for(plan)
    use = await usage(db, user_id)
    if chars > lim["book_chars"]:
        raise _blocked(f"Материал слишком большой для вашего тарифа ({chars:,} знаков, максимум {lim['book_chars']:,}). "
                       "Разбейте его на части или перейдите на платный тариф.".replace(",", " "))
    if use["books_this_month"] >= lim["books_per_month"]:
        raise _blocked(f"В этом месяце вы уже загрузили {use['books_this_month']} материал(ов): лимит тарифа — {lim['books_per_month']}.")
    if use["month_spend_usd"] >= lim["monthly_ai_usd"]:
        raise _blocked("Месячный лимит работы ИИ по вашему тарифу исчерпан. Он обновится в начале следующего месяца.")
    return bool(lim["instant"])


async def enforce_exam_plan(db, user_id: str, subject: str) -> None:
    """Бесплатно разбор билетов один раз на предмет (отчёт о покрытии — главный крючок продукта); дальше — платный тариф."""
    if _bypass(user_id):
        return
    lim = limits_for(await get_plan(db, user_id))
    cap = lim["exam_plans_per_subject"]
    if cap is None:
        return
    made = (await db.execute(select(func.count(ExamPlan.id)).where(ExamPlan.user_id == user_id, ExamPlan.subject == subject))).scalar() or 0
    if made >= cap:
        raise _blocked("Разбор билетов по этому предмету на бесплатном тарифе доступен один раз. Новые разборы — на платном тарифе.")
    use = await usage(db, user_id)
    if use["month_spend_usd"] >= lim["monthly_ai_usd"]:
        raise _blocked("Месячный лимит работы ИИ по вашему тарифу исчерпан.")


AI_CHECK_PREFIX = "aicheck:"


async def ai_checks_this_month(db, user_id: str) -> int:
    """Сколько раз за месяц запрашивалась ИИ-оценка смысла ответа (по телеметрии: задача «aicheck:<билет>»)."""
    return int((await db.execute(
        select(func.count(AiTelemetryLog.id)).where(
            AiTelemetryLog.user_id == user_id, AiTelemetryLog.job_id.like(AI_CHECK_PREFIX + "%"), AiTelemetryLog.created_at >= month_start())
    )).scalar() or 0)


async def enforce_ai_check(db, user_id: str) -> int | None:
    """ИИ-оценка смысла ответа — только платный тариф, PAID_AI_CHECKS_PER_MONTH в месяц. Возвращает, сколько проверок осталось (None — без лимита)."""
    if _bypass(user_id):
        return None
    if await get_plan(db, user_id) != "paid":
        raise _blocked("ИИ-оценка смысла ответа доступна на платном тарифе. Проверка по тезисам (кнопка «Сдать ответ») бесплатна.")
    left = settings.PAID_AI_CHECKS_PER_MONTH - await ai_checks_this_month(db, user_id)
    if left <= 0:
        raise _blocked(f"В этом месяце вы использовали все {settings.PAID_AI_CHECKS_PER_MONTH} ИИ-оценок. Проверка по тезисам по-прежнему бесплатна.")
    return left - 1


async def allows_rematch(db, user_id: str) -> bool:
    """Повторный поиск недостающих билетов после нового материала — платная возможность (стоит запрос к ИИ)."""
    if _bypass(user_id):
        return True
    return limits_for(await get_plan(db, user_id))["rematch"]


async def overview(db, user_id: str) -> dict:
    """Для экрана «Тариф»: что у пользователя, лимиты и сколько уже потрачено в этом месяце."""
    plan = await get_plan(db, user_id)
    lim = limits_for(plan)
    use = await usage(db, user_id)
    return {"enabled": settings.QUOTAS_ENABLED, "plan": plan, "limits": lim, "usage": use,
            "left": {"books": max(0, lim["books_per_month"] - use["books_this_month"]),
                     "ai_usd": round(max(0.0, lim["monthly_ai_usd"] - use["month_spend_usd"]), 4)}}


async def set_plan(db, user_id: str, plan: str, days: int | None) -> dict:
    from datetime import timedelta
    if plan not in PLANS:
        raise ValueError("Тариф: free или paid")
    row = (await db.execute(select(UserSetting).where(UserSetting.user_id == user_id))).scalar_one_or_none()
    if not row:
        row = UserSetting(user_id=user_id)
        db.add(row)
    row.plan = plan
    row.plan_until = (utc_now() + timedelta(days=days)) if (plan == "paid" and days) else None
    await db.commit()
    return {"user_id": user_id, "plan": plan, "plan_until": row.plan_until.isoformat() if row.plan_until else None}
