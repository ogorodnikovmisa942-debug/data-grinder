"""Политика открытых вопросов: кому, когда и как часто предъявлять письменный формат."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from main import app
from app.database.models import Card, Phrase, PracticeItem, ReviewLog, UserSetting, utc_now
from app.database.session import AsyncSessionLocal
from app.services import open_policy as op
from app.services.open_policy import answer_kind, is_open_eligible, pick_open_ids, share_for


def card(i=1, **kw):
    base = dict(id=i, state=2, content_type="text", lapses=0, stability=10.0, translation="Верховенство права")
    base.update(kw)
    return SimpleNamespace(**base)


# ---------- кому можно ----------
def test_only_learned_stable_cards_are_eligible():
    assert is_open_eligible(card())
    assert not is_open_eligible(card(state=0)), "новые — сначала узнавание"
    assert not is_open_eligible(card(state=1)) and not is_open_eligible(card(state=3))
    assert not is_open_eligible(card(stability=2.0)), "шаткое вспоминание: успех извлечения важнее формата"
    assert not is_open_eligible(card(lapses=4)), "пиявка"
    assert not is_open_eligible(card(content_type="cloze"))
    assert not is_open_eligible(card(translation=""))
    assert not is_open_eligible(card(translation="и в на"))            # нечего проверять
    assert not is_open_eligible(card(translation="слово " * 120))      # слишком длинный эталон


def test_answer_kind():
    assert answer_kind("1994 год") == "short"
    assert answer_kind("Система общеобязательных норм, охраняемых силой государства и обеспечивающих порядок") == "free"


# ---------- как часто ----------
def test_share_grows_with_stability_and_mode():
    assert share_for("off", 50) == 0
    assert share_for("auto", 20) > share_for("auto", 5)
    assert share_for("exam", 20) > share_for("auto", 20)
    assert share_for("exam", 100, recent_accuracy=1.0, n_recent=30) <= op.MAX_SHARE


def test_share_adapts_to_accuracy_85_percent_rule():
    base = share_for("auto", 10)
    assert share_for("auto", 10, 0.97, 20) > base       # уверенно — сложнее
    assert share_for("auto", 10, 0.70, 20) < base       # трудновато — реже
    assert share_for("auto", 10, 0.40, 20) < share_for("auto", 10, 0.70, 20)
    assert share_for("auto", 10, 0.40, 3) == base       # мало данных — не подстраиваемся


def test_selection_is_deterministic_and_capped():
    cards = [card(i) for i in range(1, 400)]
    a = pick_open_ids(cards, "exam", "2026-09-30", salt="u1")
    b = pick_open_ids(cards, "exam", "2026-09-30", salt="u1")
    assert a == b and len(a) == op.SESSION_CAP["exam"]
    assert pick_open_ids(cards, "auto", "2026-09-30", salt="u1") <= set(range(1, 400))
    assert len(pick_open_ids(cards, "auto", "2026-09-30", salt="u1")) <= op.SESSION_CAP["auto"]
    assert pick_open_ids(cards, "off", "2026-09-30") == set()
    assert a != pick_open_ids(cards, "exam", "2026-10-01", salt="u1"), "в другой день набор другой"


def test_auto_mode_share_is_modest():
    cards = [card(i) for i in range(1, 2001)]
    picked = pick_open_ids(cards, "auto", "2026-09-30", salt="u", cap=10_000)
    assert 0.15 < len(picked) / len(cards) < 0.30     # около 20%


# ---------- интеграция с очередью и практикой ----------
UID = "policy_user"
H = {"X-User-Id": UID}


async def _seed(n=40):
    now = utc_now()
    async with AsyncSessionLocal() as db:
        ph = Phrase(text="p", subject="policy_sub", user_id=UID)
        db.add(ph)
        await db.flush()
        for i in range(n):
            db.add(Card(phrase_id=ph.id, user_id=UID, subject="policy_sub", text=f"Вопрос {i}?",
                        translation=f"Ответ номер {i} про право", state=2, stability=20.0, difficulty=5.0,
                        last_review=now - timedelta(days=20), next_review=now - timedelta(hours=1)))
        await db.commit()


async def _clean():
    async with AsyncSessionLocal() as db:
        for m in (ReviewLog, Card, Phrase, PracticeItem, UserSetting):
            await db.execute(delete(m).where(m.user_id == UID))
        await db.commit()


def test_session_marks_some_cards_open_and_respects_mode():
    asyncio.run(_clean()); asyncio.run(_seed())
    try:
        with TestClient(app) as client:
            cards = client.get("/api/session?subject=policy_sub&mode=review", headers=H).json()
            n_open = sum(1 for c in cards if c["presentation"] == "open")
            assert 1 <= n_open <= op.SESSION_CAP["auto"] and n_open < len(cards)
            assert all(c["answer_kind"] in ("short", "free") for c in cards)
            # повторный запрос в тот же день даёт тот же формат
            again = client.get("/api/session?subject=policy_sub&mode=review", headers=H).json()
            assert {c["id"] for c in cards if c["presentation"] == "open"} == {c["id"] for c in again if c["presentation"] == "open"}

            assert client.post("/api/open/settings", json={"mode": "off"}, headers=H).status_code == 200
            assert all(c["presentation"] == "flip" for c in client.get("/api/session?subject=policy_sub&mode=review", headers=H).json())
            assert client.post("/api/open/settings", json={"mode": "bogus"}, headers=H).status_code == 422

            client.post("/api/open/settings", json={"mode": "exam"}, headers=H)
            exam = client.get("/api/session?subject=policy_sub&mode=review", headers=H).json()
            assert sum(1 for c in exam if c["presentation"] == "open") > n_open
            # новые карточки и штурм — не письменные
            assert all(c["presentation"] == "flip" for c in client.get("/api/session?subject=policy_sub&mode=cram", headers=H).json())
    finally:
        asyncio.run(_clean())


def test_answer_records_format_and_auto_score():
    asyncio.run(_clean()); asyncio.run(_seed(3))
    try:
        with TestClient(app) as client:
            c = client.get("/api/session?subject=policy_sub&mode=review", headers=H).json()[0]
            r = client.post("/api/answer", headers=H, json={
                "card_id": c["id"], "rating": 3, "response_time": 5000, "answer_format": "open", "auto_score": 0.75})
            assert r.status_code == 200
            bad = client.post("/api/answer", headers=H, json={"card_id": c["id"], "rating": 3, "answer_format": "weird"})
            assert bad.status_code == 422

        async def fetch():
            async with AsyncSessionLocal() as db:
                return (await db.execute(select(ReviewLog).where(ReviewLog.user_id == UID))).scalars().first()
        log = asyncio.run(fetch())
        assert log.answer_format == "open" and log.auto_score == 0.75
    finally:
        asyncio.run(_clean())


def test_low_open_accuracy_reduces_open_share():
    asyncio.run(_clean()); asyncio.run(_seed(200))
    try:
        with TestClient(app) as client:
            client.post("/api/open/settings", json={"mode": "exam"}, headers=H)
            before = sum(1 for c in client.get("/api/session?subject=policy_sub&mode=review", headers=H).json()
                         if c["presentation"] == "open")

        async def add_fails():
            async with AsyncSessionLocal() as db:
                first = (await db.execute(select(Card.id).where(Card.user_id == UID))).scalars().first()
                for _ in range(12):
                    db.add(ReviewLog(card_id=first, user_id=UID, rating=1, review_time=utc_now(), answer_format="open"))
                await db.commit()
        asyncio.run(add_fails())
        with TestClient(app) as client:
            after = sum(1 for c in client.get("/api/session?subject=policy_sub&mode=review", headers=H).json()
                        if c["presentation"] == "open")
        assert after <= before
    finally:
        asyncio.run(_clean())


def test_practice_mixes_open_recall_items_and_grades_them():
    asyncio.run(_clean())

    async def seed_path():
        from app.database.models import KnowledgeNode, NodeProgress
        now = utc_now()
        async with AsyncSessionLocal() as db:
            n = KnowledgeNode(user_id=UID, subject="policy_sub", node_key="k", name="K", tier=0, order_idx=0,
                              prereq_keys=[], summary="", source_hint="", lesson_status="ready")
            db.add(n); await db.flush()
            db.add(NodeProgress(user_id=UID, node_id=n.id, lesson_done=True, checkpoint_score=3))
            ph = Phrase(text="p", subject="policy_sub", user_id=UID); db.add(ph); await db.flush()
            for i in range(30):
                db.add(Card(phrase_id=ph.id, user_id=UID, subject="policy_sub", node_id=n.id, text=f"Q{i}?",
                            translation=f"Верховенство права номер {i}", state=2, stability=25.0, difficulty=5.0,
                            last_review=now - timedelta(days=20), next_review=now + timedelta(days=5),
                            distractors=["один", "два", "три"]))
            await db.commit()
    asyncio.run(seed_path())
    try:
        with TestClient(app) as client:
            client.post("/api/open/settings", json={"mode": "exam"}, headers=H)
            items = client.get("/api/practice/session?subject=policy_sub&count=30", headers=H).json()
            opens = [i for i in items if i["type"] == "open_recall"]
            assert opens and all(i["options"] == [] for i in opens)
            assert any(i["options"] for i in items), "формат смешивается, а не заменяется целиком"
            it = opens[0]
            number = it["prompt"][1:-1]
            ok = client.post("/api/practice/verify", headers=H,
                             json={"item_id": it["id"], "selected_answer": f"верховенство права номер {number}"}).json()
            assert ok["correct"] is True and ok["open"] is True and ok["correct_answer"].startswith("Верховенство")
            bad = client.post("/api/practice/verify", headers=H, json={"item_id": it["id"], "selected_answer": "не знаю"}).json()
            assert bad["correct"] is False and bad["score"] == 0
    finally:
        async def clean_path():
            from app.database.models import KnowledgeNode, NodeProgress
            async with AsyncSessionLocal() as db:
                await db.execute(delete(NodeProgress).where(NodeProgress.user_id == UID))
                await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == UID))
                await db.commit()
        asyncio.run(clean_path()); asyncio.run(_clean())
