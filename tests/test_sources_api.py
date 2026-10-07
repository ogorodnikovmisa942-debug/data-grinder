"""Материалы курса через API: повторная загрузка того же файла, новый материал, список и удаление."""
import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import delete, select, func

from main import app
from app.database.session import AsyncSessionLocal
from app.database.models import Card, GenerationJob, KnowledgeNode, Phrase, ReviewLog, Source, utc_now
from app.services.knowledge_path import text_fingerprint, wipe_subject

USER = "test_sources_api_user"
SUBJECT = "test_sources_api_subject"
BOOK = "Право есть система общеобязательных норм, охраняемых государством. " * 40
OTHER = "Государство есть политическая организация власти на определённой территории. " * 40
H = {"X-User-Id": USER}


async def _cleanup():
    async with AsyncSessionLocal() as db:
        await wipe_subject(db, USER, SUBJECT)
        await db.execute(delete(GenerationJob).where(GenerationJob.user_id == USER))
        await db.commit()


async def _seed_source(with_review: bool) -> int:
    """Курс из одного материала (как после обычной загрузки), у карточки которого есть ответ."""
    async with AsyncSessionLocal() as db:
        src = Source(user_id=USER, subject=SUBJECT, name="Учебник.pdf", chars=len(BOOK), text_hash=text_fingerprint(BOOK),
                     cards_count=1, nodes_count=1)
        db.add(src)
        await db.flush()
        node = KnowledgeNode(user_id=USER, subject=SUBJECT, node_key="base", name="Основа", tier=0, order_idx=1, prereq_keys=[],
                             summary="", source_hint="", lesson_status="ready", source_id=src.id)
        ph = Phrase(text="Основа", subject=SUBJECT, user_id=USER)
        db.add_all([node, ph])
        await db.flush()
        card = Card(phrase_id=ph.id, user_id=USER, subject=SUBJECT, text="Q?", translation="A.", state=2, next_review=utc_now(),
                    node_id=node.id, source_id=src.id)
        db.add(card)
        await db.flush()
        if with_review:
            db.add(ReviewLog(card_id=card.id, user_id=USER, rating=3, review_time=utc_now(), state=2))
        await db.commit()
        return src.id


def _client():
    return TestClient(app)


def test_same_material_asks_for_confirmation_and_then_replaces_only_itself(monkeypatch):
    async def prepare():
        await _cleanup()
        return await _seed_source(with_review=True)

    source_id = asyncio.run(prepare())
    try:
        with _client() as client:
            r = client.post("/api/config/import", json={"text": BOOK, "subject": SUBJECT}, headers=H)
            assert r.status_code == 409
            d = r.json()["detail"]
            assert d["code"] == "duplicate_source" and d["reviews"] == 1 and "Учебник.pdf" in d["message"]

            r = client.post("/api/config/import", json={"text": BOOK, "subject": SUBJECT, "confirm_replace": True}, headers=H)
            assert r.status_code == 200 and r.json()["status"] == "queued"

        async def job():
            async with AsyncSessionLocal() as db:
                return (await db.execute(select(GenerationJob).where(GenerationJob.user_id == USER))).scalar_one()

        j = asyncio.run(job())
        assert j.replace_source_id == source_id and j.text_hash == text_fingerprint(BOOK) and j.source_name
    finally:
        asyncio.run(_cleanup())


def test_other_material_is_added_without_questions_even_with_progress():
    source_id = asyncio.run(_seed_source(with_review=True)) if asyncio.run(_cleanup()) is None else 0
    try:
        with _client() as client:
            r = client.post("/api/config/import", json={"text": OTHER, "subject": SUBJECT}, headers=H)
            assert r.status_code == 200 and r.json()["status"] == "queued"          # раньше здесь требовалось согласие «заменить всё»

        async def job():
            async with AsyncSessionLocal() as db:
                return (await db.execute(select(GenerationJob).where(GenerationJob.user_id == USER))).scalar_one()

        j = asyncio.run(job())
        assert j.replace_source_id is None and j.text_hash == text_fingerprint(OTHER)
        assert source_id
    finally:
        asyncio.run(_cleanup())


def test_same_material_without_answers_is_replaced_without_asking():
    source_id = asyncio.run(_seed_source(with_review=False)) if asyncio.run(_cleanup()) is None else 0
    try:
        with _client() as client:
            r = client.post("/api/config/import", json={"text": BOOK, "subject": SUBJECT}, headers=H)
            assert r.status_code == 200
        async def job():
            async with AsyncSessionLocal() as db:
                return (await db.execute(select(GenerationJob).where(GenerationJob.user_id == USER))).scalar_one()
        assert asyncio.run(job()).replace_source_id == source_id                    # терять нечего: согласие не нужно
    finally:
        asyncio.run(_cleanup())


def test_sources_list_and_delete_endpoints():
    source_id = asyncio.run(_seed_source(with_review=True)) if asyncio.run(_cleanup()) is None else 0
    try:
        with _client() as client:
            r = client.get(f"/api/path/{SUBJECT}/sources", headers=H)
            assert r.status_code == 200
            [s] = r.json()["sources"]
            assert (s["id"], s["name"], s["nodes"], s["cards"], s["reviews"]) == (source_id, "Учебник.pdf", 1, 1, 1)

            assert client.delete(f"/api/path/{SUBJECT}/sources/{source_id}", headers={"X-User-Id": "someone_else"}).status_code == 404
            r = client.delete(f"/api/path/{SUBJECT}/sources/{source_id}", headers=H)
            assert r.status_code == 200 and r.json()["cards"] == 1 and r.json()["reviews"] == 1
            assert client.get(f"/api/path/{SUBJECT}/sources", headers=H).json()["sources"] == []

        async def left():
            async with AsyncSessionLocal() as db:
                return await db.scalar(select(func.count(Card.id)).where(Card.user_id == USER))
        assert asyncio.run(left()) == 0
    finally:
        asyncio.run(_cleanup())


def test_session_cards_show_the_source_only_when_the_subject_has_several_materials():
    from app.api.endpoints.train import get_session_cards

    async def scenario():
        await _cleanup()
        first = await _seed_source(with_review=False)
        async with AsyncSessionLocal() as db:
            async def names():
                cards = await get_session_cards(subject=SUBJECT, mode="review", current_user=USER, db=db)
                return {c["source_name"] for c in cards}
            assert await names() == {""}                                          # материал один: подпись «Из: …» была бы шумом
            second = Source(user_id=USER, subject=SUBJECT, name="Конспект", chars=10, nodes_count=1, cards_count=1)
            db.add(second)
            await db.flush()
            ph = Phrase(text="п", subject=SUBJECT, user_id=USER)
            db.add(ph)
            await db.flush()
            db.add(Card(phrase_id=ph.id, user_id=USER, subject=SUBJECT, text="Q2?", translation="A2.", state=2, next_review=utc_now(),
                        source_id=second.id))
            await db.commit()
            assert await names() == {"Учебник.pdf", "Конспект"}
        return first

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_cleanup())
