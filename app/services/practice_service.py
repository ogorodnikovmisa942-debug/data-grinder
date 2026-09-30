# app/services/practice_service.py
"""
Практика «Пути знаний» — тест только по выученному (урок темы пройден, карточка хоть раз выучена).
1. Вопросы с вариантами из карточек: вопрос карточки, её ответ и 3 неверных варианта (их пишет ИИ при нарезке).
   Чаще — правила, условия и различения, реже — чистые термины (их и так тренируют карточки).
2. Вопросы на связи графа: «A <связка> …?».
3. Вопросы на понимание из уроков, пройденных в прошлые дни.
4. Открытые вопросы (~20%): ответ вводится с клавиатуры — только там, где он короткий и однозначный.
Ошибка в тесте приближает повторение этой карточки.
"""

import re
import uuid
import random
from datetime import timedelta
from difflib import SequenceMatcher
from typing import List, Dict, Any, Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PracticeItem, Card
from app.database.session import AsyncSessionLocal


PRACTICE_TYPE_BY_LAYER = {0: "recall", 1: "recall", 2: "situational"}
# Задания, сделанные из карточки: по ним ошибка приближает повторение карточки
CARD_ITEM_TYPES = {"recall", "situational", "open"}
# Открытый вопрос — только для коротких однозначных ответов
OPEN_ANSWER_TYPES = {"term", "organ", "person", "number", "date", "duration"}
OPEN_MAX_WORDS = 4
OPEN_SHARE = 0.2


async def generate_practice_session(
    user_id: str,
    subject: str,
    count: int = 10,
    db: Optional[AsyncSession] = None
) -> List[Dict[str, Any]]:
    """Собирает тест по выученному: карточки, связи тем, вопросы из уроков; часть вопросов — открытые."""
    from app.services.knowledge_path import normalize_subject, seen_practice_cards_filter, day_start_utc
    from app.database.models import KnowledgeNode, KnowledgeEdge, NodeProgress

    should_close = False
    if db is None:
        db = AsyncSessionLocal()
        should_close = True

    try:
        subject = normalize_subject(subject)
        today = day_start_utc()
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
        open_ok: set[str] = set()

        for c in cards:
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
            # Правила и различения важнее для теста, чем термины
            weights[pi.item_id] = 1.0 if (c.layer or 0) == 0 else 2.0
            if c.answer_type in OPEN_ANSWER_TYPES and len(_norm_open(c.translation).split()) <= OPEN_MAX_WORDS:
                open_ok.add(pi.item_id)

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

        records = _pick_interleaved(records, count, await _fresh_node_ids(db, user_id), weights)
        _make_open(records, open_ok, round(len(records) * OPEN_SHARE))

        await db.execute(delete(PracticeItem).where(PracticeItem.user_id == user_id, PracticeItem.subject == subject))
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
    from app.services.knowledge_path import day_start_utc
    return set((await db.execute(
        select(NodeProgress.node_id).where(NodeProgress.user_id == user_id, NodeProgress.lesson_done_at >= day_start_utc())
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


def _make_open(records: list, open_ok: set[str], n_open: int) -> None:
    """Часть подходящих заданий превращается в открытые: вариантов нет, ответ вводится вручную."""
    candidates = [r for r in records if r.item_id in open_ok]
    for r in random.sample(candidates, min(n_open, len(candidates))):
        r.item_type = "open"
        r.options = []


def normalize_answer_text(text: str) -> str:
    if not text:
        return ""
    t = text.replace('\xa0', ' ').strip()
    return re.sub(r'[.!?,;:]+$', '', t).strip().lower()


def _norm_open(text: str) -> str:
    t = (text or "").replace('\xa0', ' ').lower().replace('ё', 'е')
    t = re.sub(r'[«»"\'“”()\[\].,!?;:—–\-]', ' ', t)
    return ' '.join(t.split())


def _stem(word: str) -> str:
    """Грубая основа слова: без последних двух букв — прощает падежные окончания."""
    return word[:max(4, len(word) - 2)] if len(word) > 4 else word


def open_answer_matches(given: str, correct: str) -> bool:
    """Открытый ответ: прощает регистр, ё/е, кавычки, опечатку и окончания, но не лишние слова."""
    g, c = _norm_open(given), _norm_open(correct)
    if not g or not c:
        return False
    if g == c or SequenceMatcher(None, g, c).ratio() >= 0.85:
        return True
    g_stems = {_stem(w) for w in g.split()}
    c_words = [w for w in c.split() if len(w) > 2 or w.isdigit()]
    return bool(c_words) and all(_stem(w) in g_stems for w in c_words) and len(g.split()) <= len(c.split()) + 1


async def _pull_card_review(db: AsyncSession, user_id: str, item: PracticeItem) -> None:
    """Ошибка в тесте: карточка, из которой сделано задание, придёт на повторение не позже завтрашнего дня."""
    from app.services.knowledge_path import day_start_utc
    card = (await db.execute(
        select(Card).where(Card.user_id == user_id, Card.node_id == item.node_id,
                           Card.text == item.prompt, Card.translation == item.correct_answer)
    )).scalars().first()
    tomorrow = day_start_utc() + timedelta(days=1)
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

        if item.item_type == "open":
            is_correct = open_answer_matches(selected_answer, item.correct_answer)
        else:
            is_correct = normalize_answer_text(selected_answer) == normalize_answer_text(item.correct_answer)
        if not is_correct and item.item_type in CARD_ITEM_TYPES:
            await _pull_card_review(db, user_id, item)
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
