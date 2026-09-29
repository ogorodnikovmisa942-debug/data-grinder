"""
«Путь знаний»: сохранение результата конвейера в БД и правила открытия узлов.

Узел открыт, когда освоены все его пререквизиты (ярус 0 открыт сразу).
Узел освоен, когда пройден его урок и на ≥80% его карточек ответили хотя бы раз.
Новые карточки узла попадают в очередь только после прохождения урока.
"""
from sqlalchemy import select, delete, func, case

from app.database.models import (
    Card, Phrase, ReviewLog, PracticeItem, KnowledgeNode, KnowledgeEdge, NodeProgress, GenerationJob, utc_now,
)

MASTERY_ANSWERED_SHARE = 0.8
DIFFICULTY_BY_TIER = {"easy": 3.5, "medium": 5.5, "hard": 7.5}


def normalize_subject(subject: str) -> str:
    return (subject or "").strip().lower() or "general"


async def wipe_subject(db, user_id: str, subject: str) -> None:
    """Повторная загрузка предмета заменяет его целиком: граф, уроки, карточки, прогресс, практику."""
    card_ids = select(Card.id).where(Card.user_id == user_id, Card.subject == subject)
    node_ids = select(KnowledgeNode.id).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject)
    await db.execute(delete(ReviewLog).where(ReviewLog.card_id.in_(card_ids)))
    await db.execute(delete(NodeProgress).where(NodeProgress.node_id.in_(node_ids)))
    await db.execute(delete(PracticeItem).where(PracticeItem.user_id == user_id, PracticeItem.subject == subject))
    await db.execute(delete(Card).where(Card.user_id == user_id, Card.subject == subject))
    await db.execute(delete(Phrase).where(Phrase.user_id == user_id, Phrase.subject == subject))
    await db.execute(delete(KnowledgeEdge).where(KnowledgeEdge.user_id == user_id, KnowledgeEdge.subject == subject))
    await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject))


async def save_learning_path(db, user_id: str, subject: str, result: dict) -> dict:
    """Сохраняет карту, уроки и карточки. Порядок карточек: ярус → порядок узла → слой."""
    subject = normalize_subject(subject)
    path_map, packs = result["map"], result["packs"]
    await wipe_subject(db, user_id, subject)

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
    # Сколько карточек узла всего и сколько уже хоть раз отвечено (state != New)
    counts = {
        node_id: (total, answered or 0)
        for node_id, total, answered in (await db.execute(
            select(Card.node_id, func.count(Card.id), func.sum(case((Card.state != 0, 1), else_=0)))
            .where(Card.user_id == user_id, Card.subject == subject, Card.node_id.isnot(None))
            .group_by(Card.node_id)
        )).all()
    }

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
        "nodes": items,
        "edges": [{"from": e.source_key, "to": e.target_key, "relation": e.relation, "label": e.label} for e in edges],
    }


async def is_node_open(db, user_id: str, node: KnowledgeNode) -> bool:
    state = await get_path_state(db, user_id, node.subject)
    status = next((x["status"] for x in state["nodes"] if x["id"] == node.id), "locked")
    return status != "locked"


async def complete_lesson(db, user_id: str, node_id: int, checkpoint_score: int) -> NodeProgress:
    progress = (await db.execute(
        select(NodeProgress).where(NodeProgress.user_id == user_id, NodeProgress.node_id == node_id)
    )).scalar_one_or_none()
    if not progress:
        progress = NodeProgress(user_id=user_id, node_id=node_id, lesson_done=False, checkpoint_score=0)
        db.add(progress)
    progress.lesson_done = True
    progress.checkpoint_score = max(progress.checkpoint_score or 0, checkpoint_score)
    progress.lesson_done_at = progress.lesson_done_at or utc_now()
    return progress


def unlocked_node_ids_subquery(user_id: str):
    """Узлы с пройденным уроком: только их новые карточки выдаются в тренировку."""
    return select(NodeProgress.node_id).where(NodeProgress.user_id == user_id, NodeProgress.lesson_done == True)  # noqa: E712
