"""ИИ-оценка смысла ответа на билет: разбор ответа модели, платный тариф и месячная квота, расход в телеметрии."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select

import test_exam_prep as tep
from app.core.config import settings
from app.database.models import AiTelemetryLog, ExamTicket
from app.database.session import AsyncSessionLocal
from app.services import exam_prep as ep
from app.services import quota
from app.services.ai_gateway import answer_judge as aj

POINTS = ["Система общеобязательных норм", "Охраняется государством"]


def test_the_judge_prompt_is_static_and_the_task_lists_the_points():
    assert "{" not in aj.JUDGE_SYSTEM_PROMPT.split("OUTPUT:")[0]
    task = aj.build_judge_task("Понятие права", POINTS, "  Право — это нормы  ")
    assert task.startswith("TICKET: Понятие права\nREFERENCE POINTS:\n1. Система") and "STUDENT ANSWER:\nПраво — это нормы\n" in task


def test_judgement_is_normalized_and_aligned_to_the_reference_wording():
    raw = {"score": "72.4", "covered": ["система общеобязательных норм", "система общеобязательных норм"], "partial": [],
           "missed": ["Охраняется государством", ""], "wrong": ["Сказано, что право создают суды — это не так"], "advice": "  Повтори\n про государство. "}
    out = aj.normalize_judgement(raw, POINTS)
    assert out == {"score": 72, "covered": ["Система общеобязательных норм"], "partial": [], "missed": ["Охраняется государством"],
                   "wrong": ["Сказано, что право создают суды — это не так"], "advice": "Повтори про государство."}
    assert aj.normalize_judgement({"score": 500}, POINTS)["score"] == 100
    for bad in ([], {"score": "много"}, {"covered": []}):
        with pytest.raises(ValueError):
            aj.normalize_judgement(bad, POINTS)


async def _fake_call(user_prompt, system_instruction, **kwargs):
    return ({"score": 80, "covered": POINTS[:1], "partial": [], "missed": POINTS[1:], "wrong": [], "advice": "Повтори роль государства."},
            {"cost_usd": 0.0004, "prompt_tokens": 600, "completion_tokens": 90, "cache_hit_tokens": 0, "model_resolved": "deepseek-flash",
             "finish_reason": "stop"})


def _scenario(plan: str, quotas_on: bool, monkeypatch, runs: int = 1):
    monkeypatch.setattr(settings, "QUOTAS_ENABLED", quotas_on)
    monkeypatch.setattr(settings, "AI_CHECK_ENABLED", True)          # по умолчанию ИИ-оценка выключена (см. test_ai_spend_caps)

    async def run():
        nodes, plan_id = await _make_plan()
        try:
            async with AsyncSessionLocal() as db:
                if plan == "paid":
                    await quota.set_plan(db, tep.USER, "paid", 30)
                ticket = (await db.execute(select(ExamTicket).where(ExamTicket.plan_id == plan_id, ExamTicket.status == "ok"))).scalar_one()
                out = []
                with patch("app.services.ai_gateway.client.call_deepseek", new=AsyncMock(side_effect=_fake_call)) as m:
                    for _ in range(runs):
                        try:
                            out.append(await ep.ai_check_ticket(db, tep.USER, ticket.id, "Право — это нормы, которые защищает государство"))
                        except HTTPException as e:
                            out.append(e)
                spent = (await db.execute(select(func.count(AiTelemetryLog.id)).where(
                    AiTelemetryLog.user_id == tep.USER, AiTelemetryLog.job_id.like("aicheck:%")))).scalar()
                return out, spent, m.call_count
        finally:
            await tep._cleanup()
            async with AsyncSessionLocal() as db:
                from app.database.models import UserSetting
                await db.execute(delete(UserSetting).where(UserSetting.user_id == tep.USER))
                await db.commit()

    return asyncio.run(run())


async def _make_plan():
    from test_exam_extras import _make_plan as mk
    return await mk()


def test_a_paid_user_gets_the_judgement_and_the_spend_is_recorded(monkeypatch):
    out, spent, calls = _scenario("paid", True, monkeypatch)
    r = out[0]
    assert r["score"] == 80 and r["missed"] == POINTS[1:] and r["checks_left"] == settings.PAID_AI_CHECKS_PER_MONTH - 1
    assert "нормы" in r["reference"].lower() or r["reference"]
    assert spent == 1 and calls == 1


def test_a_free_user_is_asked_to_upgrade_and_nothing_is_spent(monkeypatch):
    out, spent, calls = _scenario("free", True, monkeypatch)
    assert isinstance(out[0], HTTPException) and out[0].status_code == 402 and "платном тарифе" in out[0].detail["message"]
    assert spent == 0 and calls == 0


def test_the_monthly_limit_stops_further_checks(monkeypatch):
    monkeypatch.setattr(settings, "PAID_AI_CHECKS_PER_MONTH", 2)
    out, spent, calls = _scenario("paid", True, monkeypatch, runs=3)
    assert [type(x).__name__ for x in out] == ["dict", "dict", "HTTPException"] and out[1]["checks_left"] == 0
    assert "использовали все 2" in out[2].detail["message"] and spent == 2 and calls == 2


def test_with_quotas_off_everyone_can_check_without_a_counter(monkeypatch):
    out, spent, calls = _scenario("free", False, monkeypatch)
    assert out[0]["score"] == 80 and out[0]["checks_left"] is None and spent == 1


def test_an_empty_answer_or_unknown_ticket_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "QUOTAS_ENABLED", False)
    monkeypatch.setattr(settings, "AI_CHECK_ENABLED", True)

    async def run():
        nodes, plan_id = await _make_plan()
        try:
            async with AsyncSessionLocal() as db:
                ticket = (await db.execute(select(ExamTicket).where(ExamTicket.plan_id == plan_id, ExamTicket.status == "ok"))).scalar_one()
                with pytest.raises(ValueError):
                    await ep.ai_check_ticket(db, tep.USER, ticket.id, "   ")
                with pytest.raises(LookupError):
                    await ep.ai_check_ticket(db, tep.USER, 99999999, "ответ")
        finally:
            await tep._cleanup()

    asyncio.run(run())
