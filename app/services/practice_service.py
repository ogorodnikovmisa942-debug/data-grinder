# app/services/practice_service.py
"""
Практика «Пути знаний» — тест только по выученному (урок темы пройден, карточка хоть раз выучена).
1. Вопросы с вариантами из карточек: вопрос карточки, её ответ и 3 неверных варианта (их пишет ИИ при нарезке).
   Чаще — правила, условия и различения, реже — чистые термины (их и так тренируют карточки).
2. Письменные вопросы (open_recall): часть зрелых карточек без вариантов — политика в open_policy,
   проверка по ключевым тезисам в open_answer.
3. Вопросы на связи графа: «A <связка> …?».
4. Вопросы на понимание из уроков, пройденных в прошлые дни.
Ошибка в задании из карточки приближает повторение этой карточки.
"""

import re
import uuid
import random
from datetime import timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PracticeItem, Card, utc_now
from app.database.session import AsyncSessionLocal


PRACTICE_ITEM_TTL_HOURS = 6
PRACTICE_TYPE_BY_LAYER = {0: "recall", 1: "recall", 2: "situational"}
# Задания, сделанные из карточки: по ним ошибка приближает повторение карточки
CARD_ITEM_TYPES = {"recall", "situational", "open_recall"}


async def generate_practice_session(
    user_id: str,
    subject: str,
    count: int = 10,
    db: Optional[AsyncSession] = None,
    node_ids: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Собирает тест по выученному: карточки (с вариантами или письменно), связи тем, вопросы из уроков."""
    from app.services.knowledge_path import normalize_subject, seen_practice_cards_filter
    from app.services.card_db_sync import get_user_experiment_status
    from app.services.open_policy import pick_open_ids
    from app.core.timeutil import user_day_start
    from app.database.models import KnowledgeNode, KnowledgeEdge, NodeProgress, UserSetting

    should_close = False
    if db is None:
        db = AsyncSessionLocal()
        should_close = True

    try:
        subject = normalize_subject(subject)
        today = await user_day_start(db, user_id)
        nodes = (await db.execute(
            select(KnowledgeNode).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject)
        )).scalars().all()
        progress = {
            p.node_id: p for p in (await db.execute(
                select(NodeProgress).where(NodeProgress.user_id == user_id, NodeProgress.lesson_done == True)  # noqa: E712
            )).scalars().all()
        }
        cards = (await db.execute(select(Card).where(*seen_practice_cards_filter(user_id, subject)))).scalars().all()
        # Тема «изучена» для теста, если по ней уже выучена хотя бы одна карточка (или карточек у неё нет)
        with_cards = {n_id for (n_id,) in (await db.execute(
            select(Card.node_id).where(Card.user_id == user_id, Card.subject == subject, Card.node_id.isnot(None)).distinct()
        )).all()}
        learned_nodes = {c.node_id for c in cards}
        studied = {
            n.node_key: n for n in nodes
            if n.id in progress and (n.id in learned_nodes or n.id not in with_cards)
        }

        records: list[PracticeItem] = []
        weights: dict[str, float] = {}

        # Часть зрелых карточек предъявляем письменно вместо выбора из вариантов (политика — в open_policy)
        is_part, phase = await get_user_experiment_status(user_id, db)
        if is_part and phase == 1:
            open_mode = "off"
        else:
            open_mode = (await db.execute(
                select(UserSetting.open_mode).where(UserSetting.user_id == user_id))).scalar_one_or_none()
        open_ids = pick_open_ids(cards, open_mode, day_key=today.strftime("%Y-%m-%d"), salt=f"{user_id}:practice")

        for c in cards:
            # Правила и различения важнее для теста, чем термины
            weight = 1.0 if (c.layer or 0) == 0 else 2.0
            if c.id in open_ids:
                pi = PracticeItem(
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
                )
                records.append(pi)
                weights[pi.item_id] = weight
                continue
            wrong = [d for d in (c.distractors or []) if d]
            if len(wrong) < 2:
                continue
            options = [c.translation] + wrong[:3]
            random.shuffle(options)
            pi = PracticeItem(
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
            )
            records.append(pi)
            weights[pi.item_id] = weight

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
            pi = PracticeItem(
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
            )
            records.append(pi)
            weights[pi.item_id] = 1.5

        # Вопросы на понимание из уроков прошлых дней (сегодняшние только что были в уроке)
        for n in studied.values():
            p = progress.get(n.id)
            if not (p and p.lesson_done_at and p.lesson_done_at < today):
                continue
            for chk in ((n.lesson or {}).get("check") or []):
                opts = [o for o in (chk.get("options") or []) if o]
                ans = chk.get("answer")
                if not chk.get("q") or len(opts) < 2 or not isinstance(ans, int) or not 0 <= ans < len(opts):
                    continue
                correct = opts[ans]
                random.shuffle(opts)
                pi = PracticeItem(
                    item_id=str(uuid.uuid4()),
                    user_id=user_id,
                    subject=subject,
                    node_id=n.id,
                    item_type="check",
                    prompt=chk["q"],
                    options=opts,
                    correct_answer=correct,
                    explanation=chk.get("why") or f"Правильный ответ: {correct}",
                    gold_standard=correct,
                )
                records.append(pi)
                weights[pi.item_id] = 1.5

        if node_ids:
            # Режим экзамена: только темы билетов (если по ним заданий мало — добираем из остальных)
            focused = [r for r in records if r.node_id in node_ids]
            if len(focused) >= min(count, 4):
                records = focused
        records = _pick_interleaved(records, count, await _fresh_node_ids(db, user_id), weights)

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


def _pick_interleaved(records: list, count: int, fresh_ids: set[int], weights: Optional[dict] = None) -> list:
    """
    Половина заданий — по сегодняшним узлам, остальное — по ранее изученным (вперемешку).
    Внутри каждой части задания с большим весом попадают в тест чаще (взвешенная случайная выборка).
    Подряд не идут два задания одного узла: чередование учит выбирать правило, а не узнавать тему.
    """
    weights = weights or {}
    records = sorted(records, key=lambda r: random.random() ** (1.0 / weights.get(r.item_id, 1.0)), reverse=True)
    fresh = [r for r in records if r.node_id in fresh_ids]
    old = [r for r in records if r.node_id not in fresh_ids]
    n_fresh = min(len(fresh), max(count // 2, count - len(old)))
    picked = fresh[:n_fresh] + old[:count - n_fresh]
    random.shuffle(picked)

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


async def _pull_card_review(db: AsyncSession, user_id: str, item: PracticeItem) -> None:
    """Ошибка в тесте: карточка, из которой сделано задание, придёт на повторение не позже завтрашнего дня."""
    from app.core.timeutil import user_day_start
    card = (await db.execute(
        select(Card).where(Card.user_id == user_id, Card.node_id == item.node_id,
                           Card.text == item.prompt, Card.translation == item.correct_answer)
    )).scalars().first()
    tomorrow = await user_day_start(db, user_id) + timedelta(days=1)
    # Заучиваемые сегодня карточки (state 1/3) и так скоро вернутся; двигаем только долгие интервалы
    if card and card.state == 2 and card.next_review and card.next_review > tomorrow:
        card.next_review = tomorrow
        await db.commit()


async def verify_practice_answer(
    user_id: str,
    item_id: str,
    selected_answer: str,
    db: Optional[AsyncSession] = None
) -> Dict[str, Any]:
    """Проверяет ответ пользователя на его собственное задание («» — «Не знаю»)."""
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
            result = {
                "correct": bool(selected_answer.strip()) and graded["suggested_rating"] >= 3,
                "selected": selected_answer,
                "correct_answer": item.correct_answer,
                "explanation": item.explanation or "",
                "gold_standard": item.gold_standard or item.correct_answer,
                "open": True,
                "score": graded["score"],
                "points": graded["points"],
            }
        else:
            result = {
                "correct": normalize_answer_text(selected_answer) == normalize_answer_text(item.correct_answer),
                "selected": selected_answer,
                "correct_answer": item.correct_answer,
                "explanation": item.explanation or "Обоснование зафиксировано в нормативном акте.",
                "gold_standard": item.gold_standard or item.correct_answer
            }
        if not result["correct"] and item.item_type in CARD_ITEM_TYPES:
            await _pull_card_review(db, user_id, item)
        return result

    finally:
        if should_close:
            await db.close()
