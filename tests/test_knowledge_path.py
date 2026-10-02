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
                    if it.item_type == "open_recall":
                        assert it.options == []  # письменный вопрос: ответ вводится вручную
                    else:
                        assert set(it.options) == {"Ответ.", "Неверно 1.", "Неверно 2.", "Неверно 3."}
                assert all(it.item_type != "relation" for it in stored)
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(PracticeItem).where(PracticeItem.user_id == USER))
                await db.commit()
            await cleanup()

    asyncio.run(scenario())


def test_practice_mistake_pulls_card_review():
    from datetime import timedelta
    from app.services.practice_service import generate_practice_session, verify_practice_answer
    from app.core.timeutil import user_day_start
    from app.database.models import PracticeItem

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
                assert card_row.next_review == await user_day_start(db, USER) + timedelta(days=1)

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


# --- Повторная нарезка не уничтожает ручные карточки; лимиты импорта ---
def test_replace_keeps_manual_cards_and_requires_confirmation():
    import asyncio
    from sqlalchemy import select, func
    from app.database.session import AsyncSessionLocal
    from app.database.models import Card, ReviewLog, utc_now
    from app.services.knowledge_path import wipe_subject, generated_path_stats

    uid, sub = "replace_test_user", "replace_test_sub"

    async def run():
        async with AsyncSessionLocal() as db:
            from app.database.models import Phrase
            ph = Phrase(text="p", subject=sub, user_id=uid)
            db.add(ph); await db.flush()
            gen = Card(phrase_id=ph.id, user_id=uid, subject=sub, text="g", translation="g", state=2, next_review=utc_now())
            man = Card(phrase_id=ph.id, user_id=uid, subject=sub, text="m", translation="m", state=2, next_review=utc_now())
            db.add_all([gen, man]); await db.flush()
            from app.database.models import KnowledgeNode
            n = KnowledgeNode(user_id=uid, subject=sub, node_key="k", name="k", tier=0, order_idx=0,
                              prereq_keys=[], summary="", source_hint="", lesson_status="ready")
            db.add(n); await db.flush()
            gen.node_id = n.id
            db.add_all([ReviewLog(card_id=gen.id, user_id=uid, rating=3, review_time=utc_now()),
                        ReviewLog(card_id=man.id, user_id=uid, rating=3, review_time=utc_now())])
            await db.commit()

            assert await generated_path_stats(db, uid, sub) == {"cards": 1, "reviews": 1}
            await wipe_subject(db, uid, sub, only_generated=True)
            await db.commit()
            left = (await db.execute(select(Card.text).where(Card.user_id == uid, Card.subject == sub))).scalars().all()
            logs = (await db.execute(select(func.count(ReviewLog.id)).where(ReviewLog.user_id == uid))).scalar()
            assert left == ["m"] and logs == 1
            await wipe_subject(db, uid, sub)
            await db.commit()

    asyncio.run(run())


def test_import_limits(monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    from app.core.config import settings
    monkeypatch.setattr(settings, "MAX_IMPORT_CHARS", 100)
    with TestClient(app) as client:
        r = client.post("/api/config/import", json={"text": "x" * 500, "subject": "limits_sub"},
                        headers={"X-User-Id": "limits_user"})
        assert r.status_code == 413 and "слишком большой" in r.json()["detail"]


# --- Вводный урок курса ---

INTRO_LESSON = {"screens": [{"say": f"Экран {i}", "emo": "talk", "focus": []} for i in range(6)], "check": []}


def test_intro_lesson_is_first_hidden_step_without_cards():
    """Вводный урок: скрыт из графа, идёт первым шагом, не считается темой дня и не открывает карточки."""
    from app.services.knowledge_path import next_path_step, get_day_plan, INTRO_KEY, get_today_summary

    async def fake_with_intro(text, subject, calls=None):
        res = fake_result()
        res["intro"] = INTRO_LESSON
        return res

    async def scenario():
        await cleanup()
        try:
            with patch("app.services.generation_worker.build_learning_path", side_effect=fake_with_intro):
                await process_generation_job(await create_job(), is_offpeak=True)
            async with AsyncSessionLocal() as db:
                state = await get_path_state(db, USER, SUBJECT)
                assert INTRO_KEY not in [n["key"] for n in state["nodes"]] and len(state["nodes"]) == 3
                assert state["intro"]["done"] is False
                intro_id = state["intro"]["id"]
                assert (await db.scalar(select(func.count(Card.id)).where(Card.node_id == intro_id))) == 0

                step = await next_path_step(db, USER, SUBJECT, [])
                assert step["type"] == "intro" and step["node_id"] == intro_id
                # Один раз за запуск; после него — обычный путь
                step = await next_path_step(db, USER, SUBJECT, ["intro"])
                assert step["type"] == "lesson"

                await complete_lesson(db, USER, intro_id, 0)
                await db.commit()
                assert (await get_path_state(db, USER, SUBJECT))["intro"]["done"] is True
                step = await next_path_step(db, USER, SUBJECT, [])
                assert step["type"] == "lesson"
                plan = await get_day_plan(db, USER, SUBJECT)
                assert plan["lessons_today"] == 0 and (await get_today_summary(db, USER, SUBJECT))["lessons"] == 0
        finally:
            await cleanup()

    asyncio.run(scenario())


def test_build_intro_lesson_normalizes_and_survives_failure():
    from app.services.ai_gateway import path_builder

    path_map = fake_result()["map"]
    good = {"lesson": {"screens": [{"say": f"Экран {i}", "emo": "weird", "focus": ["base", "ghost"]} for i in range(7)],
                       "check": [{"q": "лишнее", "options": ["а", "б"], "answer": 0}]}}

    async def ok_call(prompt, max_tokens, label, calls_log, temperature=0.1):
        assert "TYPE: INTRO" in prompt and "FACTS:" in prompt and "[SOURCE MATERIAL" not in prompt
        return good

    async def bad_call(*args, **kwargs):
        raise RuntimeError("API down")

    async def scenario():
        with patch.object(path_builder, "_call", side_effect=ok_call):
            lesson = await path_builder.build_intro_lesson(path_map, [])
        assert len(lesson["screens"]) == 7 and lesson["check"] == [] and lesson["screens"][0]["emo"] == "talk"
        assert lesson["screens"][0]["focus"] == ["base"]
        with patch.object(path_builder, "_call", side_effect=bad_call):
            assert await path_builder.build_intro_lesson(path_map, []) is None

    asyncio.run(scenario())
