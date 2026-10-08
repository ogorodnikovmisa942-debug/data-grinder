"""Тарифы и месячные квоты на ИИ: лимиты книги, число книг и расход в месяц, один разбор билетов на предмет, платные возможности."""
import asyncio
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from app.core.config import settings
from app.database.models import AiTelemetryLog, ExamPlan, GenerationJob, UserSetting, utc_now
from app.database.session import AsyncSessionLocal
from app.services import quota

USER = "test_quota_user"


async def _clean():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(GenerationJob).where(GenerationJob.user_id == USER))
        await db.execute(delete(AiTelemetryLog).where(AiTelemetryLog.user_id == USER))
        await db.execute(delete(ExamPlan).where(ExamPlan.user_id == USER))
        await db.execute(delete(UserSetting).where(UserSetting.user_id == USER))
        await db.commit()


def run(coro_fn):
    async def wrapper():
        await _clean()
        try:
            return await coro_fn()
        finally:
            await _clean()
    return asyncio.run(wrapper())


async def _job(db, status="completed"):
    db.add(GenerationJob(user_id=USER, subject="s", theme="t", raw_text="x" * 20, status=status))
    await db.commit()


async def _spend(db, usd):
    db.add(AiTelemetryLog(user_id=USER, model_requested="m", model_resolved="m", cost_usd=usd))
    await db.commit()


async def _blocked(coro):
    try:
        await coro
    except HTTPException as e:
        assert e.status_code == 402 and e.detail["code"] == "quota"
        return e.detail["message"]
    return None


def test_nothing_is_limited_while_quotas_are_off(monkeypatch):
    monkeypatch.setattr(settings, "QUOTAS_ENABLED", False)

    async def scenario():
        async with AsyncSessionLocal() as db:
            await _spend(db, 50.0)
            assert await quota.enforce_new_job(db, USER, 5_000_000, False) is True
            await quota.enforce_exam_plan(db, USER, "s")
            assert await quota.allows_rematch(db, USER) is True

    run(scenario)


def test_free_plan_has_a_small_book_one_per_month_and_night_queue_only(monkeypatch):
    monkeypatch.setattr(settings, "QUOTAS_ENABLED", True)

    async def scenario():
        async with AsyncSessionLocal() as db:
            assert await quota.get_plan(db, USER) == "free"
            assert await quota.enforce_new_job(db, USER, 40_000, False) is False             # можно, но только ночью со скидкой
            assert "слишком большой" in await _blocked(quota.enforce_new_job(db, USER, 60_000, False))
            await _job(db)
            assert "уже загрузили 1" in await _blocked(quota.enforce_new_job(db, USER, 10_000, False))
            await _job(db, status="failed")                                                   # неудачные в счёт не идут
            assert (await quota.usage(db, USER))["books_this_month"] == 1

    run(scenario)


def test_monthly_ai_spend_is_capped_and_only_this_month_counts(monkeypatch):
    monkeypatch.setattr(settings, "QUOTAS_ENABLED", True)

    async def scenario():
        async with AsyncSessionLocal() as db:
            db.add(AiTelemetryLog(user_id=USER, model_requested="m", model_resolved="m", cost_usd=9.0, created_at=utc_now() - timedelta(days=45)))
            await db.commit()
            assert (await quota.usage(db, USER))["month_spend_usd"] == 0.0                    # прошлые месяцы не считаются
            await _spend(db, settings.FREE_MONTHLY_AI_USD + 0.01)
            assert "лимит работы ИИ" in await _blocked(quota.enforce_new_job(db, USER, 1000, False))

    run(scenario)


def test_paid_plan_lifts_the_limits_and_expires(monkeypatch):
    monkeypatch.setattr(settings, "QUOTAS_ENABLED", True)

    async def scenario():
        async with AsyncSessionLocal() as db:
            info = await quota.set_plan(db, USER, "paid", 30)
            assert info["plan"] == "paid" and info["plan_until"]
            assert await quota.get_plan(db, USER) == "paid"
            await _job(db)
            assert await quota.enforce_new_job(db, USER, 800_000, False) is True                # крупная книга, вторая за месяц, сразу
            assert await quota.allows_rematch(db, USER) is True
            await quota.enforce_exam_plan(db, USER, "s")
            await db.execute(UserSetting.__table__.update().where(UserSetting.user_id == USER).values(plan_until=utc_now() - timedelta(days=1)))
            await db.commit()
            assert await quota.get_plan(db, USER) == "free"                                      # срок вышел — снова бесплатный
            try:
                await quota.set_plan(db, USER, "vip", None)
                assert False
            except ValueError:
                pass

    run(scenario)


def test_free_exam_report_once_per_subject_and_rematch_is_paid(monkeypatch):
    monkeypatch.setattr(settings, "QUOTAS_ENABLED", True)

    async def scenario():
        async with AsyncSessionLocal() as db:
            await quota.enforce_exam_plan(db, USER, "право")                                    # первый разбор — бесплатно
            db.add(ExamPlan(user_id=USER, subject="право", title="Билеты", exam_date=date.today() + timedelta(days=9), status="ready"))
            await db.commit()
            assert "один раз" in await _blocked(quota.enforce_exam_plan(db, USER, "право"))
            await quota.enforce_exam_plan(db, USER, "история")                                  # другой предмет — свой первый разбор
            assert await quota.allows_rematch(db, USER) is False

    run(scenario)


def test_usage_and_admin_plan_api(monkeypatch):
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    from main import app
    monkeypatch.setattr(settings, "QUOTAS_ENABLED", True)
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "test-admin-token")

    async def clean():
        await _clean()

    asyncio.run(clean())
    try:
        with patch("app.services.generation_worker.claim_next_pending_job", return_value=None):
            client = TestClient(app)
            h = {"X-User-Id": USER}
            u = client.get("/api/usage", headers=h).json()
            assert u["enabled"] and u["plan"] == "free" and u["left"]["books"] == settings.FREE_BOOKS_PER_MONTH
            assert client.post("/api/admin/users/plan", json={"user_id": USER, "plan": "paid", "days": 30}).status_code == 403
            ok = client.post("/api/admin/users/plan", json={"user_id": USER, "plan": "paid", "days": 30}, headers={"X-Admin-Token": "test-admin-token"})
            assert ok.status_code == 200 and ok.json()["plan"] == "paid"
            assert client.get("/api/usage", headers=h).json()["limits"]["books_per_month"] == settings.PAID_BOOKS_PER_MONTH
            bad = client.post("/api/admin/users/plan", json={"user_id": USER, "plan": "vip"}, headers={"X-Admin-Token": "test-admin-token"})
            assert bad.status_code == 400
    finally:
        asyncio.run(clean())
