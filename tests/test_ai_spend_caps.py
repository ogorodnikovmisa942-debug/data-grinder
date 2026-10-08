"""Жёсткие потолки на платный ИИ вне нарезки: мнемоника (сутки), ИИ-оценка (выключена), разбор билетов (частичный результат,
потолок плана и суток). Лимиты действуют независимо от QUOTAS_ENABLED."""
import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

import test_exam_prep as tep
from app.core.config import settings
from app.database.models import AiTelemetryLog, Card, ExamPlan, ExamTicket, Phrase, utc_now
from app.database.session import AsyncSessionLocal
from app.services import exam_prep as ep
from app.services import quota
from app.services.ai_gateway import exam_matcher
from app.services.ai_gateway.client import LLMCallError

USER = "test_spend_caps_user"
SUBJECT = "test_spend_caps_subject"


async def _clean():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AiTelemetryLog).where(AiTelemetryLog.user_id == USER))
        await db.execute(delete(Card).where(Card.user_id == USER))
        await db.execute(delete(Phrase).where(Phrase.user_id == USER))
        await db.commit()


async def _log(job_id: str, cost: float = 0.0001):
    from app.services.generation_worker import _record_path_calls
    await _record_path_calls(job_id, USER, [{"label": "x#1", "cost_usd": cost, "prompt_tokens": 10, "completion_tokens": 5,
                                             "cache_hit_tokens": 0, "duration_ms": 1, "finish_reason": "stop", "model": "deepseek-flash"}])


# --- мнемоника: не больше N в сутки, расход виден в телеметрии --------------------------------------------------------------------------

def test_mnemonic_daily_limit_applies_even_with_quotas_off(monkeypatch):
    monkeypatch.setattr(settings, "QUOTAS_ENABLED", False)
    monkeypatch.setattr(settings, "MNEMONIC_PER_DAY", 3)

    async def scenario():
        await _clean()
        try:
            async with AsyncSessionLocal() as db:
                await quota.enforce_mnemonic(db, USER)
            for i in range(3):
                await _log(f"{quota.MNEMONIC_PREFIX}{i}")
            async with AsyncSessionLocal() as db:
                with pytest.raises(HTTPException) as e:
                    await quota.enforce_mnemonic(db, USER)
                assert e.value.status_code == 429 and "не больше 3 в сутки" in e.value.detail
        finally:
            await _clean()

    asyncio.run(scenario())


def test_mnemonic_endpoint_records_the_spend_and_stops_at_the_limit(monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    monkeypatch.setattr(settings, "MNEMONIC_PER_DAY", 2)

    async def fake_mnemonic(text, translation, subject, preference="visual", calls=None):
        calls.append({"label": "mnemonic#1", "cost_usd": 0.0002, "prompt_tokens": 150, "completion_tokens": 60, "cache_hit_tokens": 0,
                      "duration_ms": 5, "finish_reason": "stop", "model": "deepseek-flash", "error": None})
        return {"keyword": "ключ", "verbal_cue": "связка"}

    async def setup():
        await _clean()
        async with AsyncSessionLocal() as db:
            ph = Phrase(text="п", subject=SUBJECT, user_id=USER)
            db.add(ph)
            await db.flush()
            card = Card(phrase_id=ph.id, user_id=USER, subject=SUBJECT, text="Q?", translation="A.", state=0, next_review=utc_now())
            db.add(card)
            await db.commit()
            return card.id

    card_id = asyncio.run(setup())
    try:
        with patch("app.services.generation_worker.claim_next_pending_job", return_value=None), \
                patch("app.api.endpoints.cards.regenerate_card_mnemonic", new=fake_mnemonic):
            client = TestClient(app)
            h = {"X-User-Id": USER}
            url = f"/api/management/cards/{card_id}/regenerate_mnemonic"
            assert client.post(url, json={"preference": "visual"}, headers=h).status_code == 200
            assert client.post(url, json={"preference": "visual"}, headers=h).status_code == 200
            third = client.post(url, json={"preference": "visual"}, headers=h)
            assert third.status_code == 429 and "в сутки" in third.json()["detail"]

        async def spent():
            async with AsyncSessionLocal() as db:
                return await quota.ai_events_today(db, USER, quota.MNEMONIC_PREFIX), await quota.ai_spend_today(db, USER, quota.MNEMONIC_PREFIX)
        events, cost = asyncio.run(spent())
        assert events == 2 and round(cost, 4) == 0.0004
    finally:
        asyncio.run(_clean())


# --- ИИ-оценка выключена, проверка по тезисам работает ----------------------------------------------------------------------------

def test_ai_check_is_off_by_default_and_costs_nothing():
    assert settings.AI_CHECK_ENABLED is False

    async def scenario():
        from test_exam_extras import _make_plan
        nodes, plan_id = await _make_plan()
        try:
            async with AsyncSessionLocal() as db:
                ticket = (await db.execute(select(ExamTicket).where(ExamTicket.plan_id == plan_id, ExamTicket.status == "ok"))).scalar_one()
                with patch("app.services.ai_gateway.client.call_deepseek", new=AsyncMock()) as m:
                    with pytest.raises(PermissionError):
                        await ep.ai_check_ticket(db, tep.USER, ticket.id, "Любой ответ")
                    assert m.call_count == 0
                # проверка по тезисам (без ИИ) на месте
                for key in ("base", "topic"):
                    from app.services.knowledge_path import complete_lesson
                    await complete_lesson(db, tep.USER, nodes[key], 0)
                await db.commit()
                await tep._learn(db, nodes["topic"])
                draw = await ep.simulator_draw(db, tep.USER, tep.SUBJECT)
                assert draw["ai_check"] is False
                assert (await ep.simulator_check(db, tep.USER, draw["ticket_id"], "Тема — это главное в курсе"))["percent"] == 100
        finally:
            await tep._cleanup()

    asyncio.run(scenario())


# --- разбор билетов: оплаченное не пропадает, потолок расхода ---------------------------------------------------------------------

NODES = [{"id": 1, "key": "base", "tier": 0, "name": "Основа", "summary": ""}]


def _ok_answer(numbers):
    return {"tickets": [{"i": i, "nodes": ["@base"], "found": True, "points": [{"t": f"тезис {i}", "v": [], "w": 1}]} for i in numbers]}


def _fake_api(failing_numbers=(), cost=0.01):
    """Подставная модель: пачка, где есть «плохой» билет, не разбирается (оплаченный непригодный ответ), остальные — да."""
    seen = []

    async def call(prompt, system_instruction=None, **kwargs):
        numbers = [int(line.split(".")[0]) for line in prompt.split("[TICKETS]\n")[1].split("[END TICKETS]")[0].strip().splitlines()]
        seen.append(numbers)
        if set(numbers) & set(failing_numbers):
            raise LLMCallError("битый JSON", {"cost_usd": cost, "prompt_tokens": 10, "completion_tokens": 5})
        return _ok_answer(numbers), {"cost_usd": cost, "prompt_tokens": 10, "completion_tokens": 5, "cache_hit_tokens": 0,
                                     "finish_reason": "stop", "model_resolved": "deepseek-flash"}
    return call, seen


def test_a_failed_batch_does_not_throw_away_the_paid_ones():
    call, seen = _fake_api(failing_numbers={15})
    questions = [f"Вопрос {i}" for i in range(1, 26)]            # 3 пачки по 10, 10, 5

    async def scenario():
        calls = []
        with patch.object(exam_matcher, "_get_call_deepseek", return_value=call):
            return await exam_matcher.match_tickets(NODES, {}, questions, calls), calls

    results, calls = asyncio.run(scenario())
    assert [r is None for r in results] == [False] * 10 + [True] * 10 + [False] * 5      # пачка 11–20 не удалась дважды
    assert seen[0] == list(range(1, 11))                                                 # первая пачка идёт одна (прогрев кэша)
    assert sum(1 for s in seen if 15 in s) == 2 and len(calls) == 4 and all(r["found"] for r in results if r)


def test_every_batch_failing_is_still_an_error():
    call, _ = _fake_api(failing_numbers=set(range(1, 30)))

    async def scenario():
        with patch.object(exam_matcher, "_get_call_deepseek", return_value=call):
            await exam_matcher.match_tickets(NODES, {}, ["a", "b"], [])

    with pytest.raises(exam_matcher.ExamMatchError):
        asyncio.run(scenario())


def test_the_cost_ceiling_stops_new_batches_but_keeps_what_was_bought():
    call, seen = _fake_api(cost=0.01)

    async def scenario():
        calls = []
        with patch.object(exam_matcher, "_get_call_deepseek", return_value=call):
            return await exam_matcher.match_tickets(NODES, {}, [f"В{i}" for i in range(1, 26)], calls, max_cost_usd=0.005), calls

    results, calls = asyncio.run(scenario())
    assert len(calls) == 1 and [r is None for r in results] == [False] * 10 + [True] * 15      # купили первую пачку, потолок достигнут


def test_the_ceiling_alone_raises_the_budget_error():
    call, _ = _fake_api()

    async def scenario():
        with patch.object(exam_matcher, "_get_call_deepseek", return_value=call):
            await exam_matcher.match_tickets(NODES, {}, ["a"], [], max_cost_usd=0.0)

    with pytest.raises(exam_matcher.ExamBudgetExceeded):
        asyncio.run(scenario())


def test_partial_result_is_saved_and_the_retry_pays_only_for_the_rest(monkeypatch):
    sent = []

    async def first(nodes, cards_by_node, questions, calls, **kwargs):
        sent.append(list(questions))
        calls.append({"label": "exam#1.1", "cost_usd": 0.004, "prompt_tokens": 1, "cache_hit_tokens": 0, "completion_tokens": 1,
                      "duration_ms": 1, "finish_reason": "stop", "model": "deepseek-flash"})
        return [{"nodes": ["topic"], "found": True, "points": [{"text": "Тема — главное", "variants": [], "weight": 1.0}]}, None]

    async def second(nodes, cards_by_node, questions, calls, **kwargs):
        sent.append(list(questions))
        calls.append({"label": "exam#1.2", "cost_usd": 0.002, "prompt_tokens": 1, "cache_hit_tokens": 0, "completion_tokens": 1,
                      "duration_ms": 1, "finish_reason": "stop", "model": "deepseek-flash"})
        return [{"nodes": [], "found": False, "points": []}]

    async def scenario():
        await tep._build()
        try:
            async with AsyncSessionLocal() as db:
                plan = await ep.create_plan(db, tep.USER, tep.SUBJECT, "Билеты", date.today() + timedelta(days=10), [
                    {"question": "Расскажите о теме", "answer": ""}, {"question": "Вопрос не из книги", "answer": ""}])
                plan_id = plan.id
            with patch("app.services.ai_gateway.exam_matcher.match_tickets", side_effect=first):
                await ep.run_plan_matching(plan_id)
            async with AsyncSessionLocal() as db:
                plan = (await db.execute(select(ExamPlan).where(ExamPlan.id == plan_id))).scalar_one()
                tickets = (await db.execute(select(ExamTicket).where(ExamTicket.plan_id == plan_id).order_by(ExamTicket.order_idx))).scalars().all()
                assert plan.status == "failed" and "Разобрано 1 из 2" in plan.error
                assert [t.status for t in tickets] == ["ok", "pending"] and tickets[0].card_id
            with patch("app.services.ai_gateway.exam_matcher.match_tickets", side_effect=second):
                await ep.run_plan_matching(plan_id)
            async with AsyncSessionLocal() as db:
                plan = (await db.execute(select(ExamPlan).where(ExamPlan.id == plan_id))).scalar_one()
                tickets = (await db.execute(select(ExamTicket).where(ExamTicket.plan_id == plan_id).order_by(ExamTicket.order_idx))).scalars().all()
                assert plan.status == "ready" and [t.status for t in tickets] == ["ok", "missing"]
                assert round(plan.cost_usd, 4) == 0.006
            assert sent == [["Расскажите о теме", "Вопрос не из книги"], ["Вопрос не из книги"]]      # во второй раз — только неразобранный
        finally:
            await tep._cleanup()

    asyncio.run(scenario())


def test_an_exhausted_plan_ceiling_makes_no_ai_call(monkeypatch):
    async def scenario():
        await tep._build()
        try:
            async with AsyncSessionLocal() as db:
                plan = await ep.create_plan(db, tep.USER, tep.SUBJECT, "Билеты", date.today() + timedelta(days=10),
                                            [{"question": "Расскажите о теме", "answer": ""}])
                plan.cost_usd = 1.0                                           # заведомо выше потолка плана
                await db.commit()
                plan_id = plan.id
            boom = AsyncMock(side_effect=AssertionError("ИИ вызван при исчерпанном потолке"))
            with patch("app.services.ai_gateway.exam_matcher.match_tickets", new=boom):
                await ep.run_plan_matching(plan_id)
            async with AsyncSessionLocal() as db:
                plan = (await db.execute(select(ExamPlan).where(ExamPlan.id == plan_id))).scalar_one()
                assert plan.status == "failed" and "Лимит расходов" in plan.error and boom.call_count == 0
        finally:
            await tep._cleanup()

    asyncio.run(scenario())


def test_daily_exam_budget_blocks_new_plans_and_retries(monkeypatch):
    monkeypatch.setattr(settings, "EXAM_DAILY_BUDGET_USD", 0.05)

    async def scenario():
        await _clean()
        try:
            async with AsyncSessionLocal() as db:
                await quota.enforce_exam_budget(db, USER)                       # пока потрачено 0 — можно
            await _log(f"{quota.EXAM_PREFIX}1", cost=0.05 * quota.peak_factor())
            async with AsyncSessionLocal() as db:
                with pytest.raises(HTTPException) as e:
                    await quota.enforce_exam_budget(db, USER)
                assert e.value.status_code == 429 and "лимит разбора билетов" in e.value.detail
        finally:
            await _clean()

    asyncio.run(scenario())
