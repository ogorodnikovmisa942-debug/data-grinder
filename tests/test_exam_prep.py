"""Подготовка к экзамену по билетам: разбор списка, ответ ИИ, план по дням, шаги занятия."""
import asyncio
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select, delete

from app.database.models import (
    GenerationJob, Card, KnowledgeNode, ReviewLog, ExamPlan, ExamTicket, AiTelemetryLog, utc_now,
)
from app.database.session import AsyncSessionLocal
from app.services.ai_gateway.exam_matcher import normalize_ticket_results
from app.services.ai_gateway.exam_prompts import EXAM_MATCHER_SYSTEM_PROMPT, build_course_block
from app.services.exam_prep import (
    TICKET_ANSWER_TYPE, create_plan, exam_overview, exam_session_cards, lesson_quota, parse_ticket_list,
    required_node_ids, run_plan_matching, set_ticket_answer, next_exam_step, deactivate_plan,
)
from app.services.generation_worker import process_generation_job
from app.services.knowledge_path import complete_lesson, wipe_subject, next_path_step, _learned_today

USER = "test_exam_prep_user"
SUBJECT = "test_exam_subject"


# --- Разбор списка билетов ---

def test_parse_ticket_list_one_per_line():
    items = parse_ticket_list("1. Понятие государства\nБилет 2. Функции государства?\n3) Форма правления\n\n1. Понятие государства")
    assert [it["question"] for it in items] == ["Понятие государства", "Функции государства?", "Форма правления"]
    assert all(it["answer"] == "" for it in items)


def test_parse_ticket_list_blocks_with_answers():
    items = parse_ticket_list("Что такое суверенитет?\n- верховенство власти / независимость\n\nГод Конституции РФ\nОтвет: 1993")
    assert [it["question"] for it in items] == ["Что такое суверенитет?", "Год Конституции РФ"]
    assert "верховенство" in items[0]["answer"] and items[1]["answer"] == "1993"


# --- Ответ ИИ ---

def test_normalize_ticket_results_drops_bad_keys_and_unfounded():
    raw = {"tickets": [
        {"i": 1, "nodes": ["@topic", "ghost", "@topic"], "found": True,
         "points": [{"t": "Организация  власти", "v": ["власть"], "w": 7}, {"t": ""}]},
        {"i": 2, "nodes": ["@ghost"], "found": True, "points": [{"t": "x"}]},
        {"i": 3, "nodes": ["base"], "found": False, "points": [{"t": "выдумка"}]},
        {"i": 9, "nodes": ["base"], "found": True, "points": [{"t": "чужой номер"}]},
    ]}
    out = normalize_ticket_results(raw, [1, 2, 3], {"topic", "base"})
    assert out[1] == {"nodes": ["topic"], "found": True,
                      "points": [{"text": "Организация власти", "variants": ["власть"], "weight": 2.0}]}
    assert out[2]["found"] is False and out[2]["nodes"] == []          # нет ни одной настоящей темы
    assert out[3] == {"nodes": ["base"], "found": False, "points": []}  # «нет в курсе» — тезисы не берём
    assert 9 not in out


def test_course_block_is_static_prefix():
    nodes = [{"id": 1, "key": "base", "tier": 0, "name": "Основа", "summary": "Кратко"}]
    block = build_course_block(nodes, {1: [("Вопрос?", "Ответ.")]})
    assert block.startswith("[COURSE]\n@base | tier 0 | Основа | Кратко\n  - Вопрос? → Ответ.")
    assert block == build_course_block(nodes, {1: [("Вопрос?", "Ответ.")]})
    assert "{" not in EXAM_MATCHER_SYSTEM_PROMPT.split("OUTPUT JSON SCHEMA")[0].split("CONTRASTIVE")[0]


# --- План по дням ---

def test_required_nodes_include_prereqs_transitively():
    nodes = [
        {"id": 1, "key": "a", "prereq_keys": []},
        {"id": 2, "key": "b", "prereq_keys": ["a"]},
        {"id": 3, "key": "c", "prereq_keys": ["b"]},
        {"id": 4, "key": "d", "prereq_keys": []},
    ]
    assert required_node_ids(nodes, {3}) == {1, 2, 3}
    assert required_node_ids(nodes, {4, 99}) == {4}


def test_lesson_quota_spreads_until_drill_days():
    assert lesson_quota(remaining=20, done_today=0, days_left=12) == 2   # 10 учебных дней + 2 на прогон
    assert lesson_quota(remaining=18, done_today=2, days_left=12) == 2   # норма не плывёт в течение дня
    assert lesson_quota(remaining=5, done_today=0, days_left=2) == 3     # дни прогона, а темы ещё есть
    assert lesson_quota(remaining=3, done_today=0, days_left=0) == 3     # экзамен сегодня
    assert lesson_quota(remaining=0, done_today=0, days_left=10) == 0


# --- Сценарий целиком ---

LESSON = {"screens": [{"say": "a", "emo": "talk", "focus": []}] * 3, "check": []}


def _card(text, layer=1):
    return {"text": text, "secondary_text": "", "translation": "Ответ.", "example": "", "layer": layer,
            "initial_difficulty_tier": "medium", "answer_type": "term", "distractors": ["Н1.", "Н2.", "Н3."]}


async def _fake_build(text, subject, calls=None, course=None, **options):
    from app.services.ai_gateway.path_builder import normalize_map
    path_map = normalize_map({"title": "Курс", "domain": "law", "nodes": [
        {"key": "base", "name": "Основа", "tier": 0, "order": 1},
        {"key": "topic", "name": "Тема", "tier": 1, "order": 2},
        {"key": "other", "name": "Другая тема", "tier": 1, "order": 3},
    ], "edges": []})
    packs = {k: {"lesson": LESSON, "cards": [_card(f"Вопрос {k} {i}?") for i in range(2)]} for k in ("base", "topic", "other")}
    return {"map": path_map, "packs": packs, "missing_nodes": [], "calls": [], "cost_usd": 0.0}


async def _cleanup():
    async with AsyncSessionLocal() as db:
        plan_ids = select(ExamPlan.id).where(ExamPlan.user_id == USER)
        await db.execute(delete(ExamTicket).where(ExamTicket.plan_id.in_(plan_ids)))
        await db.execute(delete(ExamPlan).where(ExamPlan.user_id == USER))
        await wipe_subject(db, USER, SUBJECT)
        await db.execute(delete(GenerationJob).where(GenerationJob.user_id == USER))
        await db.execute(delete(AiTelemetryLog).where(AiTelemetryLog.user_id == USER))
        await db.commit()


async def _build() -> dict:
    await _cleanup()
    async with AsyncSessionLocal() as db:
        job = GenerationJob(user_id=USER, subject=SUBJECT, theme="Т", raw_text="Текст " * 20, status="processing")
        db.add(job)
        await db.commit()
        job_id = job.id
    with patch("app.services.generation_worker.build_learning_path", side_effect=_fake_build):
        await process_generation_job(job_id, is_offpeak=True)
    async with AsyncSessionLocal() as db:
        return {n.node_key: n.id for n in (await db.execute(
            select(KnowledgeNode).where(KnowledgeNode.user_id == USER))).scalars().all()}


async def _learn(db, node_id):
    for c in (await db.execute(select(Card).where(Card.node_id == node_id))).scalars().all():
        c.state, c.next_review = 2, utc_now() + timedelta(days=5)
        db.add(ReviewLog(card_id=c.id, user_id=USER, rating=3, review_time=utc_now(), state=0))
    await db.commit()


async def _fake_match(nodes, cards_by_node, questions, calls):
    calls.append({"label": "exam#1.1", "cost_usd": 0.004, "prompt_tokens": 10, "cache_hit_tokens": 0,
                  "completion_tokens": 5, "duration_ms": 1, "finish_reason": "stop", "model": "deepseek-flash"})
    return [
        {"nodes": ["topic"], "found": True, "points": [{"text": "Тема — главное", "variants": ["главное"], "weight": 2.0}]},
        {"nodes": [], "found": False, "points": []},
    ]


def test_exam_plan_end_to_end():
    from datetime import date

    async def scenario():
        nodes = await _build()
        try:
            async with AsyncSessionLocal() as db:
                plan = await create_plan(db, USER, SUBJECT, "Билеты", date.today() + timedelta(days=10), [
                    {"question": "Расскажите о теме", "answer": ""},
                    {"question": "Вопрос не из книги", "answer": ""},
                ])
                plan_id = plan.id
            with patch("app.services.ai_gateway.exam_matcher.match_tickets", side_effect=_fake_match):
                await run_plan_matching(plan_id)

            async with AsyncSessionLocal() as db:
                ov = await exam_overview(db, USER, SUBJECT)
                assert ov["status"] == "ready" and round(ov["cost_usd"], 4) == 0.004
                assert ov["tickets"]["ok"] == 1 and ov["tickets"]["missing"] == 1
                assert ov["nodes"] == {"required": 2, "done": 0}           # тема + её основа, «другая» не нужна
                assert ov["today"]["quota"] == 1                          # 2 темы на 8 учебных дней
                card = (await db.execute(select(Card).where(Card.user_id == USER, Card.answer_type == TICKET_ANSWER_TYPE))).scalar_one()
                assert card.content_type == "open" and card.key_points[0]["text"] == "Тема — главное"

                # Шаги: урок основы → её карточки → норма выполнена (тема — только сверх нормы)
                step = await next_path_step(db, USER, SUBJECT, [], scope="exam")
                assert step["type"] == "lesson" and step["node_id"] == nodes["base"]
                await complete_lesson(db, USER, nodes["base"], 0)
                await db.commit()
                step = await next_exam_step(db, USER, SUBJECT, ["lesson"])
                assert step["type"] == "cards" and step["node_id"] == nodes["base"]
                await _learn(db, nodes["base"])
                step = await next_exam_step(db, USER, SUBJECT, ["lesson", "cards"])
                assert step["type"] == "done" and step["reason"] == "exam_quota" and step["plan"]["goal_met"] is True
                step = await next_exam_step(db, USER, SUBJECT, [], extra=True)
                assert step["type"] == "lesson" and step["node_id"] == nodes["topic"]
                step = await next_exam_step(db, USER, SUBJECT, ["lesson"], extra=True)
                assert step["type"] == "done"                              # сверх нормы — одна тема за нажатие

                # Билет не выдаётся, пока тема не пройдена; после темы — сразу её билет письменно
                assert await exam_session_cards(db, USER, plan_id, drill=False, limit=None) == []
                await complete_lesson(db, USER, nodes["topic"], 0)
                await db.commit()
                await _learn(db, nodes["topic"])
                step = await next_exam_step(db, USER, SUBJECT, ["lesson", "cards", "lesson", "cards"])
                assert step["type"] == "ticket" and step["count"] == 1
                ready = await exam_session_cards(db, USER, plan_id, drill=False, limit=None)
                assert [c.id for c in ready] == [card.id]

                # Билет вне курса: эталон вписывает пользователь — билет готов сразу
                missing = (await db.execute(select(ExamTicket).where(
                    ExamTicket.plan_id == plan_id, ExamTicket.status == "missing"))).scalar_one()
                await set_ticket_answer(db, USER, missing.id, "- первый тезис / вариант\n- второй тезис")
                ov = await exam_overview(db, USER, SUBJECT)
                assert ov["tickets"]["ok"] == 2 and ov["tickets"]["ready"] == 2

                # Билеты не съедают дневную норму новых карточек обычного пути
                learned = await _learned_today(db, USER, SUBJECT)
                db.add(ReviewLog(card_id=card.id, user_id=USER, rating=3, review_time=utc_now(), state=0))
                await db.commit()
                assert await _learned_today(db, USER, SUBJECT) == learned

                # Выключение: неотвеченные карточки билетов удаляются, отвеченные остаются в повторениях
                card.state = 2
                await db.commit()
                await deactivate_plan(db, USER, SUBJECT)
                left = (await db.execute(select(Card.id).where(Card.user_id == USER, Card.answer_type == TICKET_ANSWER_TYPE))).scalars().all()
                assert left == [card.id]
                assert (await exam_overview(db, USER, SUBJECT)) == {"active": False}
        finally:
            await _cleanup()

    asyncio.run(scenario())


def test_exam_api_validation():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    headers = {"X-Telegram-User-Id": USER}
    r = client.post(f"/api/exam/{SUBJECT}/preview?tg_id={USER}", json={"text": "1. Понятие права\n2. Норма права"}, headers=headers)
    assert r.status_code == 200 and r.json()["count"] == 2
    # Без графа тем режим не включается
    r = client.post(f"/api/exam/{SUBJECT}?tg_id={USER}", json={"exam_date": "2099-01-01", "text": "1. Понятие права"}, headers=headers)
    assert r.status_code == 400
    r = client.get(f"/api/exam/{SUBJECT}?tg_id={USER}", headers=headers)
    assert r.status_code == 200 and r.json() == {"active": False}


def test_matcher_gives_up_on_hanging_api():
    """Перегруженный DeepSeek держит соединение без ответа — разбор не должен висеть вечно."""
    from app.services.ai_gateway import exam_matcher

    async def hang(*args, **kwargs):
        await asyncio.sleep(10)

    async def scenario():
        calls = []
        with patch.object(exam_matcher, "MATCH_HARD_LIMIT_S", 0.05), \
                patch.object(exam_matcher, "_get_call_deepseek", return_value=hang):
            try:
                await exam_matcher.match_tickets(
                    [{"id": 1, "key": "base", "tier": 0, "name": "Основа", "summary": ""}], {}, ["Вопрос"], calls)
            except exam_matcher.ExamMatchError:
                pass
            else:
                raise AssertionError("ожидалась ошибка разбора")
        assert len(calls) == 2 and all(c["error"] for c in calls)

    asyncio.run(scenario())
