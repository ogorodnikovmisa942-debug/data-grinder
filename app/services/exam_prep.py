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
import random
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
MAX_LESSONS_PER_DAY = 3     # больше трёх новых тем в день не вытягиваем: тогда аварийный режим предлагает отложить часть билетов
PACE_DAYS = 7               # по скольким последним дням считаем реальный темп уроков
SIMULATOR_SECONDS = 900     # время на билет в симуляторе: 15 минут
REMINDER_MAX_DAYS = 60      # напоминаем о приближающемся экзамене не раньше чем за два месяца

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


async def run_plan_matching(plan_id: int, only_missing: bool = False) -> int:
    """Фоновая задача: ИИ сопоставляет билеты с графом, затем создаются карточки билетов.
    only_missing — повторный разбор только билетов «нет в книге» после добавления нового материала (план остаётся готовым;
    сбой ничего не портит). Возвращает, сколько билетов нашлось в курсе."""
    from app.database.session import AsyncSessionLocal
    from app.services.ai_gateway.exam_matcher import match_tickets

    async with AsyncSessionLocal() as db:
        plan = (await db.execute(select(ExamPlan).where(ExamPlan.id == plan_id))).scalar_one_or_none()
        if not plan:
            return 0
        user_id, subject = plan.user_id, plan.subject
        tickets = (await db.execute(
            select(ExamTicket).where(ExamTicket.plan_id == plan_id).order_by(ExamTicket.order_idx)
        )).scalars().all()
        if only_missing:
            tickets = [t for t in tickets if t.status == "missing"]
            if not tickets:
                return 0
        ticket_ids = [t.id for t in tickets]
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

    closed = 0
    async with AsyncSessionLocal() as db:
        plan = (await db.execute(select(ExamPlan).where(ExamPlan.id == plan_id))).scalar_one_or_none()
        if not plan:
            return 0
        plan.cost_usd = round((plan.cost_usd or 0) + sum(c.get("cost_usd", 0.0) for c in calls), 6)
        if error:
            if not only_missing:
                plan.status, plan.error = "failed", error
            await db.commit()
        else:
            id_by_key = {n["key"]: n["id"] for n in nodes}
            tickets = (await db.execute(
                select(ExamTicket).where(ExamTicket.id.in_(ticket_ids)).order_by(ExamTicket.order_idx)
            )).scalars().all()
            phrase_id = await _plan_phrase_id(db, plan)
            for t, res in zip(tickets, results):
                t.node_ids = [id_by_key[k] for k in res["nodes"] if k in id_by_key]
                if t.user_answer:
                    t.status = "ok"
                    await _upsert_ticket_card(db, plan, t, ticket_fields_from_answer(t.user_answer), phrase_id)
                elif res["found"]:
                    t.status = "ok"
                    closed += 1
                    await _upsert_ticket_card(db, plan, t, ticket_fields_from_points(res["points"]), phrase_id)
                else:
                    t.status = "missing"
            plan.status, plan.error = "ready", None
            await db.commit()

    if calls:
        from app.services.generation_worker import _record_path_calls
        await _record_path_calls(f"exam:{plan_id}", user_id, calls)
    return closed


def start_matching(plan_id: int, only_missing: bool = False) -> None:
    asyncio.create_task(run_plan_matching(plan_id, only_missing))


async def rematch_after_new_source(user_id: str, subject: str) -> dict | None:
    """Загружен новый материал: билеты «нет в книге» ищутся заново, остальные не трогаются (дёшево: один разбор только недостающих).
    Возвращает {"closed", "missing"} или None, если плана или недостающих билетов нет."""
    from app.database.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        plan = await get_active_plan(db, user_id, subject)
        if not plan or plan.status != "ready":
            return None
        missing = (await db.execute(
            select(func.count(ExamTicket.id)).where(ExamTicket.plan_id == plan.id, ExamTicket.status == "missing")
        )).scalar() or 0
        plan_id = plan.id
    if not missing:
        return None
    closed = await run_plan_matching(plan_id, only_missing=True)
    return {"closed": closed, "missing": missing, "plan_id": plan_id}


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
    emergency = _emergency(ctx)
    return {
        **base,
        "days_left": days_left,
        "phase": "past" if days_left < 0 else ("drill" if days_left <= EXAM_RESERVE_DAYS else "learn"),
        "readiness": {"percent": readiness_percent(ctx), **exam_forecast(ctx, await lesson_pace(db, user_id, plan.subject))},
        "emergency": {"needed": emergency["needed"], "capacity": emergency["capacity"], "postpone": len(emergency["postpone"]),
                      "postponed_now": len(emergency["postponed_now"])},
        "tickets": {
            "total": len(items),
            "ok": sum(1 for x in items if x["status"] == "ok"),
            "missing": sum(1 for x in items if x["status"] == "missing"),
            "skipped": sum(1 for x in items if x["status"] == "skipped"),
            "postponed": sum(1 for x in items if x["status"] == "postponed"),
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
# Готовность, прогноз, аварийный режим (всё считает код, без ИИ)
# ---------------------------------------------------------------------------

def ticket_strength(card, nodes_share: float, ready: bool, days_left: int) -> float:
    """Насколько билет готов, от 0 до 1: закреплён надолго (запомнится до экзамена) = 1; в повторениях, но стойкость короче срока = 0,7;
    отвечен, ещё заучивается = 0,4; темы пройдены, билет не отвечен = 0,15; иначе — доля пройденных тем билета, умноженная на 0,1."""
    if card is not None and card.state == 2:
        return 1.0 if (card.stability or 0) >= max(1, days_left) else 0.7
    if card is not None and card.state in (1, 3):
        return 0.4
    return 0.15 if ready else 0.1 * nodes_share


def readiness_percent(ctx: dict) -> int:
    """Готовность к экзамену в процентах: средняя готовность билетов плана (отложенные и «нет в книге» не считаются)."""
    active = ctx["active"]
    if not active:
        return 0
    days_left = max(0, ctx["days_left"])
    total = 0.0
    for t in active:
        ids = list(t.node_ids or [])
        share = (sum(1 for i in ids if i in ctx["studied"]) / len(ids)) if ids else 0.0
        total += ticket_strength(ctx["cards"].get(t.card_id), share, _ticket_ready(t, ctx["studied"]), days_left)
    return round(100 * total / len(active))


def ticket_needs(ctx: dict) -> dict[int, frozenset]:
    """Для каждого билета — какие ещё не пройденные темы нужны ему (с пререквизитами)."""
    return {t.id: frozenset(required_node_ids(ctx["nodes"], set(t.node_ids or [])) - ctx["studied"]) for t in ctx["active"]}


def greedy_tickets(tickets: list, needs: dict[int, frozenset], capacity: int) -> tuple[list, set]:
    """Жадный выбор билетов, которые можно закрыть при запасе capacity новых тем: каждый раз берём билет, которому нужно меньше всего
    ещё не взятых тем (билеты на уже пройденных темах — бесплатно). Возвращает (выбранные билеты, темы, которые придётся пройти)."""
    pool = sorted(tickets, key=lambda t: t.order_idx)
    chosen, covered = [], set()
    while pool:
        best = min(pool, key=lambda t: len(needs[t.id] - covered))
        add = needs[best.id] - covered
        if len(covered) + len(add) > capacity:
            break
        chosen.append(best)
        covered |= add
        pool.remove(best)
    return chosen, covered


async def lesson_pace(db, user_id: str, subject: str) -> float:
    """Темп: уроков в день за последние PACE_DAYS дней."""
    since = utc_now() - timedelta(days=PACE_DAYS)
    done = (await db.execute(
        select(func.count(NodeProgress.id)).join(KnowledgeNode, KnowledgeNode.id == NodeProgress.node_id)
        .where(NodeProgress.user_id == user_id, KnowledgeNode.subject == subject, NodeProgress.lesson_done_at >= since)
    )).scalar() or 0
    return done / PACE_DAYS


def exam_forecast(ctx: dict, pace: float) -> dict:
    """«Что успеете к экзамену»: при нынешнем темпе (нет истории — считаем, что идёте по плану) сколько новых тем пройдёте до прогона
    и сколько билетов это закроет."""
    learn_days = max(0, ctx["days_left"] - EXAM_RESERVE_DAYS)
    effective = pace if pace > 0 else ctx["quota"]
    capacity = int(round(effective * learn_days))
    chosen, _ = greedy_tickets(ctx["active"], ticket_needs(ctx), capacity)
    return {
        "pace": round(pace, 2), "needed_pace": round(ctx["remaining"] / max(1, learn_days), 2), "capacity": capacity,
        "tickets_closable": len(chosen), "tickets_total": len(ctx["active"]), "will_make_it": ctx["remaining"] <= capacity,
    }


def _emergency(ctx: dict) -> dict:
    learn_days = max(0, ctx["days_left"] - EXAM_RESERVE_DAYS)
    capacity = learn_days * MAX_LESSONS_PER_DAY
    chosen, covered = greedy_tickets(ctx["active"], ticket_needs(ctx), capacity)
    keep = {t.id for t in chosen}
    needed = ctx["days_left"] >= 0 and ctx["remaining"] > capacity
    return {
        "needed": needed, "days_left": ctx["days_left"], "capacity": capacity, "remaining_lessons": ctx["remaining"],
        "keep": len(chosen), "lessons_after": len(covered),
        "postpone": [{"id": t.id, "n": t.order_idx, "question": t.question} for t in ctx["active"] if t.id not in keep],
        "postponed_now": [{"id": t.id, "n": t.order_idx, "question": t.question} for t in ctx["tickets"] if t.status == "postponed"],
    }


async def emergency_overview(db, user_id: str, subject: str) -> dict:
    """Аварийный режим: если новых тем больше, чем можно пройти (MAX_LESSONS_PER_DAY в день), показываем, какие билеты закрываются
    минимумом тем и какие разумно отложить."""
    plan = await get_active_plan(db, user_id, subject)
    if not plan or plan.status != "ready":
        return {"available": False}
    return {"available": True, **_emergency(await _plan_context(db, user_id, plan))}


async def apply_emergency(db, user_id: str, subject: str) -> dict:
    """Откладывает билеты, которые не помещаются (status postponed): они выходят из плана, пока их не вернут."""
    plan = await get_active_plan(db, user_id, subject)
    if not plan or plan.status != "ready":
        raise LookupError("План не готов")
    info = _emergency(await _plan_context(db, user_id, plan))
    ids = [p["id"] for p in info["postpone"]] if info["needed"] else []
    if ids:
        for t in (await db.execute(select(ExamTicket).where(ExamTicket.id.in_(ids)))).scalars().all():
            t.status = "postponed"
        await db.commit()
    return {"postponed": len(ids)}


async def undo_emergency(db, user_id: str, subject: str) -> dict:
    plan = await get_active_plan(db, user_id, subject)
    if not plan:
        raise LookupError("План не найден")
    rows = (await db.execute(
        select(ExamTicket).where(ExamTicket.plan_id == plan.id, ExamTicket.status == "postponed")
    )).scalars().all()
    for t in rows:
        t.status = "ok"
    await db.commit()
    return {"restored": len(rows)}


# ---------------------------------------------------------------------------
# Симулятор экзамена: случайный билет, таймер на клиенте, проверка по тезисам (без записи в повторения)
# ---------------------------------------------------------------------------

async def simulator_draw(db, user_id: str, subject: str, exclude: list[int] | None = None) -> dict:
    plan = await get_active_plan(db, user_id, subject)
    if not plan or plan.status != "ready":
        raise LookupError("План не готов")
    ctx = await _plan_context(db, user_id, plan)
    pool = [t for t in ctx["active"] if ctx["cards"].get(t.card_id) is not None and _ticket_ready(t, ctx["studied"])]
    if not pool:
        raise LookupError("Пока нет билетов, темы которых пройдены")
    fresh = [t for t in pool if t.id not in set(exclude or [])] or pool
    t = random.choice(fresh)
    card = ctx["cards"][t.card_id]
    return {"ticket_id": t.id, "n": t.order_idx, "question": t.question, "seconds": SIMULATOR_SECONDS,
            "points": len(card.key_points or []), "pool": len(pool), "left": max(0, len(fresh) - 1)}


async def simulator_check(db, user_id: str, ticket_id: int, answer: str) -> dict:
    """Ответ на билет сверяется с тезисами эталона: что названо, что названо частично, что упущено."""
    from app.services.open_answer import grade_answer
    ticket, _ = await _own_ticket(db, user_id, ticket_id)
    card = (await db.execute(select(Card).where(Card.id == ticket.card_id, Card.user_id == user_id))).scalar_one_or_none() if ticket.card_id else None
    if not card:
        raise LookupError("У билета нет эталона")
    res = grade_answer(answer, card.key_points, card.translation)
    pts = res["points"]
    return {
        "percent": res["percent"], "matched": res["matched"], "total": res["total"], "graded": res["graded"],
        "hit": [p["text"] for p in pts if p["matched"]],
        "partial": [p["text"] for p in pts if p["partial"] and not p["matched"]],
        "missed": [p["text"] for p in pts if not p["matched"] and not p["partial"]],
        "reference": card.translation,
    }


async def ai_check_ticket(db, user_id: str, ticket_id: int, answer: str) -> dict:
    """ИИ-оценка смысла ответа на билет (платно, месячная квота): тезисы эталона берутся из карточки билета, расход пишется в телеметрию."""
    from app.services.ai_gateway.answer_judge import judge_answer
    from app.services.open_answer import derive_key_points, normalize_key_points
    from app.services.quota import AI_CHECK_PREFIX, enforce_ai_check
    ticket, _ = await _own_ticket(db, user_id, ticket_id)
    card = (await db.execute(select(Card).where(Card.id == ticket.card_id, Card.user_id == user_id))).scalar_one_or_none() if ticket.card_id else None
    if not card:
        raise LookupError("У билета нет эталона")
    points = [p["text"] for p in (normalize_key_points(card.key_points) or derive_key_points(card.translation or ""))]
    if not points:
        raise LookupError("У билета нет тезисов эталона")
    if not (answer or "").strip():
        raise ValueError("Ответ пустой")
    left = await enforce_ai_check(db, user_id)
    calls: list[dict] = []
    try:
        result = await judge_answer(ticket.question, points, answer, calls)
    finally:
        if calls:
            from app.services.generation_worker import _record_path_calls
            await _record_path_calls(f"{AI_CHECK_PREFIX}{ticket_id}", user_id, calls)
    return {**result, "reference": card.translation, "checks_left": left}


# ---------------------------------------------------------------------------
# Несколько предметов: что сегодня важнее; напоминания с обратным отсчётом
# ---------------------------------------------------------------------------

async def exam_priorities(db, user_id: str) -> list[dict]:
    """Активные планы по всем предметам пользователя, от самого срочного: чем ближе экзамен и ниже готовность, тем выше приоритет."""
    plans = (await db.execute(
        select(ExamPlan).where(ExamPlan.user_id == user_id, ExamPlan.active == True, ExamPlan.status == "ready")  # noqa: E712
    )).scalars().all()
    out = []
    for plan in plans:
        ctx = await _plan_context(db, user_id, plan)
        if ctx["days_left"] < 0:
            continue
        ready = readiness_percent(ctx)
        left = sum(1 for t in ctx["active"] if not (ctx["cards"].get(t.card_id) is not None and ctx["cards"][t.card_id].state == 2))
        out.append({"subject": plan.subject, "title": plan.title, "days_left": ctx["days_left"], "readiness": ready,
                    "tickets_left": left, "priority": round((100 - ready) / 100 / (ctx["days_left"] + 1), 4)})
    return sorted(out, key=lambda x: -x["priority"])


async def exam_reminders(db, user_ids: list[str]) -> dict[str, list[dict]]:
    """Для утреннего уведомления: у кого есть план, сколько билетов ещё не закреплено. Лёгкие запросы (без разбора графа)."""
    if not user_ids:
        return {}
    plans = (await db.execute(
        select(ExamPlan).where(ExamPlan.user_id.in_(user_ids), ExamPlan.active == True, ExamPlan.status == "ready")  # noqa: E712
    )).scalars().all()
    out: dict[str, list[dict]] = {}
    for plan in plans:
        rows = (await db.execute(
            select(Card.state).join(ExamTicket, ExamTicket.card_id == Card.id)
            .where(ExamTicket.plan_id == plan.id, ExamTicket.status == "ok")
        )).scalars().all()
        out.setdefault(plan.user_id, []).append({
            "subject": plan.subject, "title": plan.title, "exam_date": plan.exam_date,
            "tickets": len(rows), "not_strong": sum(1 for st in rows if st != 2),
        })
    return out


def _days_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"


def exam_reminder_text(info: dict, today: date) -> str | None:
    """«До экзамена 6 дней по предмету «X»: 14 из 40 билетов не закреплено.» None — напоминать рано, поздно или нечего."""
    days = (info["exam_date"] - today).days
    if days < 0 or days > REMINDER_MAX_DAYS or not info["tickets"]:
        return None
    left = info["not_strong"]
    when = "Сегодня экзамен" if days == 0 else f"До экзамена {days} {_days_word(days)}"
    tail = f"{left} из {info['tickets']} билетов не закреплено" if left else "все билеты закреплены, держим форму"
    return f"{when} по предмету «{info['subject']}»: {tail}."


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
