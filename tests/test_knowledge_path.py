import asyncio
from unittest.mock import patch

from sqlalchemy import select, delete, func

from app.database.session import AsyncSessionLocal
from app.database.models import (
    GenerationJob, Card, KnowledgeNode, KnowledgeEdge, AiTelemetryLog, ReviewLog, utc_now,
)
from app.services.ai_gateway.path_builder import normalize_map
from app.services.generation_worker import process_generation_job
from app.services.knowledge_path import get_path_state, complete_lesson, wipe_subject

USER = "test_knowledge_path_user"
SUBJECT = "test_kp_subject"

LESSON = {"screens": [{"say": "a", "emo": "talk", "focus": []}] * 3, "check": []}


def card(text, layer=1):
    return {
        "text": text, "secondary_text": "Тест | Раздел", "translation": "Ответ.", "example": "",
        "initial_difficulty_tier": "medium", "layer": layer, "answer_type": "term",
        "distractors": ["Неверно 1.", "Неверно 2.", "Неверно 3."],
    }


def fake_result():
    path_map = normalize_map({"title": "Тестовый курс", "domain": "law", "nodes": [
        {"key": "base", "name": "Основа", "tier": 0, "order": 1},
        {"key": "topic", "name": "Тема", "tier": 1, "order": 2},
        {"key": "sub", "name": "Подтема", "tier": 2, "parent": "topic", "order": 3},
    ], "edges": [{"from": "base", "to": "topic", "relation": "leads_to", "label": "ведёт к"}]})
    packs = {
        "base": {"lesson": LESSON, "cards": [card(f"Вопрос основы {i}?", 0) for i in range(5)]},
        "topic": {"lesson": LESSON, "cards": [card("Вопрос темы?")]},
        "sub": {"lesson": None, "cards": [card("Вопрос подтемы?", 2)]},
    }
    return {"map": path_map, "packs": packs, "missing_nodes": [], "calls": [], "cost_usd": 0.0}


async def fake_build(text, subject, calls=None):
    calls.append({"label": "map#1", "cost_usd": 0.0123, "prompt_tokens": 100, "cache_hit_tokens": 0,
                  "completion_tokens": 10, "duration_ms": 5, "finish_reason": "stop", "model": "deepseek-flash"})
    return fake_result()


async def create_job() -> int:
    async with AsyncSessionLocal() as db:
        job = GenerationJob(user_id=USER, subject=SUBJECT, theme="Тест", raw_text="Текст учебника " * 10, status="processing")
        db.add(job)
        await db.commit()
        return job.id


async def cleanup():
    async with AsyncSessionLocal() as db:
        await wipe_subject(db, USER, SUBJECT)
        await db.execute(delete(GenerationJob).where(GenerationJob.user_id == USER))
        await db.execute(delete(AiTelemetryLog).where(AiTelemetryLog.user_id == USER))
        await db.commit()


def test_worker_saves_path_and_replaces_on_reupload():
    async def scenario():
        await cleanup()
        try:
            with patch("app.services.generation_worker.build_learning_path", side_effect=fake_build):
                job_id = await create_job()
                await process_generation_job(job_id, is_offpeak=True)
                # Повторная загрузка того же предмета заменяет его, а не дублирует
                await process_generation_job(await create_job(), is_offpeak=True)

            async with AsyncSessionLocal() as db:
                job = (await db.execute(select(GenerationJob).where(GenerationJob.id == job_id))).scalar_one()
                assert job.status == "completed" and job.cards_count == 7 and job.theme == "Тестовый курс"
                nodes = (await db.execute(select(KnowledgeNode).where(KnowledgeNode.user_id == USER))).scalars().all()
                assert sorted(n.node_key for n in nodes) == ["base", "sub", "topic"]
                assert {n.node_key: n.lesson_status for n in nodes} == {"base": "ready", "topic": "ready", "sub": "failed"}
                assert (await db.scalar(select(func.count(KnowledgeEdge.id)).where(KnowledgeEdge.user_id == USER))) == 1
                cards = (await db.execute(select(Card).where(Card.user_id == USER).order_by(Card.topological_rank))).scalars().all()
                assert len(cards) == 7
                by_id = {n.id: n.node_key for n in nodes}
                assert [by_id[c.node_id] for c in cards] == ["base"] * 5 + ["topic", "sub"]
                assert cards[0].distractors == ["Неверно 1.", "Неверно 2.", "Неверно 3."] and cards[0].answer_type == "term"
                cost = await db.scalar(select(func.sum(AiTelemetryLog.cost_usd)).where(AiTelemetryLog.user_id == USER))
                assert round(cost, 4) == round(0.0123 * 2, 4)
        finally:
            await cleanup()

    asyncio.run(scenario())


def test_tiers_unlock_after_lesson_and_answered_cards():
    async def scenario():
        await cleanup()
        try:
            with patch("app.services.generation_worker.build_learning_path", side_effect=fake_build):
                await process_generation_job(await create_job(), is_offpeak=True)

            async def statuses():
                async with AsyncSessionLocal() as db:
                    state = await get_path_state(db, USER, SUBJECT)
                    return {n["key"]: n["status"] for n in state["nodes"]}

            assert await statuses() == {"base": "open", "topic": "locked", "sub": "locked"}

            async with AsyncSessionLocal() as db:
                base = (await db.execute(select(KnowledgeNode).where(
                    KnowledgeNode.user_id == USER, KnowledgeNode.node_key == "base"))).scalar_one()
                await complete_lesson(db, USER, base.id, checkpoint_score=1)
                await db.commit()
            assert (await statuses())["base"] == "lesson_done"

            # «Не вспомнил» освоением не считается
            async with AsyncSessionLocal() as db:
                cards = (await db.execute(select(Card).where(Card.node_id == base.id).order_by(Card.id))).scalars().all()
                for c in cards[:4]:
                    db.add(ReviewLog(card_id=c.id, user_id=USER, rating=1, review_time=utc_now(), state=0))
                await db.commit()
            assert (await statuses())["base"] == "lesson_done"

            # 4 из 5 карточек хоть раз вспомнены верно = 80% → основа освоена, тема открывается в тот же день
            async with AsyncSessionLocal() as db:
                for c in cards[:4]:
                    db.add(ReviewLog(card_id=c.id, user_id=USER, rating=3, review_time=utc_now(), state=1))
                await db.commit()
            assert await statuses() == {"base": "mastered", "topic": "open", "sub": "locked"}
        finally:
            await cleanup()

    asyncio.run(scenario())


def test_path_api_and_train_gating():
    from fastapi.testclient import TestClient
    from main import app

    async def setup():
        await cleanup()
        with patch("app.services.generation_worker.build_learning_path", side_effect=fake_build):
            await process_generation_job(await create_job(), is_offpeak=True)

    asyncio.run(setup())
    try:
        with patch("app.services.generation_worker.claim_next_pending_job", return_value=None):
            client = TestClient(app)
            h = {"X-User-Id": USER}
            state = client.get(f"/api/path/{SUBJECT}", headers=h).json()
            ids = {n["key"]: n["id"] for n in state["nodes"]}
            assert len(state["edges"]) == 1

            # Закрытый узел: ни урока, ни отметки
            assert client.get(f"/api/path/node/{ids['topic']}/lesson", headers=h).status_code == 403
            assert client.post(f"/api/path/node/{ids['topic']}/complete", json={}, headers=h).status_code == 403
            # Чужой пользователь узел не видит
            assert client.get(f"/api/path/node/{ids['base']}/lesson", headers={"X-User-Id": "someone_else"}).status_code == 404

            def new_cards():
                r = client.get(f"/api/session?subject={SUBJECT}&mode=new", headers=h)
                assert r.status_code == 200
                return r.json()

            assert new_cards() == []  # урок не пройден — новых карточек нет
            lesson = client.get(f"/api/path/node/{ids['base']}/lesson", headers=h).json()
            assert len(lesson["lesson"]["screens"]) == 3
            assert client.post(f"/api/path/node/{ids['base']}/complete", json={"checkpoint_score": 1}, headers=h).status_code == 200
            cards = new_cards()
            assert cards and all(c["organ_slug"] == "base" for c in cards)
    finally:
        asyncio.run(cleanup())




async def build_path() -> dict:
    await cleanup()
    with patch("app.services.generation_worker.build_learning_path", side_effect=fake_build):
        await process_generation_job(await create_job(), is_offpeak=True)
    async with AsyncSessionLocal() as db:
        return {n.node_key: n.id for n in (await db.execute(
            select(KnowledgeNode).where(KnowledgeNode.user_id == USER))).scalars().all()}


async def learn_node_cards(db, node_id: int, count: int | None = None) -> None:
    """Карточки узла впервые выучены сегодня и ушли в Review на завтра."""
    from datetime import timedelta
    cards = (await db.execute(select(Card).where(Card.node_id == node_id).order_by(Card.id))).scalars().all()
    for c in cards[:count]:
        c.state, c.next_review = 2, utc_now() + timedelta(days=5)
        db.add(ReviewLog(card_id=c.id, user_id=USER, rating=3, review_time=utc_now(), state=0))
    await db.commit()


async def set_daily_limit(limit: int | None) -> None:
    from app.database.models import UserSetting
    async with AsyncSessionLocal() as db:
        await db.execute(delete(UserSetting).where(UserSetting.user_id == USER))
        if limit is not None:
            db.add(UserSetting(user_id=USER, daily_limit=limit))
        await db.commit()


def test_practice_only_from_learned_cards():
    """Практика — тест только по выученному: урок пройден и карточка уже хоть раз выучена."""
    from app.services.practice_service import generate_practice_session
    from app.database.models import PracticeItem

    async def scenario():
        nodes = await build_path()
        try:
            async with AsyncSessionLocal() as db:
                assert await generate_practice_session(USER, SUBJECT, count=20, db=db) == []  # уроков нет — практики нет

                for key in ("base", "topic"):
                    await complete_lesson(db, USER, nodes[key], 1)
                await db.commit()
                # Урок пройден, но карточки ещё не выучены — угадывать нечего
                assert await generate_practice_session(USER, SUBJECT, count=20, db=db) == []

                await learn_node_cards(db, nodes["base"], 3)
                items = await generate_practice_session(USER, SUBJECT, count=20, db=db)
                assert len(items) == 3
                stored = (await db.execute(select(PracticeItem).where(PracticeItem.user_id == USER))).scalars().all()
                assert {it.prompt for it in stored} == {f"Вопрос основы {i}?" for i in range(3)}
                for it in stored:
                    assert it.correct_answer == "Ответ."
                    if it.item_type == "open":
                        assert it.options == []  # открытый вопрос: ответ вводится вручную
                    else:
                        assert set(it.options) == {"Ответ.", "Неверно 1.", "Неверно 2.", "Неверно 3."}
                # ~20% открытых: из 3 заданий одно
                assert sum(it.item_type == "open" for it in stored) == 1
                assert all(it.item_type != "relation" for it in stored)
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(PracticeItem).where(PracticeItem.user_id == USER))
                await db.commit()
            await cleanup()

    asyncio.run(scenario())


def test_practice_mistake_pulls_card_review_and_open_answers():
    from datetime import timedelta
    from app.services.practice_service import (
        generate_practice_session, verify_practice_answer, open_answer_matches,
    )
    from app.services.knowledge_path import day_start_utc
    from app.database.models import PracticeItem

    assert open_answer_matches("прокурора", "Прокурор.")
    assert open_answer_matches("Конституционный суд", "Конституционный Суд.")
    assert open_answer_matches("конституционый суд", "Конституционный суд.")  # опечатка
    assert open_answer_matches("15", "15.")
    assert not open_answer_matches("", "Прокурор.")
    assert not open_answer_matches("суд", "Прокурор.")
    assert not open_answer_matches("прокурор или судья или адвокат", "Прокурор.")

    async def scenario():
        nodes = await build_path()
        try:
            async with AsyncSessionLocal() as db:
                await complete_lesson(db, USER, nodes["base"], 1)
                await db.commit()
                await learn_node_cards(db, nodes["base"])
                items = await generate_practice_session(USER, SUBJECT, count=20, db=db)
                item = items[0]
                res = await verify_practice_answer(USER, item["id"], "Неверно 1.", db=db)
                assert res["correct"] is False
                stored = (await db.execute(select(PracticeItem).where(PracticeItem.item_id == item["id"]))).scalar_one()
                card_row = (await db.execute(select(Card).where(Card.text == stored.prompt, Card.user_id == USER))).scalar_one()
                assert card_row.next_review == day_start_utc() + timedelta(days=1)

                ok = await verify_practice_answer(USER, items[1]["id"], "Ответ.", db=db)
                assert ok["correct"] is True
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(PracticeItem).where(PracticeItem.user_id == USER))
                await db.commit()
            await cleanup()

    asyncio.run(scenario())


def test_lesson_and_its_cards_are_one_portion():
    """Урок + его карточки неделимы: после урока выдаются все его карточки, даже сверх нормы."""
    from fastapi.testclient import TestClient
    from main import app

    nodes = asyncio.run(build_path())
    asyncio.run(set_daily_limit(2))
    try:
        with patch("app.services.generation_worker.claim_next_pending_job", return_value=None):
            client = TestClient(app)
            h = {"X-User-Id": USER}
            plan = client.get(f"/api/path/{SUBJECT}/day", headers=h).json()
            assert plan["is_path"] and plan["room"] == 2 and plan["can_start_lesson"] is False
            assert plan["next_lesson"]["node_id"] == nodes["base"]

            # Норма 2, но в уроке 5 карточек: после урока — все 5, а не половина
            assert client.post(f"/api/path/node/{nodes['base']}/complete", json={"checkpoint_score": 1}, headers=h).status_code == 200
            cards = client.get(f"/api/session?subject={SUBJECT}&mode=new&node_id={nodes['base']}", headers=h).json()
            assert len(cards) == 5

            plan = client.get(f"/api/path/{SUBJECT}/day", headers=h).json()
            assert plan["unfinished"] == {"node_id": nodes["base"], "node_name": "Основа", "count": 5}
            stats = client.get(f"/api/stats/dashboard?subject={SUBJECT}", headers=h).json()
            assert stats["path_day"]["unfinished"]["count"] == 5
    finally:
        asyncio.run(set_daily_limit(None))
        asyncio.run(cleanup())


def test_next_step_guides_through_path():
    """«Продолжить путь»: урок → все его карточки → следующий урок, пока есть место → практика → итог с целью дня."""
    from app.services.knowledge_path import next_path_step, get_day_plan

    async def scenario():
        nodes = await build_path()
        try:
            async with AsyncSessionLocal() as db:
                step = await next_path_step(db, USER, SUBJECT, [])
                assert step["type"] == "lesson" and step["node_id"] == nodes["base"]

                await complete_lesson(db, USER, nodes["base"], 1)
                await db.commit()
                step = await next_path_step(db, USER, SUBJECT, ["lesson"])
                assert step["type"] == "cards" and step["node_id"] == nodes["base"] and step["count"] == 5

                # Карточки основы выучены (5 из нормы 10) → место есть, открывается урок темы
                await learn_node_cards(db, nodes["base"])
                step = await next_path_step(db, USER, SUBJECT, ["lesson", "cards"])
                assert step["type"] == "lesson" and step["node_id"] == nodes["topic"]
                assert (await get_day_plan(db, USER, SUBJECT))["goal_met"] is False

                # «Новая тема» — ровно одна порция за нажатие
                step = await next_path_step(db, USER, SUBJECT, ["lesson", "cards"], scope="topic")
                assert step["type"] == "done" and step["reason"] == "topic_done"
                step = await next_path_step(db, USER, SUBJECT, [], scope="topic")
                assert step["type"] == "lesson" and step["node_id"] == nodes["topic"]
        finally:
            await cleanup()

    async def limit_reached():
        nodes = await build_path()
        await set_daily_limit(6)
        try:
            async with AsyncSessionLocal() as db:
                await complete_lesson(db, USER, nodes["base"], 1)
                await db.commit()
                await learn_node_cards(db, nodes["base"])  # 5 из 6 — на новый урок места нет

                step = await next_path_step(db, USER, SUBJECT, ["lesson", "cards"])
                assert step["type"] == "practice" and step["count"] == 5
                step = await next_path_step(db, USER, SUBJECT, ["lesson", "cards", "practice"])
                assert step["type"] == "done" and step["reason"] == "limit"
                assert step["plan"]["goal_met"] is True and step["today"]["lessons"] == 1

                # Тема сверх нормы — только по явному выбору
                step = await next_path_step(db, USER, SUBJECT, [], scope="topic")
                assert step["type"] == "done" and step["reason"] == "limit"
                step = await next_path_step(db, USER, SUBJECT, [], scope="topic", extra=True)
                assert step["type"] == "lesson" and step["node_id"] == nodes["topic"]

                # Подтема без урока: после освоения темы сразу открываются её карточки
                await complete_lesson(db, USER, nodes["topic"], 1)
                await db.commit()
                await learn_node_cards(db, nodes["topic"])
                step = await next_path_step(db, USER, SUBJECT, [], scope="topic", extra=True)
                assert step["type"] == "cards" and step["node_id"] == nodes["sub"]
        finally:
            await set_daily_limit(None)
            await cleanup()

    asyncio.run(scenario())
    asyncio.run(limit_reached())


def test_day_starts_at_moscow_midnight():
    from datetime import datetime
    from app.services.knowledge_path import day_start_utc, day_key

    # 01:30 по Москве 1 октября = 22:30 UTC 30 сентября: учебный день уже 1 октября
    now = datetime(2026, 9, 30, 22, 30)
    assert day_start_utc(now) == datetime(2026, 9, 30, 21, 0)
    assert day_key(now) == "2026-10-01"
    assert day_start_utc(datetime(2026, 9, 30, 20, 59)) == datetime(2026, 9, 29, 21, 0)
