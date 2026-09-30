"""
«Путь знаний»: сохранение результата конвейера в БД и правила открытия узлов.

Узел открыт, когда освоены все его пререквизиты (ярус 0 открыт сразу).
Узел освоен (и открывает следующие), когда пройден его урок и ≥80% его карточек хотя бы раз вспомнены верно.
Долговременное удержание дальше обеспечивают повторения FSRS — ждать их для открытия новых тем не нужно
(так же устроена Math Academy: тема открывается после урока, закрепление идёт повторениями).
Новые карточки узла попадают в очередь только после прохождения урока.
"""

from sqlalchemy import select, delete, func, case

from app.core.timeutil import user_day_start, get_user_timezone, local_now
from app.services.graph_service import get_all_subject_aliases

from app.database.models import (
    Card, Phrase, ReviewLog, PracticeItem, PracticeSessionLog, KnowledgeNode, KnowledgeEdge, NodeProgress,
    GenerationJob, UserSetting, utc_now,
)

MASTERY_ANSWERED_SHARE = 0.8
# Урок и его карточки неделимы. Новый урок начинается, если до дневной нормы осталось
# место хотя бы на столько карточек; начатый урок доучивается целиком, даже сверх нормы.
LESSON_MIN_ROOM = 3
DIFFICULTY_BY_TIER = {"easy": 3.5, "medium": 5.5, "hard": 7.5}


def normalize_subject(subject: str) -> str:
    return (subject or "").strip().lower() or "general"


async def generated_path_stats(db, user_id: str, subject: str) -> dict:
    """Что будет заменено повторной нарезкой: сгенерированные карточки (node_id задан) и их повторения."""
    card_ids = select(Card.id).where(Card.user_id == user_id, Card.subject == subject, Card.node_id.isnot(None))
    cards = (await db.execute(select(func.count()).select_from(card_ids.subquery()))).scalar() or 0
    reviews = (await db.execute(select(func.count(ReviewLog.id)).where(ReviewLog.card_id.in_(card_ids)))).scalar() or 0
    return {"cards": cards, "reviews": reviews}


async def wipe_subject(db, user_id: str, subject: str, only_generated: bool = False) -> None:
    """Удаляет граф, уроки, прогресс и практику предмета.

    only_generated=True (повторная нарезка): удаляются только карточки, созданные конвейером (node_id задан);
    карточки, добавленные вручную или импортом CSV, сохраняются вместе с историей повторений.
    False (пользователь удаляет предмет целиком): удаляется всё.
    """
    card_filter = [Card.user_id == user_id, Card.subject == subject]
    if only_generated:
        card_filter.append(Card.node_id.isnot(None))
    card_ids = select(Card.id).where(*card_filter)
    node_ids = select(KnowledgeNode.id).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject)
    await db.execute(delete(ReviewLog).where(ReviewLog.card_id.in_(card_ids)))
    await db.execute(delete(NodeProgress).where(NodeProgress.node_id.in_(node_ids)))
    await db.execute(delete(PracticeItem).where(PracticeItem.user_id == user_id, PracticeItem.subject == subject))
    await db.execute(delete(Card).where(*card_filter))
    if only_generated:
        # Фразы-обложки узлов удаляем, только если под ними не осталось ручных карточек
        has_cards = select(Card.phrase_id).where(Card.user_id == user_id, Card.phrase_id.isnot(None))
        await db.execute(delete(Phrase).where(
            Phrase.user_id == user_id, Phrase.subject == subject, Phrase.id.notin_(has_cards)))
    else:
        await db.execute(delete(Phrase).where(Phrase.user_id == user_id, Phrase.subject == subject))
    await db.execute(delete(KnowledgeEdge).where(KnowledgeEdge.user_id == user_id, KnowledgeEdge.subject == subject))
    await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject))


async def save_learning_path(db, user_id: str, subject: str, result: dict) -> dict:
    """Сохраняет карту, уроки и карточки. Порядок карточек: ярус → порядок узла → слой."""
    subject = normalize_subject(subject)
    path_map, packs = result["map"], result["packs"]
    await wipe_subject(db, user_id, subject, only_generated=True)

    now = utc_now()
    node_rows: dict[str, KnowledgeNode] = {}
    for n in path_map["nodes"]:
        lesson = (packs.get(n["key"]) or {}).get("lesson")
        row = KnowledgeNode(
            user_id=user_id,
            subject=subject,
            node_key=n["key"],
            name=n["name"],
            tier=n["tier"],
            parent_key=n["parent"],
            prereq_keys=n["prereqs"],
            order_idx=n["order"],
            summary=n["summary"],
            source_hint=n["src"],
            lesson=lesson,
            lesson_status="ready" if lesson else "failed",
            created_at=now,
        )
        db.add(row)
        node_rows[n["key"]] = row
    for e in path_map["edges"]:
        db.add(KnowledgeEdge(
            user_id=user_id, subject=subject,
            source_key=e["from"], target_key=e["to"], relation=e["relation"], label=e["label"],
        ))
    await db.flush()

    rank = 0
    cards_created = 0
    for n in sorted(path_map["nodes"], key=lambda x: x["order"]):
        cards = (packs.get(n["key"]) or {}).get("cards") or []
        if not cards:
            continue
        phrase = Phrase(text=n["name"], subject=subject, user_id=user_id)
        db.add(phrase)
        await db.flush()
        for c in sorted(cards, key=lambda x: x.get("layer", 1)):
            rank += 1
            db.add(Card(
                phrase_id=phrase.id,
                user_id=user_id,
                subject=subject,
                text=c["text"],
                secondary_text=c.get("secondary_text") or None,
                translation=c["translation"],
                example=c.get("example") or None,
                difficulty=DIFFICULTY_BY_TIER.get(c.get("initial_difficulty_tier"), 5.5),
                stability=1.0,
                state=0,
                content_type="text",
                organ_slug=n["key"],
                layer=c.get("layer", 1),
                topological_rank=rank,
                node_id=node_rows[n["key"]].id,
                answer_type=c.get("answer_type"),
                distractors=c.get("distractors"),
                next_review=now,
            ))
            cards_created += 1

    return {
        "subject": subject,
        "title": path_map.get("title") or subject,
        "nodes": len(node_rows),
        "edges": len(path_map["edges"]),
        "cards": cards_created,
        "lessons_failed": sum(1 for r in node_rows.values() if r.lesson_status != "ready"),
    }


# ---------------------------------------------------------------------------
# Открытие узлов
# ---------------------------------------------------------------------------

async def _path_plan(db, user_id: str, subject: str) -> dict:
    """Сколько новых карточек осталось и за сколько дней путь будет пройден при текущем дневном лимите."""
    from math import ceil
    left = (await db.execute(
        select(func.count(Card.id)).where(Card.user_id == user_id, Card.subject == subject, Card.state == 0)
    )).scalar() or 0
    setting = (await db.execute(select(UserSetting).where(UserSetting.user_id == user_id))).scalar_one_or_none()
    limit = setting.daily_limit if setting and setting.daily_limit else 10
    if setting and setting.subject_limits:
        limit = setting.subject_limits.get(subject, limit)
    limit = max(1, int(limit or 10))
    return {"new_cards_left": left, "daily_limit": limit, "days_left": ceil(left / limit) if left else 0}


async def get_path_state(db, user_id: str, subject: str) -> dict:
    """Состояние пути для UI: узлы со статусами locked | open | lesson_done | mastered и прогрессом."""
    subject = normalize_subject(subject)
    nodes = (await db.execute(
        select(KnowledgeNode).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject)
        .order_by(KnowledgeNode.order_idx)
    )).scalars().all()
    if not nodes:
        return {"subject": subject, "nodes": [], "edges": []}

    progress = {
        p.node_id: p for p in (await db.execute(
            select(NodeProgress).where(NodeProgress.user_id == user_id, NodeProgress.node_id.in_([n.id for n in nodes]))
        )).scalars().all()
    }
    # Сколько карточек узла всего и сколько из них хоть раз вспомнено верно (оценка выше «Снова»).
    # «Не вспомнил» освоением не считается: иначе путь можно пройти, ничего не зная.
    totals = dict((await db.execute(
        select(Card.node_id, func.count(Card.id))
        .where(Card.user_id == user_id, Card.subject == subject, Card.node_id.isnot(None))
        .group_by(Card.node_id)
    )).all())
    recalled = dict((await db.execute(
        select(Card.node_id, func.count(func.distinct(Card.id)))
        .join(ReviewLog, ReviewLog.card_id == Card.id)
        .where(Card.user_id == user_id, Card.subject == subject, Card.node_id.isnot(None), ReviewLog.rating > 1)
        .group_by(Card.node_id)
    )).all())
    counts = {node_id: (total, recalled.get(node_id, 0)) for node_id, total in totals.items()}

    mastered: set[str] = set()
    lesson_done: set[str] = set()
    for n in nodes:
        p = progress.get(n.id)
        if not (p and p.lesson_done):
            continue
        lesson_done.add(n.node_key)
        total, answered = counts.get(n.id, (0, 0))
        if total == 0 or answered >= total * MASTERY_ANSWERED_SHARE:
            mastered.add(n.node_key)

    items = []
    for n in nodes:
        total, answered = counts.get(n.id, (0, 0))
        if n.node_key in mastered:
            status = "mastered"
        elif n.node_key in lesson_done:
            status = "lesson_done"
        elif all(k in mastered for k in (n.prereq_keys or [])):
            status = "open"
        else:
            status = "locked"
        items.append({**n.to_dict(), "status": status, "cards_total": total, "cards_answered": answered})

    edges = (await db.execute(
        select(KnowledgeEdge).where(KnowledgeEdge.user_id == user_id, KnowledgeEdge.subject == subject)
    )).scalars().all()
    # Название курса — из последней успешной нарезки предмета (показывается в центре графа)
    title = (await db.execute(
        select(GenerationJob.theme)
        .where(GenerationJob.user_id == user_id, GenerationJob.subject == subject, GenerationJob.status == "completed")
        .order_by(GenerationJob.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    return {
        "subject": subject,
        "title": title or subject,
        "plan": await _path_plan(db, user_id, subject),
        "nodes": items,
        "edges": [{"from": e.source_key, "to": e.target_key, "relation": e.relation, "label": e.label} for e in edges],
    }


async def is_node_open(db, user_id: str, node: KnowledgeNode) -> bool:
    state = await get_path_state(db, user_id, node.subject)
    status = next((x["status"] for x in state["nodes"] if x["id"] == node.id), "locked")
    return status != "locked"


async def _apply_checkpoint_to_cards(db, user_id: str, node_id: int, score: int) -> None:
    """Слабый результат проверки в уроке делает новые карточки узла «тяжелее» (чаще повторения), сильный — легче."""
    from sqlalchemy import update
    node = (await db.execute(select(KnowledgeNode).where(KnowledgeNode.id == node_id))).scalar_one_or_none()
    checks = len(((node.lesson or {}).get("check") or [])) if node else 0
    if not checks:
        return
    ratio = max(0.0, min(1.0, score / checks))
    delta = 1.0 if ratio < 0.5 else (-0.5 if ratio >= 1.0 else 0.0)
    if delta:
        await db.execute(
            update(Card)
            .where(Card.user_id == user_id, Card.node_id == node_id, Card.state == 0)
            .values(difficulty=func.max(1.0, func.min(10.0, Card.difficulty + delta)))
        )


async def complete_lesson(db, user_id: str, node_id: int, checkpoint_score: int) -> NodeProgress:
    progress = (await db.execute(
        select(NodeProgress).where(NodeProgress.user_id == user_id, NodeProgress.node_id == node_id)
    )).scalar_one_or_none()
    if not progress:
        progress = NodeProgress(user_id=user_id, node_id=node_id, lesson_done=False, checkpoint_score=0)
        db.add(progress)
    first_completion = not progress.lesson_done
    progress.lesson_done = True
    if first_completion:
        await _apply_checkpoint_to_cards(db, user_id, node_id, checkpoint_score)
    progress.checkpoint_score = max(progress.checkpoint_score or 0, checkpoint_score)
    progress.lesson_done_at = progress.lesson_done_at or utc_now()
    return progress


def unlocked_node_ids_subquery(user_id: str):
    """Узлы с пройденным уроком: только их новые карточки выдаются в тренировку."""
    return select(NodeProgress.node_id).where(NodeProgress.user_id == user_id, NodeProgress.lesson_done == True)  # noqa: E712


# ---------------------------------------------------------------------------
# План дня и «Продолжить путь»: следующий шаг занятия одной кнопкой
# ---------------------------------------------------------------------------

# Сколько раз шаг каждого типа может встретиться за один запуск (защита от зацикливания;
# сколько уроков пройти за день, решает дневная норма, а не этот предохранитель)
RUN_STEP_LIMITS = {"review": 3, "cards": 6, "lesson": 6, "practice": 1}
RUN_REVIEW_BATCH = 15      # повторений за шаг: короткие подходы вместо стены из 80 карточек
PRACTICE_MIN_ITEMS = 4


async def _today_start(db, user_id: str):
    """Начало суток пользователя в его часовом поясе (наивный UTC)."""
    return await user_day_start(db, user_id)


async def _daily_new_limit(db, user_id: str, subject: str) -> int:
    setting = (await db.execute(select(UserSetting).where(UserSetting.user_id == user_id))).scalar_one_or_none()
    limit = setting.daily_limit if setting and setting.daily_limit else 10
    if setting and setting.subject_limits:
        limit = setting.subject_limits.get(subject, limit)
    return limit


async def _learned_today(db, user_id: str, subject: str) -> int:
    """Сколько новых карточек предмета впервые показано сегодня (повторные «Не вспомнил» не в счёт)."""
    return (await db.execute(
        select(func.count(func.distinct(ReviewLog.card_id))).join(Card, ReviewLog.card_id == Card.id).where(
            ReviewLog.user_id == user_id, ReviewLog.state == 0,
            ReviewLog.review_time >= await _today_start(db, user_id), Card.subject.in_(get_all_subject_aliases(subject)),
        )
    )).scalar() or 0


def seen_practice_cards_filter(user_id: str, subject: str) -> list:
    """Карточки, из которых можно собрать тест: урок темы пройден и карточка уже хоть раз выучена."""
    return [
        Card.user_id == user_id, Card.subject == subject, Card.state != 0,
        Card.distractors.isnot(None), Card.node_id.in_(unlocked_node_ids_subquery(user_id)),
    ]


async def get_day_plan(db, user_id: str, subject: str) -> dict:
    """
    Единый счётчик дня для всех кнопок: сколько места под новые карточки, какую тему доучить,
    какой урок следующий и выполнена ли цель дня (для победной мордочки).

    Урок + его карточки — неделимая порция. Цель дня: сегодня пройден хотя бы один урок,
    карточки начатых уроков доучены и новый урок уже не помещается в норму (или открывать нечего).
    """
    subject = normalize_subject(subject)
    has_path = (await db.execute(
        select(func.count(KnowledgeNode.id)).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject)
    )).scalar() or 0
    limit = await _daily_new_limit(db, user_id, subject)
    learned = await _learned_today(db, user_id, subject)
    room = max(0, limit - learned)
    plan = {
        "day": local_now(await get_user_timezone(db, user_id)).date().isoformat(), "is_path": bool(has_path), "limit": limit, "learned_today": learned, "room": room,
        "lessons_today": 0, "unfinished": None, "next_lesson": None, "can_start_lesson": room >= LESSON_MIN_ROOM,
        "practice_items": 0, "goal_met": False,
    }
    if not has_path:
        return plan

    # Урок пройден, а его карточки выучены не все — доучить в первую очередь (самый ранний узел пути)
    row = (await db.execute(
        select(Card.node_id, KnowledgeNode.name, func.count(Card.id))
        .join(KnowledgeNode, Card.node_id == KnowledgeNode.id)
        .where(Card.user_id == user_id, Card.subject == subject, Card.state == 0,
               Card.node_id.in_(unlocked_node_ids_subquery(user_id)))
        .group_by(Card.node_id, KnowledgeNode.name, KnowledgeNode.tier, KnowledgeNode.order_idx)
        .order_by(KnowledgeNode.tier, KnowledgeNode.order_idx)
        .limit(1)
    )).first()
    if row:
        plan["unfinished"] = {"node_id": row[0], "node_name": row[1], "count": row[2]}

    state = await get_path_state(db, user_id, subject)
    opened = sorted((x for x in state["nodes"] if x["status"] == "open"), key=lambda x: (x["tier"], x["order"]))
    if opened:
        n = opened[0]
        plan["next_lesson"] = {"node_id": n["id"], "node_name": n["name"], "tier": n["tier"],
                               "cards": n["cards_total"], "has_lesson": n.get("lesson_status") == "ready"}

    plan["lessons_today"] = (await db.execute(
        select(func.count(NodeProgress.id)).join(KnowledgeNode, NodeProgress.node_id == KnowledgeNode.id)
        .where(NodeProgress.user_id == user_id, NodeProgress.lesson_done_at >= await _today_start(db, user_id),
               KnowledgeNode.subject == subject)
    )).scalar() or 0
    plan["practice_items"] = (await db.execute(
        select(func.count(Card.id)).where(*seen_practice_cards_filter(user_id, subject))
    )).scalar() or 0
    plan["goal_met"] = (
        plan["lessons_today"] >= 1 and plan["unfinished"] is None
        and (not plan["can_start_lesson"] or plan["next_lesson"] is None)
    )
    return plan


async def get_today_summary(db, user_id: str, subject: str) -> dict:
    """Что сделано сегодня по предмету — для итога дня и праздничного кота."""
    today = await _today_start(db, user_id)
    answered, correct = (await db.execute(
        select(func.count(ReviewLog.id), func.sum(case((ReviewLog.rating > 1, 1), else_=0)))
        .join(Card, ReviewLog.card_id == Card.id)
        .where(ReviewLog.user_id == user_id, ReviewLog.review_time >= today, Card.subject == subject)
    )).one()
    lessons = (await db.execute(
        select(func.count(NodeProgress.id)).join(KnowledgeNode, NodeProgress.node_id == KnowledgeNode.id)
        .where(NodeProgress.user_id == user_id, NodeProgress.lesson_done_at >= today, KnowledgeNode.subject == subject)
    )).scalar() or 0
    practice = (await db.execute(
        select(PracticeSessionLog.score, PracticeSessionLog.total)
        .where(PracticeSessionLog.user_id == user_id, PracticeSessionLog.subject == subject,
               PracticeSessionLog.created_at >= today)
        .order_by(PracticeSessionLog.id.desc()).limit(1)
    )).first()
    return {
        "answered": answered or 0,
        "accuracy": round(100 * (correct or 0) / answered) if answered else None,
        "lessons": lessons,
        "practice": {"score": practice[0], "total": practice[1]} if practice else None,
    }


async def next_path_step(db, user_id: str, subject: str, done: list[str], scope: str = "day", extra: bool = False) -> dict:
    """
    Следующий шаг занятия (составитель сессии в духе Math Academy / Duolingo).

    scope="day" — «Продолжить путь»:
    1. разминка — повторения по FSRS короткими подходами;
    2. карточки уже пройденного урока — все, урок неделим (даже сверх нормы);
    3. урок следующего открытого узла, пока до нормы остаётся место на LESSON_MIN_ROOM карточек;
    4. практика-тест по выученному (раз в день);
    5. итог дня.
    scope="topic" — кнопка «Новая тема»: одна порция «урок → все его карточки» (или доучить начатую).
    extra=True — тема сверх нормы по явному выбору пользователя.
    done — типы шагов, уже выданных в этом запуске.
    """
    subject = normalize_subject(subject)
    used = {t: done.count(t) for t in RUN_STEP_LIMITS}
    now = utc_now()
    base = [Card.user_id == user_id, Card.subject == subject]
    topic = scope == "topic"

    # 1. Повторения, у которых подошёл срок (Review и заучивание). Только что выученные
    # карточки ждут своего шага заучивания, а не возвращаются сразу же.
    due = (await db.execute(
        select(func.count(Card.id)).where(*base, Card.state.in_([1, 2, 3]), Card.next_review <= now)
    )).scalar() or 0
    if not topic and due and used["review"] < RUN_STEP_LIMITS["review"]:
        return {"type": "review", "count": min(due, RUN_REVIEW_BATCH), "remaining": due}

    plan = await get_day_plan(db, user_id, subject)

    # 2. Доучить карточки пройденного урока — все сразу, чтобы тема не осталась наполовину
    if plan["unfinished"] and used["cards"] < RUN_STEP_LIMITS["cards"]:
        u = plan["unfinished"]
        return {"type": "cards", "node_id": u["node_id"], "node_name": u["node_name"], "count": u["count"]}

    # «Новая тема» — ровно одна порция за нажатие
    topic_finished = topic and bool(used["lesson"] or used["cards"])

    # 3. Урок следующего открытого узла
    nl = plan["next_lesson"]
    if nl and not topic_finished and (plan["can_start_lesson"] or extra) and used["lesson"] < RUN_STEP_LIMITS["lesson"]:
        if not nl["has_lesson"]:
            # Урок не сгенерировался — не блокируем путь: сразу открываем карточки узла
            await complete_lesson(db, user_id, nl["node_id"], 0)
            await db.commit()
            return await next_path_step(db, user_id, subject, done, scope, extra)
        return {"type": "lesson", "node_id": nl["node_id"], "node_name": nl["node_name"],
                "tier": nl["tier"], "cards": nl["cards"]}

    # 4. Практика — раз в день, если уже есть из чего собрать тест
    if not topic and used["practice"] < RUN_STEP_LIMITS["practice"]:
        practiced_today = (await db.execute(
            select(func.count(PracticeSessionLog.id)).where(
                PracticeSessionLog.user_id == user_id, PracticeSessionLog.subject == subject,
                PracticeSessionLog.created_at >= await _today_start(db, user_id),
            )
        )).scalar() or 0
        if not practiced_today and plan["practice_items"] >= PRACTICE_MIN_ITEMS:
            return {"type": "practice", "count": min(8, plan["practice_items"])}

    # 5. Итог: что сделано и что будет дальше
    if topic_finished:
        reason = "topic_done"
    elif nl and not plan["can_start_lesson"]:
        reason = "limit"
    elif due and not topic:
        reason = "reviews_left"
    else:
        reason = "waiting"
    return {
        "type": "done",
        "scope": scope,
        "reason": reason,
        "next_up": nl["node_name"] if nl else None,
        "plan": plan,
        "today": await get_today_summary(db, user_id, subject),
    }
