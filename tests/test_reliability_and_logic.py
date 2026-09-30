"""Надёжность ответов, cloze-burying, FSRS-переобучение, границы суток в часовом поясе пользователя."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, func

from app.api.endpoints.train import AnswerIn, get_session_cards, handle_answer
from app.core import timeutil
from app.database.models import Card, Phrase, ReviewLog, UserSetting, utc_now
from app.database.session import AsyncSessionLocal
from app.services.fsrs_core import calculate_intervals


def run(coro):
    return asyncio.run(coro)


async def _reset(uid):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(ReviewLog).where(ReviewLog.user_id == uid))
        await db.execute(delete(Card).where(Card.user_id == uid))
        await db.execute(delete(Phrase).where(Phrase.user_id == uid))
        await db.execute(delete(UserSetting).where(UserSetting.user_id == uid))
        await db.commit()


async def _card(db, uid, sub, **kw):
    ph = Phrase(text=kw.pop("phrase", "p"), subject=sub, user_id=uid)
    db.add(ph)
    await db.flush()
    defaults = dict(phrase_id=ph.id, user_id=uid, subject=sub, text="q", translation="a", state=0, next_review=utc_now())
    defaults.update(kw)
    c = Card(**defaults)
    db.add(c)
    await db.flush()
    return c


# ---------- идемпотентность и время ответа ----------
def test_answer_is_idempotent_and_uses_client_time():
    uid = "rel_user_1"

    async def go():
        await _reset(uid)
        async with AsyncSessionLocal() as db:
            c = await _card(db, uid, "rel_sub")
            await db.commit()
            cid = c.id
        client_time = int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000)
        payload = AnswerIn(card_id=cid, rating=3, response_time=3000, client_id="abc-1", answered_at=client_time)
        async with AsyncSessionLocal() as db:
            first = await handle_answer(payload, uid, db)
        async with AsyncSessionLocal() as db:
            second = await handle_answer(payload, uid, db)
            logs = (await db.execute(select(func.count(ReviewLog.id)).where(ReviewLog.user_id == uid))).scalar()
            log = (await db.execute(select(ReviewLog).where(ReviewLog.user_id == uid))).scalars().first()
        assert first == {"status": "success"} and second.get("duplicate") is True
        assert logs == 1
        assert utc_now() - log.review_time > timedelta(hours=1, minutes=50)   # время ответа — клиентское, а не серверное
        await _reset(uid)

    run(go())


def test_future_or_ancient_client_time_is_ignored():
    uid = "rel_user_2"

    async def go():
        await _reset(uid)
        async with AsyncSessionLocal() as db:
            c = await _card(db, uid, "rel_sub")
            await db.commit()
            cid = c.id
        bogus = int((datetime.now(timezone.utc) + timedelta(days=5)).timestamp() * 1000)
        async with AsyncSessionLocal() as db:
            await handle_answer(AnswerIn(card_id=cid, rating=3, response_time=3000, answered_at=bogus), uid, db)
            log = (await db.execute(select(ReviewLog).where(ReviewLog.user_id == uid))).scalars().first()
        assert abs((utc_now() - log.review_time).total_seconds()) < 60
        await _reset(uid)

    run(go())


def test_intro_step_fast_answer_is_not_an_outlier():
    uid = "rel_user_3"

    async def go():
        await _reset(uid)
        async with AsyncSessionLocal() as db:
            c = await _card(db, uid, "rel_sub")
            await db.commit()
            cid = c.id
        async with AsyncSessionLocal() as db:
            await handle_answer(AnswerIn(card_id=cid, rating=4, response_time=300, is_introduction=True, is_fast_track=True), uid, db)
            log = (await db.execute(select(ReviewLog).where(ReviewLog.user_id == uid))).scalars().first()
        assert log.rating == 4 and log.is_outlier is False
        await _reset(uid)

    run(go())


# ---------- cloze ----------
def test_failed_cloze_card_is_not_buried_but_sibling_is():
    uid, sub = "rel_user_4", "rel_cloze"

    async def go():
        await _reset(uid)
        now = utc_now()
        async with AsyncSessionLocal() as db:
            a = await _card(db, uid, sub, content_type="cloze", state=3, next_review=now, text="a")
            b = await _card(db, uid, sub, content_type="cloze", state=2, next_review=now - timedelta(hours=1), text="b",
                            last_review=now - timedelta(days=3), phrase_id=a.phrase_id)
            db.add(ReviewLog(card_id=a.id, user_id=uid, rating=1, review_time=now, state=2))
            await db.commit()
            a_id, b_id = a.id, b.id
        async with AsyncSessionLocal() as db:
            cards = await get_session_cards(subject=sub, mode="review", current_user=uid, db=db)
        ids = [c["id"] for c in cards]
        assert a_id in ids          # провалена сегодня — должна вернуться на закрепление
        assert b_id not in ids      # её сиблинг откладывается до завтра
        await _reset(uid)

    run(go())


# ---------- FSRS ----------
def _card_obj(**kw):
    base = dict(state=2, stability=30.0, difficulty=5.0, last_review=utc_now() - timedelta(days=30))
    base.update(kw)
    return SimpleNamespace(**base)


def test_relearning_keeps_post_lapse_stability():
    card = _card_obj(state=3, stability=20.0, last_review=utc_now() - timedelta(minutes=6))
    stability, _, state, nxt, _ = calculate_intervals(card, 3, utc_now())
    assert state == 2 and stability >= 20.0           # раньше сбрасывалось к 2.4
    assert nxt - utc_now() > timedelta(days=10)


def test_relearning_hard_is_below_good():
    good = calculate_intervals(_card_obj(state=3, stability=20.0), 3, utc_now())[0]
    hard = calculate_intervals(_card_obj(state=3, stability=20.0), 2, utc_now())[0]
    assert hard < good


def test_retrievability_independent_of_target_retention():
    now = utc_now()
    a = calculate_intervals(_card_obj(), 3, now, target_retention=0.8)
    b = calculate_intervals(_card_obj(), 3, now, target_retention=0.95)
    # Стабильность (новое знание) не зависит от целевого удержания; от него зависит только интервал
    assert a[0] == pytest.approx(b[0])
    assert a[3] > b[3]


# ---------- часовой пояс ----------
def test_local_midnight_respects_timezone():
    now = datetime(2026, 9, 30, 21, 30)  # 21:30 UTC = 00:30 1 октября в Москве
    msk = timeutil.local_midnight_utc("Europe/Moscow", now_utc=now)
    utc = timeutil.local_midnight_utc("UTC", now_utc=now)
    assert msk == datetime(2026, 9, 30, 21, 0)
    assert utc == datetime(2026, 9, 30, 0, 0)
    assert timeutil.is_valid_timezone("Asia/Almaty") and not timeutil.is_valid_timezone("Mars/Base")
    assert timeutil.resolve_tz("garbage").key in ("Europe/Moscow", "UTC")


def test_timezone_endpoint_validates_and_stores():
    from fastapi.testclient import TestClient
    from main import app
    uid = "rel_tz_user"
    with TestClient(app) as client:
        h = {"X-User-Id": uid}
        assert client.post("/api/config/timezone", json={"timezone": "Nope/Zone"}, headers=h).status_code == 422
        assert client.post("/api/config/timezone", json={"timezone": "Asia/Almaty"}, headers=h).status_code == 200

    async def check():
        async with AsyncSessionLocal() as db:
            tz = await timeutil.get_user_timezone(db, uid)
            await db.execute(delete(UserSetting).where(UserSetting.user_id == uid))
            await db.commit()
        return tz

    assert run(check()) == "Asia/Almaty"


# ---------- лимитер ----------
def test_rate_limit_is_actually_registered_on_routes():
    """Раньше @limiter.limit стоял выше @router.get, и лимит не применялся вовсе."""
    from app.core.limiter import limiter, HAS_SLOWAPI
    if not HAS_SLOWAPI:
        pytest.skip("slowapi не установлен")
    registered = set(limiter._route_limits)
    assert "app.api.endpoints.practice.get_practice_session" in registered
    assert "app.api.endpoints.imports.import_raw_text" in registered
    assert "app.api.endpoints.imports.import_file_at_code_level" in registered


def test_limiter_key_uses_real_ip_behind_proxy_only():
    from app.core.limiter import client_key
    mk = lambda host, hdr: SimpleNamespace(client=SimpleNamespace(host=host), headers=hdr)
    assert client_key(mk("127.0.0.1", {"x-real-ip": "203.0.113.5"})) == "203.0.113.5"
    # прямой клиент не может подсунуть чужой X-Real-IP
    assert client_key(mk("198.51.100.7", {"x-real-ip": "203.0.113.5"})) == "198.51.100.7"


# ---------- практика ----------
def test_practice_regeneration_keeps_active_session_items():
    from app.services.practice_service import verify_practice_answer
    from app.database.models import PracticeItem
    uid = "rel_practice_user"

    async def go():
        async with AsyncSessionLocal() as db:
            await db.execute(delete(PracticeItem).where(PracticeItem.user_id == uid))
            db.add(PracticeItem(item_id="keep-me-1", user_id=uid, subject="p_sub", item_type="recall",
                                prompt="q", options=["a", "b"], correct_answer="a"))
            await db.commit()
        from app.services.practice_service import generate_practice_session
        async with AsyncSessionLocal() as db:
            await generate_practice_session(uid, "p_sub", 5, db=db)   # пустой путь — новых заданий нет
        async with AsyncSessionLocal() as db:
            res = await verify_practice_answer(uid, "keep-me-1", "a", db=db)
            await db.execute(delete(PracticeItem).where(PracticeItem.user_id == uid))
            await db.commit()
        assert res["correct"] is True

    run(go())


# ---------- проверка урока влияет на карточки; план пути ----------
def test_checkpoint_score_shifts_new_card_difficulty_and_plan():
    from app.database.models import KnowledgeNode, NodeProgress
    from app.services.knowledge_path import complete_lesson, get_path_state
    uid, sub = "rel_cp_user", "rel_cp_sub"

    async def go():
        await _reset(uid)
        async with AsyncSessionLocal() as db:
            await db.execute(delete(NodeProgress).where(NodeProgress.user_id == uid))
            await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == uid))
            n = KnowledgeNode(user_id=uid, subject=sub, node_key="k", name="K", tier=0, order_idx=0, prereq_keys=[],
                              summary="", source_hint="", lesson_status="ready",
                              lesson={"screens": [], "check": [{}, {}, {}, {}]})
            db.add(n)
            await db.flush()
            c = await _card(db, uid, sub, node_id=n.id, difficulty=5.5)
            await db.commit()
            nid, cid = n.id, c.id
        async with AsyncSessionLocal() as db:
            await complete_lesson(db, uid, nid, 1)      # 1 из 4 — слабо
            await db.commit()
            weak = (await db.execute(select(Card.difficulty).where(Card.id == cid))).scalar()
            await complete_lesson(db, uid, nid, 4)      # повтор не должен сдвигать ещё раз
            await db.commit()
            again = (await db.execute(select(Card.difficulty).where(Card.id == cid))).scalar()
            state = await get_path_state(db, uid, sub)
            await db.execute(delete(NodeProgress).where(NodeProgress.user_id == uid))
            await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == uid))
            await db.commit()
        assert weak == 6.5 and again == 6.5
        assert state["plan"] == {"new_cards_left": 1, "daily_limit": 10, "days_left": 1}
        await _reset(uid)

    run(go())


def test_prior_difficulty_shifts_first_review_but_neutral_does_not():
    now = utc_now()
    base = calculate_intervals(_card_obj(state=0, difficulty=5.5, stability=1.0, last_review=None), 3, now)[1]
    hard = calculate_intervals(_card_obj(state=0, difficulty=7.5, stability=1.0, last_review=None), 3, now)[1]
    assert hard == pytest.approx(base + 1.0)
