# app/services/practice_service.py
"""
Практика «Пути знаний»: задания с вариантами ответа по пройденным урокам.
1. Карточки узла с дистракторами того же типа ответа (их пишет ИИ при нарезке).
2. Вопросы на связи графа: «A <связка> …?».
"""

import re
import uuid
import random
from typing import List, Dict, Any, Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import timedelta
from app.database.models import PracticeItem, Card, utc_now
from app.database.session import AsyncSessionLocal


PRACTICE_ITEM_TTL_HOURS = 6
PRACTICE_TYPE_BY_LAYER = {0: "recall", 1: "recall", 2: "situational"}


async def generate_practice_session(
    user_id: str,
    subject: str,
    count: int = 10,
    db: Optional[AsyncSession] = None
) -> List[Dict[str, Any]]:
    """Практика «Пути знаний» — только по узлам с пройденным уроком.

    1. Карточки узла с готовыми дистракторами того же типа ответа (генерирует ИИ при нарезке).
    2. Вопросы на связи графа: «A <связка> …?» — варианты из узлов того же яруса.
    Регулярных выражений и случайных ответов из общего пула больше нет.
    """
    from app.services.knowledge_path import normalize_subject, unlocked_node_ids_subquery
    from app.database.models import KnowledgeNode, KnowledgeEdge

    should_close = False
    if db is None:
        db = AsyncSessionLocal()
        should_close = True

    try:
        subject = normalize_subject(subject)
        nodes = (await db.execute(
            select(KnowledgeNode).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject)
        )).scalars().all()
        studied_ids = set((await db.execute(unlocked_node_ids_subquery(user_id))).scalars().all())
        studied = {n.node_key: n for n in nodes if n.id in studied_ids}

        records: list[PracticeItem] = []

        cards = (await db.execute(
            select(Card).where(Card.user_id == user_id, Card.subject == subject, Card.node_id.in_(studied_ids))
        )).scalars().all() if studied_ids else []
        # Часть зрелых карточек предъявляем письменно вместо выбора из вариантов (политика — в open_policy)
        from app.services.card_db_sync import get_user_experiment_status
        from app.services.open_policy import pick_open_ids
        from app.database.models import UserSetting
        is_part, phase = await get_user_experiment_status(user_id, db)
        open_mode = None if (is_part and phase == 1) else (await db.execute(
            select(UserSetting.open_mode).where(UserSetting.user_id == user_id))).scalar_one_or_none()
        if is_part and phase == 1:
            open_mode = "off"
        from app.core.timeutil import user_day_start
        day_key = (await user_day_start(db, user_id)).strftime("%Y-%m-%d")
        open_ids = pick_open_ids(cards, open_mode, day_key=day_key, salt=f"{user_id}:practice")
        for c in cards:
            if c.id in open_ids:
                records.append(PracticeItem(
                    item_id=str(uuid.uuid4()),
                    user_id=user_id,
                    subject=subject,
                    node_id=c.node_id,
                    item_type="open_recall",
                    prompt=c.text,
                    options=[],
                    correct_answer=c.translation,
                    explanation=c.example or c.secondary_text or "",
                    gold_standard=c.translation,
                ))
                continue
            wrong = [d for d in (c.distractors or []) if d]
            if len(wrong) < 2:
                continue
            options = [c.translation] + wrong[:3]
            random.shuffle(options)
            records.append(PracticeItem(
                item_id=str(uuid.uuid4()),
                user_id=user_id,
                subject=subject,
                node_id=c.node_id,
                item_type=PRACTICE_TYPE_BY_LAYER.get(c.layer, "recall"),
                prompt=c.text,
                options=options,
                correct_answer=c.translation,
                explanation=c.example or c.secondary_text or f"Правильный ответ: {c.translation}",
                gold_standard=c.translation,
            ))

        edges = (await db.execute(
            select(KnowledgeEdge).where(KnowledgeEdge.user_id == user_id, KnowledgeEdge.subject == subject)
        )).scalars().all() if studied else []
        for e in edges:
            src, dst = studied.get(e.source_key), studied.get(e.target_key)
            if not src or not dst or not e.label:
                continue
            # Дистракторы — изученные узлы того же яруса, что и верный ответ
            peers = [n.name for k, n in studied.items() if n.tier == dst.tier and k not in (src.node_key, dst.node_key)]
            if len(peers) < 2:
                continue
            options = [dst.name] + random.sample(peers, min(3, len(peers)))
            random.shuffle(options)
            records.append(PracticeItem(
                item_id=str(uuid.uuid4()),
                user_id=user_id,
                subject=subject,
                node_id=src.id,
                item_type="relation",
                prompt=f"«{src.name}» {e.label} …?",
                options=options,
                correct_answer=dst.name,
                explanation=f"{src.name} {e.label} {dst.name}.",
                gold_standard=dst.name,
            ))

        records = _pick_interleaved(records, count, await _fresh_node_ids(db, user_id))

        # Прошлые задания не стираем сразу: вторая вкладка или перезапрос не должны ломать открытую сессию
        await db.execute(delete(PracticeItem).where(
            PracticeItem.user_id == user_id, PracticeItem.subject == subject,
            PracticeItem.created_at < utc_now() - timedelta(hours=PRACTICE_ITEM_TTL_HOURS),
        ))
        for pi in records:
            db.add(pi)
        await db.commit()
        return [pi.to_dict(include_answer=False) for pi in records]

    finally:
        if should_close:
            await db.close()


async def _fresh_node_ids(db: AsyncSession, user_id: str) -> set[int]:
    """Узлы, урок которых пройден сегодня: по ним практика нужнее всего."""
    from app.database.models import NodeProgress
    from app.core.timeutil import user_day_start
    today = await user_day_start(db, user_id)
    return set((await db.execute(
        select(NodeProgress.node_id).where(NodeProgress.user_id == user_id, NodeProgress.lesson_done_at >= today)
    )).scalars().all())


def _pick_interleaved(records: list, count: int, fresh_ids: set[int]) -> list:
    """
    Половина заданий — по сегодняшним узлам, остальное — по ранее изученным (вперемешку).
    Подряд не идут два задания одного узла: чередование учит выбирать правило, а не узнавать тему.
    """
    random.shuffle(records)
    fresh = [r for r in records if r.node_id in fresh_ids]
    old = [r for r in records if r.node_id not in fresh_ids]
    n_fresh = min(len(fresh), max(count // 2, count - len(old)))
    picked = fresh[:n_fresh] + old[:count - n_fresh]

    result, pool = [], picked[:]
    while pool:
        i = next((k for k, r in enumerate(pool) if not result or r.node_id != result[-1].node_id), 0)
        result.append(pool.pop(i))
    return result


def normalize_answer_text(text: str) -> str:
    if not text:
        return ""
    t = text.replace('\xa0', ' ').strip()
    return re.sub(r'[.!?,;:]+$', '', t).strip().lower()


async def verify_practice_answer(
    user_id: str,
    item_id: str,
    selected_answer: str,
    db: Optional[AsyncSession] = None
) -> Dict[str, Any]:
    """Проверяет ответ пользователя на его собственное задание."""
    should_close = False
    if db is None:
        db = AsyncSessionLocal()
        should_close = True

    try:
        item = (await db.execute(
            select(PracticeItem).where(PracticeItem.item_id == item_id, PracticeItem.user_id == user_id)
        )).scalars().first()

        if not item:
            return {
                "correct": False,
                "selected": selected_answer,
                "correct_answer": "Не удалось найти задание в реестре.",
                "explanation": "Срок сессии истек или задание было обновлено.",
                "gold_standard": "Сессия обновлена."
            }

        if item.item_type == "open_recall":
            from app.services.open_answer import grade_answer
            graded = grade_answer(selected_answer, None, item.correct_answer)
            return {
                "correct": graded["suggested_rating"] >= 3,
                "selected": selected_answer,
                "correct_answer": item.correct_answer,
                "explanation": item.explanation or "",
                "gold_standard": item.gold_standard or item.correct_answer,
                "open": True,
                "score": graded["score"],
                "points": graded["points"],
            }
        is_correct = normalize_answer_text(selected_answer) == normalize_answer_text(item.correct_answer)
        return {
            "correct": is_correct,
            "selected": selected_answer,
            "correct_answer": item.correct_answer,
            "explanation": item.explanation or "Обоснование зафиксировано в нормативном акте.",
            "gold_standard": item.gold_standard or item.correct_answer
        }

    finally:
        if should_close:
            await db.close()
