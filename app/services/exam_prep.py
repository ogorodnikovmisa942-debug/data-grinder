"""
Подготовка к экзамену по билетам (отдельный режим «Пути знаний»).

Пользователь вставляет список билетов и дату экзамена. ИИ один раз сопоставляет каждый билет с темами
графа курса и пишет ключевые тезисы эталона из карточек этих тем (exam_matcher). Дальше всё считается без ИИ:
- нужные темы = темы билетов + все их пререквизиты (без них тема не откроется);
- норма уроков на день = оставшиеся нужные темы / дни до экзамена за вычетом последних EXAM_RESERVE_DAYS;
- после темы и её карточек — её билет письменно (проверка по тезисам);
- последние дни — прогон билетов, начиная с самых шатких;
- практика — только по темам билетов.
Билет, которого нет в курсе, не выдумывается: пользователь вписывает эталон сам или пропускает билет.
"""
import asyncio
import re
from datetime import date, timedelta
from math import ceil

from sqlalchemy import select, func, or_, delete

from app.core.timeutil import user_day_start, get_user_timezone, local_now
from app.database.models import (
    Card, Phrase, ReviewLog, KnowledgeNode, NodeProgress, ExamPlan, ExamTicket, PracticeSessionLog, utc_now,
)
from app.services.open_answer import points_from_bullets, derive_key_points

TICKET_ANSWER_TYPE = "exam_ticket"
EXAM_RESERVE_DAYS = 2       # последние дни перед экзаменом — прогон билетов
TICKETS_PER_STEP = 5        # новых билетов за шаг занятия
DRILL_PER_STEP = 8          # билетов в прогоне за шаг
MATCHING_STALE_MINUTES = 8   # разбор дольше этого — сорвался (перезапуск сервера); 2 попытки по 150 с укладываются
EXAM_STEP_LIMITS = {"review": 3, "cards": 6, "lesson": 8, "ticket": 4, "drill": 2, "practice": 1}
MIN_TICKETS = 1
MAX_TICKETS = 150

_BULLET_OR_ANSWER = re.compile(r"^\s*(?:[-•*–]\s+|ответ\s*[:.)]|a\s*[:.)])", re.IGNORECASE)
_ANSWER_MARK = re.compile(r"^\s*(?:ответ|a)\s*[:.)]\s*", re.IGNORECASE)
_TICKET_PREFIX = re.compile(r"^\s*(?:билет\s*(?:№\s*)?\d+\s*[.):-]?\s*|№?\s*\d+\s*[.)]\s*|вопрос\s*[:.)]\s*)", re.IGNORECASE)


def ticket_card_filter():
    """Условие «не карточка-билет»: билеты не попадают в обычные новые карточки и в дневную норму."""
    return or_(Card.answer_type.is_(None), Card.answer_type != TICKET_ANSWER_TYPE)


def parse_ticket_list(blob: str) -> list[dict]:
    """
    Список билетов. Обычно это строка на билет («1. Понятие государства»), иногда — блоки «вопрос + ответ»,
    разделённые пустой строкой (как в «Своих вопросах»). Блочный формат узнаём по ответу под вопросом:
    пункты списка или метка «Ответ:».
    """
    text = (blob or "").replace("\r\n", "\n").strip()
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    has_answers = any(
        len(lines) > 1 and any(_BULLET_OR_ANSWER.match(l) for l in lines[1:])
        for lines in ([l for l in b.split("\n") if l.strip()] for b in blocks)
    )
    if has_answers:
        # Блок: первая строка — билет, остальное — эталон (метки «Вопрос:»/«Ответ:» не обязательны)
        items = []
        for b in blocks:
            lines = [l.strip() for l in b.split("\n") if l.strip()]
            q = _TICKET_PREFIX.sub("", lines[0], count=1).strip()
            answer = "\n".join([_ANSWER_MARK.sub("", lines[1], count=1)] + lines[2:]).strip() if len(lines) > 1 else ""
            if len(q) >= 3:
                items.append({"question": q[:500], "answer": answer[:3000]})
    else:
        items = []
        for line in text.split("\n"):
            q = _TICKET_PREFIX.sub("", line.strip(), count=1).strip(" \t-–•")
            if len(q) >= 3:
                items.append({"question": q[:500], "answer": ""})
    seen, unique = set(), []
    for it in items:
        key = it["question"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(it)
    return unique[:MAX_TICKETS]


def ticket_fields_from_points(points: list[dict]) -> dict:
    reference = "\n".join(f"• {p['text']}" for p in points)
    return {"translation": reference, "key_points": points}


def ticket_fields_from_answer(answer: str) -> dict:
    """Эталон, вписанный пользователем: пункты «- тезис / вариант» или свободный текст."""
    bullets = points_from_bullets(answer)
    if bullets:
        reference = "\n".join(f"• {p['text']}" + (f" ({', '.join(p['variants'])})" if p["variants"] else "") for p in bullets)
        return {"translation": reference, "key_points": bullets}
    return {"translation": answer.strip(), "key_points": derive_key_points(answer) or None}


# ---------------------------------------------------------------------------
# Создание плана и разбор билетов
# ---------------------------------------------------------------------------

async def get_active_plan(db, user_id: str, subject: str) -> ExamPlan | None:
    return (await db.execute(
        select(ExamPlan).where(ExamPlan.user_id == user_id, ExamPlan.subject == subject, ExamPlan.active == True)  # noqa: E712
        .order_by(ExamPlan.id.desc()).limit(1)
    )).scalar_one_or_none()


async def _drop_unstarted_ticket_cards(db, user_id: str, plan_ids: list[int]) -> None:
    """Карточки билетов, которые ни разу не отвечались, удаляем; отвеченные остаются в повторениях FSRS."""
    if not plan_ids:
        return
    card_ids = select(ExamTicket.card_id).where(ExamTicket.plan_id.in_(plan_ids), ExamTicket.card_id.isnot(None))
    await db.execute(delete(Card).where(Card.user_id == user_id, Card.id.in_(card_ids), Card.state == 0))


async def create_plan(db, user_id: str, subject: str, title: str, exam_date: date, items: list[dict]) -> ExamPlan:
    """Новый план заменяет прежний активный план предмета."""
    old = (await db.execute(
        select(ExamPlan.id).where(ExamPlan.user_id == user_id, ExamPlan.subject == subject, ExamPlan.active == True)  # noqa: E712
    )).scalars().all()
    if old:
        await _drop_unstarted_ticket_cards(db, user_id, list(old))
        for p in (await db.execute(select(ExamPlan).where(ExamPlan.id.in_(old)))).scalars().all():
            p.active = False
    plan = ExamPlan(user_id=user_id, subject=subject, title=(title or "Билеты").strip()[:120] or "Билеты",
                    exam_date=exam_date, status="matching", active=True, cost_usd=0.0)
    db.add(plan)
    await db.flush()
    for k, it in enumerate(items, start=1):
        db.add(ExamTicket(plan_id=plan.id, user_id=user_id, order_idx=k, question=it["question"],
                          user_answer=(it.get("answer") or "").strip() or None, node_ids=[], status="pending"))
    await db.commit()
    return plan


async def _course_for_matching(db, user_id: str, subject: str) -> tuple[list[dict], dict]:
    nodes = (await db.execute(
        select(KnowledgeNode).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject,
                                    KnowledgeNode.node_key != "__intro__")
        .order_by(KnowledgeNode.tier, KnowledgeNode.order_idx, KnowledgeNode.id)
    )).scalars().all()
    rows = (await db.execute(
        select(Card.node_id, Card.text, Card.translation)
        .where(Card.user_id == user_id, Card.subject == subject, Card.node_id.isnot(None))
        .order_by(Card.node_id, Card.topological_rank, Card.id)
    )).all()
    cards_by_node: dict = {}
    for node_id, q, a in rows:
        cards_by_node.setdefault(node_id, []).append((q or "", a or ""))
    return ([{"id": n.id, "key": n.node_key, "tier": n.tier, "name": n.name, "summary": n.summary or ""} for n in nodes],
            cards_by_node)


async def _upsert_ticket_card(db, plan: ExamPlan, ticket: ExamTicket, fields: dict, phrase_id: int | None) -> None:
    card = None
    if ticket.card_id:
        card = (await db.execute(select(Card).where(Card.id == ticket.card_id, Card.user_id == plan.user_id))).scalar_one_or_none()
    if card:
        card.translation = fields["translation"]
        card.key_points = fields["key_points"]
        return
    card = Card(
        phrase_id=phrase_id, user_id=plan.user_id, subject=plan.subject, content_type="open",
        answer_type=TICKET_ANSWER_TYPE, state=0, next_review=utc_now(), has_seen_intro=True,
        text=ticket.question, secondary_text=f"Билет {ticket.order_idx}", layer=1, **fields,
    )
    db.add(card)
    await db.flush()
    ticket.card_id = card.id


async def _plan_phrase_id(db, plan: ExamPlan) -> int:
    phrase = (await db.execute(
        select(Phrase).where(Phrase.user_id == plan.user_id, Phrase.subject == plan.subject, Phrase.text == plan.title)
        .order_by(Phrase.id.desc()).limit(1)
    )).scalar_one_or_none()
    if not phrase:
        phrase = Phrase(text=plan.title, subject=plan.subject, user_id=plan.user_id)
        db.add(phrase)
        await db.flush()
    return phrase.id


async def run_plan_matching(plan_id: int) -> None:
    """Фоновая задача: ИИ сопоставляет билеты с графом, затем создаются карточки билетов."""
    from app.database.session import AsyncSessionLocal
    from app.services.ai_gateway.exam_matcher import match_tickets

    async with AsyncSessionLocal() as db:
        plan = (await db.execute(select(ExamPlan).where(ExamPlan.id == plan_id))).scalar_one_or_none()
        if not plan:
            return
        user_id, subject = plan.user_id, plan.subject
        tickets = (await db.execute(
            select(ExamTicket).where(ExamTicket.plan_id == plan_id).order_by(ExamTicket.order_idx)
        )).scalars().all()
        questions = [t.question for t in tickets]
        nodes, cards_by_node = await _course_for_matching(db, user_id, subject)

    calls: list[dict] = []
    error = None
    results: list[dict] = []
    if not nodes:
        error = "У предмета нет графа тем: сначала загрузите книгу."
    else:
        try:
            results = await match_tickets(nodes, cards_by_node, questions, calls)
        except Exception as e:  # noqa: BLE001
            print(f"[Exam WARN] план {plan_id}: {e}", flush=True)
            error = "DeepSeek сейчас не ответил — похоже, перегружен. Попробуй ещё раз через пару минут."

    async with AsyncSessionLocal() as db:
        plan = (await db.execute(select(ExamPlan).where(ExamPlan.id == plan_id))).scalar_one_or_none()
        if not plan:
            return
        plan.cost_usd = round((plan.cost_usd or 0) + sum(c.get("cost_usd", 0.0) for c in calls), 6)
        if error:
            plan.status, plan.error = "failed", error
            await db.commit()
        else:
            id_by_key = {n["key"]: n["id"] for n in nodes}
            tickets = (await db.execute(
                select(ExamTicket).where(ExamTicket.plan_id == plan_id).order_by(ExamTicket.order_idx)
            )).scalars().all()
            phrase_id = await _plan_phrase_id(db, plan)
            for t, res in zip(tickets, results):
                t.node_ids = [id_by_key[k] for k in res["nodes"] if k in id_by_key]
                if t.user_answer:
                    t.status = "ok"
                    await _upsert_ticket_card(db, plan, t, ticket_fields_from_answer(t.user_answer), phrase_id)
                elif res["found"]:
                    t.status = "ok"
                    await _upsert_ticket_card(db, plan, t, ticket_fields_from_points(res["points"]), phrase_id)
                else:
                    t.status = "missing"
            plan.status, plan.error = "ready", None
            await db.commit()

    if calls:
        from app.services.generation_worker import _record_path_calls
        await _record_path_calls(f"exam:{plan_id}", user_id, calls)


def start_matching(plan_id: int) -> None:
    asyncio.create_task(run_plan_matching(plan_id))


async def set_ticket_answer(db, user_id: str, ticket_id: int, answer: str) -> ExamTicket:
    ticket, plan = await _own_ticket(db, user_id, ticket_id)
    ticket.user_answer = answer.strip()
    ticket.status = "ok"
    await _upsert_ticket_card(db, plan, ticket, ticket_fields_from_answer(ticket.user_answer), await _plan_phrase_id(db, plan))
    await db.commit()
    return ticket


async def skip_ticket(db, user_id: str, ticket_id: int) -> ExamTicket:
    ticket, _ = await _own_ticket(db, user_id, ticket_id)
    ticket.status = "skipped"
    await db.commit()
    return ticket


async def _own_ticket(db, user_id: str, ticket_id: int) -> tuple[ExamTicket, ExamPlan]:
    row = (await db.execute(
        select(ExamTicket, ExamPlan).join(ExamPlan, ExamTicket.plan_id == ExamPlan.id)
        .where(ExamTicket.id == ticket_id, ExamTicket.user_id == user_id)
    )).first()
    if not row:
        raise LookupError("Билет не найден")
    return row[0], row[1]


async def deactivate_plan(db, user_id: str, subject: str) -> None:
    plan = await get_active_plan(db, user_id, subject)
    if plan:
        await _drop_unstarted_ticket_cards(db, user_id, [plan.id])
        plan.active = False
        await db.commit()


# ---------------------------------------------------------------------------
# План по дням
# ---------------------------------------------------------------------------

def required_node_ids(path_nodes: list[dict], ticket_node_ids: set[int]) -> set[int]:
    """Темы билетов и все их пререквизиты (транзитивно): без пререквизитов тема не откроется."""
    by_key = {n["key"]: n for n in path_nodes}
    by_id = {n["id"]: n for n in path_nodes}
    stack = [by_id[i]["key"] for i in ticket_node_ids if i in by_id]
    seen: set[str] = set()
    while stack:
        key = stack.pop()
        if key in seen or key not in by_key:
            continue
        seen.add(key)
        stack.extend(by_key[key].get("prereq_keys") or [])
    return {by_key[k]["id"] for k in seen}


def lesson_quota(remaining: int, done_today: int, days_left: int) -> int:
    """Сколько уроков нужных тем пройти сегодня, чтобы успеть до прогона (норма не меняется в течение дня)."""
    learn_days = max(1, days_left - EXAM_RESERVE_DAYS) if days_left > EXAM_RESERVE_DAYS else max(1, days_left)
    total = remaining + done_today
    return ceil(total / learn_days) if total else 0


async def _plan_context(db, user_id: str, plan: ExamPlan) -> dict:
    from app.services.knowledge_path import get_path_state
    state = await get_path_state(db, user_id, plan.subject)
    nodes = state["nodes"]
    tickets = (await db.execute(
        select(ExamTicket).where(ExamTicket.plan_id == plan.id).order_by(ExamTicket.order_idx)
    )).scalars().all()
    active = [t for t in tickets if t.status == "ok"]
    required = required_node_ids(nodes, {i for t in active for i in (t.node_ids or [])})
    status = {n["id"]: n["status"] for n in nodes}
    studied = {i for i, s in status.items() if s in ("lesson_done", "mastered")}
    today = await user_day_start(db, user_id)
    done_today = set((await db.execute(
        select(NodeProgress.node_id).where(NodeProgress.user_id == user_id, NodeProgress.lesson_done_at >= today)
    )).scalars().all())
    days_left = (plan.exam_date - local_now(await get_user_timezone(db, user_id)).date()).days
    remaining = len(required - studied)
    cards = {}
    card_ids = [t.card_id for t in active if t.card_id]
    if card_ids:
        cards = {c.id: c for c in (await db.execute(select(Card).where(Card.id.in_(card_ids)))).scalars().all()}
    return {
        "state": state, "nodes": nodes, "tickets": tickets, "active": active, "required": required,
        "studied": studied, "status": status, "today": today, "days_left": days_left,
        "remaining": remaining, "done_today": len(required & done_today),
        "quota": lesson_quota(remaining, len(required & done_today), days_left), "cards": cards,
    }


def _ticket_ready(t: ExamTicket, studied: set[int]) -> bool:
    return t.status == "ok" and t.card_id is not None and all(i in studied for i in (t.node_ids or []))


async def exam_overview(db, user_id: str, subject: str) -> dict:
    plan = await get_active_plan(db, user_id, subject)
    if not plan:
        return {"active": False}
    if plan.status == "matching" and plan.created_at < utc_now() - timedelta(minutes=MATCHING_STALE_MINUTES):
        plan.status, plan.error = "failed", "Разбор прервался. Попробуйте ещё раз."
        await db.commit()
    base = {
        "active": True, "id": plan.id, "title": plan.title, "status": plan.status, "error": plan.error,
        "exam_date": plan.exam_date.isoformat(), "cost_usd": plan.cost_usd,
    }
    if plan.status != "ready":
        tickets = (await db.execute(select(func.count(ExamTicket.id)).where(ExamTicket.plan_id == plan.id))).scalar() or 0
        return {**base, "tickets": {"total": tickets}}

    ctx = await _plan_context(db, user_id, plan)
    names = {n["id"]: n["name"] for n in ctx["nodes"]}
    items = []
    for t in ctx["tickets"]:
        card = ctx["cards"].get(t.card_id)
        items.append({
            "id": t.id, "n": t.order_idx, "question": t.question, "status": t.status,
            "nodes": [names[i] for i in (t.node_ids or []) if i in names],
            "ready": _ticket_ready(t, ctx["studied"]),
            "answered": bool(card and card.state != 0),
            "strong": bool(card and card.state == 2),
            "user_answer": t.user_answer,
        })
    days_left = ctx["days_left"]
    return {
        **base,
        "days_left": days_left,
        "phase": "past" if days_left < 0 else ("drill" if days_left <= EXAM_RESERVE_DAYS else "learn"),
        "tickets": {
            "total": len(items),
            "ok": sum(1 for x in items if x["status"] == "ok"),
            "missing": sum(1 for x in items if x["status"] == "missing"),
            "skipped": sum(1 for x in items if x["status"] == "skipped"),
            "ready": sum(1 for x in items if x["ready"]),
            "answered": sum(1 for x in items if x["answered"]),
            "strong": sum(1 for x in items if x["strong"]),
        },
        "nodes": {"required": len(ctx["required"]), "done": len(ctx["required"] & ctx["studied"])},
        "today": {"lessons": ctx["done_today"], "quota": ctx["quota"]},
        "items": items,
    }


async def plan_required_node_ids(db, user_id: str, plan_id: int) -> set[int]:
    plan = (await db.execute(
        select(ExamPlan).where(ExamPlan.id == plan_id, ExamPlan.user_id == user_id)
    )).scalar_one_or_none()
    if not plan or plan.status != "ready":
        return set()
    return (await _plan_context(db, user_id, plan))["required"]


async def exam_session_cards(db, user_id: str, plan_id: int, drill: bool, limit: int | None) -> list[Card]:
    """Карточки билетов для шага занятия: новые готовые билеты или прогон (сначала самые шаткие)."""
    plan = (await db.execute(
        select(ExamPlan).where(ExamPlan.id == plan_id, ExamPlan.user_id == user_id)
    )).scalar_one_or_none()
    if not plan or plan.status != "ready":
        return []
    ctx = await _plan_context(db, user_id, plan)
    if drill:
        reviewed_today = set((await db.execute(
            select(ReviewLog.card_id).where(ReviewLog.user_id == user_id, ReviewLog.review_time >= ctx["today"])
        )).scalars().all())
        pool = [ctx["cards"][t.card_id] for t in ctx["active"]
                if t.card_id in ctx["cards"] and ctx["cards"][t.card_id].state != 0 and t.card_id not in reviewed_today]
        pool.sort(key=lambda c: (c.stability or 0, -(c.lapses or 0)))
        return pool[:limit or DRILL_PER_STEP]
    pool = [ctx["cards"][t.card_id] for t in ctx["active"]
            if _ticket_ready(t, ctx["studied"]) and t.card_id in ctx["cards"] and ctx["cards"][t.card_id].state == 0]
    return pool[:limit or TICKETS_PER_STEP]


# ---------------------------------------------------------------------------
# Следующий шаг занятия в режиме экзамена
# ---------------------------------------------------------------------------

async def next_exam_step(db, user_id: str, subject: str, done: list[str], extra: bool = False) -> dict:
    """
    Порядок: повторения → доучить начатую тему → её билеты письменно → в последние дни прогон →
    урок следующей нужной темы (по норме дня) → практика по темам билетов → итог.
    """
    from app.services.knowledge_path import (
        get_day_plan, get_today_summary, complete_lesson, RUN_REVIEW_BATCH, PRACTICE_MIN_ITEMS,
    )
    plan = await get_active_plan(db, user_id, subject)
    if not plan or plan.status != "ready":
        return {"type": "done", "scope": "exam", "reason": "exam_off", "plan": await get_day_plan(db, user_id, subject),
                "today": await get_today_summary(db, user_id, subject)}
    used = {t: done.count(t) for t in EXAM_STEP_LIMITS}
    now = utc_now()
    base = [Card.user_id == user_id, Card.subject == subject]

    due = (await db.execute(
        select(func.count(Card.id)).where(*base, Card.state.in_([1, 2, 3]), Card.next_review <= now)
    )).scalar() or 0
    if due and used["review"] < EXAM_STEP_LIMITS["review"]:
        return {"type": "review", "count": min(due, RUN_REVIEW_BATCH), "remaining": due}

    day = await get_day_plan(db, user_id, subject)
    if day["unfinished"] and used["cards"] < EXAM_STEP_LIMITS["cards"]:
        u = day["unfinished"]
        return {"type": "cards", "node_id": u["node_id"], "node_name": u["node_name"], "count": u["count"]}

    ctx = await _plan_context(db, user_id, plan)
    days_left = ctx["days_left"]
    ready_new = [t for t in ctx["active"] if _ticket_ready(t, ctx["studied"])
                 and ctx["cards"].get(t.card_id) is not None and ctx["cards"][t.card_id].state == 0]
    if ready_new and used["ticket"] < EXAM_STEP_LIMITS["ticket"]:
        return {"type": "ticket", "plan_id": plan.id, "count": min(len(ready_new), TICKETS_PER_STEP), "remaining": len(ready_new)}

    drill_phase = 0 <= days_left <= EXAM_RESERVE_DAYS
    answered = [t for t in ctx["active"] if ctx["cards"].get(t.card_id) is not None and ctx["cards"][t.card_id].state != 0]
    if drill_phase and answered and used["drill"] < EXAM_STEP_LIMITS["drill"]:
        cards = await exam_session_cards(db, user_id, plan.id, drill=True, limit=None)
        if cards:
            return {"type": "drill", "plan_id": plan.id, "count": len(cards)}

    # Урок следующей нужной темы — пока не выполнена норма дня (или тема сверх нормы по явному выбору)
    opened = sorted((n for n in ctx["nodes"] if n["id"] in ctx["required"] and n["status"] == "open"),
                    key=lambda n: (n["tier"], n["order"]))
    quota_left = ctx["done_today"] < ctx["quota"]
    # «Ещё тема» сверх нормы — ровно одна тема за нажатие
    if days_left >= 0 and opened and (quota_left or (extra and not used["lesson"])) and used["lesson"] < EXAM_STEP_LIMITS["lesson"]:
        n = opened[0]
        if n.get("lesson_status") != "ready":
            await complete_lesson(db, user_id, n["id"], 0)
            await db.commit()
            return await next_exam_step(db, user_id, subject, done, extra)
        return {"type": "lesson", "node_id": n["id"], "node_name": n["name"], "tier": n["tier"], "cards": n["cards_total"]}

    if used["practice"] < EXAM_STEP_LIMITS["practice"]:
        practiced_today = (await db.execute(
            select(func.count(PracticeSessionLog.id)).where(
                PracticeSessionLog.user_id == user_id, PracticeSessionLog.subject == subject,
                PracticeSessionLog.created_at >= ctx["today"],
            )
        )).scalar() or 0
        if not practiced_today and day["practice_items"] >= PRACTICE_MIN_ITEMS and ctx["required"] & ctx["studied"]:
            return {"type": "practice", "plan_id": plan.id, "count": min(8, day["practice_items"])}

    if days_left < 0:
        reason = "exam_past"
    elif opened and not quota_left:
        reason = "exam_quota"
    elif not ctx["remaining"] and not ready_new:
        reason = "exam_ready"
    elif due:
        reason = "reviews_left"
    else:
        reason = "exam_waiting"
    # Цель дня в режиме экзамена — норма уроков нужных тем выполнена
    day["goal_met"] = reason == "exam_quota" and ctx["done_today"] >= 1 and not day["unfinished"]
    return {
        "type": "done", "scope": "exam", "reason": reason,
        "next_up": opened[0]["name"] if opened else None,
        "days_left": days_left, "quota": ctx["quota"], "lessons_today": ctx["done_today"],
        "plan": day, "today": await get_today_summary(db, user_id, subject),
    }
