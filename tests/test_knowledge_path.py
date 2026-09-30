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


def test_practice_uses_stored_distractors_and_graph_edges():
    from app.services.practice_service import generate_practice_session
    from app.database.models import PracticeItem

    async def scenario():
        await cleanup()
        try:
            with patch("app.services.generation_worker.build_learning_path", side_effect=fake_build):
                await process_generation_job(await create_job(), is_offpeak=True)
            async with AsyncSessionLocal() as db:
                assert await generate_practice_session(USER, SUBJECT, count=20, db=db) == []  # уроков нет — практики нет

                nodes = {n.node_key: n for n in (await db.execute(
                    select(KnowledgeNode).where(KnowledgeNode.user_id == USER))).scalars().all()}
                for key in ("base", "topic"):
                    await complete_lesson(db, USER, nodes[key].id, 1)
                await db.commit()

                items = await generate_practice_session(USER, SUBJECT, count=20, db=db)
                assert len(items) == 6  # 5 карточек основы + 1 карточка темы; подтема не пройдена
                stored = (await db.execute(select(PracticeItem).where(PracticeItem.user_id == USER))).scalars().all()
                for it in stored:
                    assert set(it.options) == {"Ответ.", "Неверно 1.", "Неверно 2.", "Неверно 3."}
                    assert it.correct_answer == "Ответ."
                # Вопрос на связь нужен ≥2 других узла того же яруса — в мини-графе его нет
                assert all(it.item_type != "relation" for it in stored)
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(PracticeItem).where(PracticeItem.user_id == USER))
                await db.commit()
            await cleanup()

    asyncio.run(scenario())


def test_next_step_guides_through_path():
    """«Продолжить путь»: урок → его карточки → следующий урок → практика → итог; пустой урок не блокирует путь."""
    from datetime import timedelta
    from app.database.models import utc_now
    from app.services.knowledge_path import next_path_step

    async def scenario():
        await cleanup()
        try:
            with patch("app.services.generation_worker.build_learning_path", side_effect=fake_build):
                await process_generation_job(await create_job(), is_offpeak=True)

            async with AsyncSessionLocal() as db:
                nodes = {n.node_key: n.id for n in (await db.execute(
                    select(KnowledgeNode).where(KnowledgeNode.user_id == USER))).scalars().all()}

                step = await next_path_step(db, USER, SUBJECT, [])
                assert step["type"] == "lesson" and step["node_id"] == nodes["base"]

                await complete_lesson(db, USER, nodes["base"], 1)
                await db.commit()
                step = await next_path_step(db, USER, SUBJECT, ["lesson"])
                assert step["type"] == "cards" and step["node_id"] == nodes["base"] and step["count"] == 5

                # Карточки основы вспомнены и ушли в Review на завтра → открывается тема
                for c in (await db.execute(select(Card).where(Card.node_id == nodes["base"]))).scalars().all():
                    c.state, c.next_review = 2, utc_now() + timedelta(days=1)
                    db.add(ReviewLog(card_id=c.id, user_id=USER, rating=3, review_time=utc_now(), state=0))
                await db.commit()
                step = await next_path_step(db, USER, SUBJECT, ["lesson", "cards"])
                assert step["type"] == "lesson" and step["node_id"] == nodes["topic"]

                # Лимит уроков за запуск исчерпан → практика по изученному
                step = await next_path_step(db, USER, SUBJECT, ["lesson", "cards", "lesson"])
                assert step["type"] == "practice"
                step = await next_path_step(db, USER, SUBJECT, ["lesson", "cards", "lesson", "practice"])
                assert step["type"] == "done" and step["today"]["lessons"] == 1

                # Подтема без урока: после освоения темы сразу открываются её карточки
                await complete_lesson(db, USER, nodes["topic"], 1)
                for c in (await db.execute(select(Card).where(Card.node_id == nodes["topic"]))).scalars().all():
                    c.state, c.next_review = 2, utc_now() + timedelta(days=1)
                    db.add(ReviewLog(card_id=c.id, user_id=USER, rating=3, review_time=utc_now(), state=0))
                await db.commit()
                step = await next_path_step(db, USER, SUBJECT, ["lesson"])
                assert step["type"] == "cards" and step["node_id"] == nodes["sub"]
        finally:
            await cleanup()

    asyncio.run(scenario())


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
