"""
«Путь знаний»: сохранение результата конвейера в БД и правила открытия узлов.

Узел открыт, когда освоены все его пререквизиты (ярус 0 открыт сразу).
Узел освоен (и открывает следующие), когда пройден его урок и ≥80% его карточек хотя бы раз вспомнены верно.
Долговременное удержание дальше обеспечивают повторения FSRS — ждать их для открытия новых тем не нужно
(так же устроена Math Academy: тема открывается после урока, закрепление идёт повторениями).
Новые карточки узла попадают в очередь только после прохождения урока.
"""

import hashlib
import re

from sqlalchemy import select, delete, func, case, or_, update

from app.core.timeutil import user_day_start, get_user_timezone, local_now
from app.services.graph_service import get_all_subject_aliases

from app.database.models import (
    Card, Phrase, ReviewLog, PracticeItem, PracticeSessionLog, KnowledgeNode, KnowledgeEdge, NodeProgress,
    GenerationJob, UserSetting, Source, ExamTicket, utc_now,
)
from app.services.ai_gateway.coverage import stems
from app.services.ai_gateway.source_profile import SUPPLEMENT_MIN_CARDS
from app.services.exam_prep import ticket_card_filter

# Вводный урок курса: скрытый узел без карточек, связей и яруса в графе. Идёт первым шагом пути.
INTRO_KEY = "__intro__"
INTRO_NAME = "Знакомство с курсом"
_intro_attempted: set[tuple[str, str]] = set()  # попытки догенерации за время жизни процесса
MASTERY_ANSWERED_SHARE = 0.8
# Урок и его карточки неделимы. Новый урок начинается, если до дневной нормы осталось
# место хотя бы на столько карточек; начатый урок доучивается целиком, даже сверх нормы.
LESSON_MIN_ROOM = 3
DIFFICULTY_BY_TIER = {"easy": 3.5, "medium": 5.5, "hard": 7.5}
FACT_DUP_JACCARD = 0.7          # факт нового материала, на 70% совпадающий по словам с имеющимся, в конспект не добавляется


def fact_texts(facts) -> list[str]:
    """Тексты фактов конспекта темы (хранятся как {"t": текст, "s": id материала})."""
    return [(f.get("t") if isinstance(f, dict) else str(f)) for f in (facts or []) if f]


def merge_facts(existing, new: list[str], source_id: int | None) -> list[dict]:
    """Дописывает факты нового материала к конспекту темы, пропуская те, что в нём уже есть."""
    out = [f if isinstance(f, dict) else {"t": str(f), "s": None} for f in (existing or []) if f]
    seen = [set(stems(f["t"])) for f in out]
    for text in new or []:
        words = set(stems(text))
        if words and any(len(words & s) / len(words | s) >= FACT_DUP_JACCARD for s in seen):
            continue
        out.append({"t": text, "s": source_id})
        seen.append(words)
    return out


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
    await db.execute(delete(Source).where(Source.user_id == user_id, Source.subject == subject))


# ---------------------------------------------------------------------------
# Материалы курса: добавление без потери повторений
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"=== [^=\n]+ ===|--- [^\n]+ ---")


def text_fingerprint(text: str) -> str:
    """Отпечаток материала: один и тот же файл даёт один и тот же хэш, как бы он ни назывался. Сам текст не хранится."""
    clean = re.sub(r"\s+", " ", _HEADER_RE.sub(" ", text or "")).strip().lower()
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


async def find_duplicate_source(db, user_id: str, subject: str, text_hash: str) -> Source | None:
    if not text_hash:
        return None
    return (await db.execute(
        select(Source).where(Source.user_id == user_id, Source.subject == normalize_subject(subject), Source.text_hash == text_hash)
        .order_by(Source.id.desc()).limit(1)
    )).scalar_one_or_none()


async def source_reviews(db, source_id: int) -> int:
    card_ids = select(Card.id).where(Card.source_id == source_id)
    return (await db.execute(select(func.count(ReviewLog.id)).where(ReviewLog.card_id.in_(card_ids)))).scalar() or 0


async def list_sources(db, user_id: str, subject: str) -> list[dict]:
    """Материалы предмета: что в них есть и сколько ответов по ним уже дано (для окна «Материалы» и подтверждения удаления)."""
    subject = normalize_subject(subject)
    rows = (await db.execute(
        select(Source).where(Source.user_id == user_id, Source.subject == subject).order_by(Source.id)
    )).scalars().all()
    out = []
    for src in rows:
        nodes = (await db.execute(select(func.count(KnowledgeNode.id)).where(KnowledgeNode.source_id == src.id))).scalar() or 0
        cards = (await db.execute(select(func.count(Card.id)).where(Card.source_id == src.id))).scalar() or 0
        out.append({
            "id": src.id, "name": src.name, "title": src.title, "kind": src.kind, "role": src.role, "chars": src.chars,
            "nodes": nodes, "cards": cards, "reviews": await source_reviews(db, src.id),
            "cost_usd": round(src.cost_usd or 0.0, 4), "created_at": src.created_at.isoformat() if src.created_at else None,
        })
    return out


async def delete_source(db, user_id: str, subject: str, source_id: int) -> dict | None:
    """Удаляет ОДИН материал: его узлы, карточки и ответы по ним. Остальные материалы и их повторения не затрагиваются.
    Карточки, добавленные вручную или импортом CSV, остаются. None — материала нет у этого пользователя."""
    subject = normalize_subject(subject)
    src = (await db.execute(
        select(Source).where(Source.id == source_id, Source.user_id == user_id, Source.subject == subject)
    )).scalar_one_or_none()
    if not src:
        return None
    node_rows = (await db.execute(select(KnowledgeNode.id, KnowledgeNode.node_key).where(KnowledgeNode.source_id == source_id))).all()
    # Тема этого материала, к которой добавлены карточки другого материала, остаётся (переходит к тому материалу): иначе пропали бы чужие карточки
    shared = dict((await db.execute(
        select(Card.node_id, func.min(Card.source_id)).where(
            Card.node_id.in_([r[0] for r in node_rows]), Card.source_id.isnot(None), Card.source_id != source_id)
        .group_by(Card.node_id)
    )).all())
    for nid, other in shared.items():
        await db.execute(update(KnowledgeNode).where(KnowledgeNode.id == nid).values(source_id=other))
    node_ids = [r[0] for r in node_rows if r[0] not in shared]
    node_keys = [r[1] for r in node_rows if r[0] not in shared]
    card_ids = select(Card.id).where(Card.user_id == user_id, or_(Card.source_id == source_id, Card.node_id.in_(node_ids)))
    cards = (await db.execute(select(func.count()).select_from(card_ids.subquery()))).scalar() or 0
    reviews = (await db.execute(select(func.count(ReviewLog.id)).where(ReviewLog.card_id.in_(card_ids)))).scalar() or 0

    await db.execute(delete(ReviewLog).where(ReviewLog.card_id.in_(card_ids)))
    await db.execute(delete(NodeProgress).where(NodeProgress.node_id.in_(node_ids)))
    await db.execute(delete(PracticeItem).where(PracticeItem.user_id == user_id, PracticeItem.node_id.in_(node_ids)))
    await db.execute(delete(Card).where(Card.user_id == user_id, or_(Card.source_id == source_id, Card.node_id.in_(node_ids))))
    has_cards = select(Card.phrase_id).where(Card.user_id == user_id, Card.phrase_id.isnot(None))
    await db.execute(delete(Phrase).where(Phrase.user_id == user_id, Phrase.subject == subject, Phrase.id.notin_(has_cards)))
    if node_keys:
        await db.execute(delete(KnowledgeEdge).where(
            KnowledgeEdge.user_id == user_id, KnowledgeEdge.subject == subject,
            or_(KnowledgeEdge.source_key.in_(node_keys), KnowledgeEdge.target_key.in_(node_keys))))
    # Билеты, привязанные к темам этого материала, остаются, но уже без этих тем (их покажет «не найдено» при следующем разборе)
    gone = set(node_ids)
    if gone:
        for t in (await db.execute(select(ExamTicket).where(ExamTicket.user_id == user_id))).scalars().all():
            ids = list(t.node_ids or [])
            if gone.intersection(ids):
                t.node_ids = [i for i in ids if i not in gone]
    await db.execute(delete(KnowledgeNode).where(KnowledgeNode.id.in_(node_ids)))
    await db.execute(delete(Source).where(Source.id == source_id))
    await db.flush()
    # Факты этого материала, дописанные в конспекты оставшихся тем, уходят вместе с ним
    for node in (await db.execute(select(KnowledgeNode).where(
            KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject, KnowledgeNode.facts.isnot(None)))).scalars().all():
        if not node.facts:                          # JSON null в колонке: isnot(None) его пропускает
            continue
        kept = [f for f in node.facts if not (isinstance(f, dict) and f.get("s") == source_id)]
        if len(kept) != len(node.facts):
            node.facts = kept or None
    await db.flush()

    # Не осталось ни одной темы — вводный урок курса тоже не нужен
    left = (await db.execute(select(func.count(KnowledgeNode.id)).where(
        KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject, KnowledgeNode.node_key != INTRO_KEY))).scalar() or 0
    if not left:
        intro_ids = select(KnowledgeNode.id).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject,
                                                   KnowledgeNode.node_key == INTRO_KEY)
        await db.execute(delete(NodeProgress).where(NodeProgress.node_id.in_(intro_ids)))
        await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject,
                                                     KnowledgeNode.node_key == INTRO_KEY))
    return {"name": src.name, "nodes": len(node_ids), "cards": cards, "reviews": reviews, "kept_shared_nodes": len(shared)}


def _unique_keys(map_keys: list[str], taken: set[str], suffix: str) -> dict[str, str]:
    """Ключи узлов нового материала: совпавшие с уже существующими получают суффикс материала (общих «основ» двух материалов
    пока не объединяем: сведение узлов разных материалов — следующий этап)."""
    out: dict[str, str] = {}
    used = set(taken)
    for key in map_keys:
        new = key if key not in used else f"{key}__{suffix}"
        n = 2
        while new in used:
            new = f"{key}__{suffix}_{n}"
            n += 1
        out[key] = new
        used.add(new)
    return out


async def build_course_context(db, user_id: str, subject: str, exclude_source_id: int | None = None,
                               max_cards: int = 4000) -> dict | None:
    """Курс предмета для нового материала: темы (ключ, название, ярус, описание) и вопросы-ответы имеющихся карточек по темам.
    Нужен, чтобы слить совпавшие темы и не спрашивать уже спрошенное. None — в предмете ещё нет тем.
    exclude_source_id — материал, который будет заменён этой загрузкой (его в курсе уже «нет»)."""
    subject = normalize_subject(subject)
    node_q = select(KnowledgeNode).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject,
                                         KnowledgeNode.node_key != INTRO_KEY).order_by(KnowledgeNode.order_idx)
    if exclude_source_id:
        node_q = node_q.where(or_(KnowledgeNode.source_id.is_(None), KnowledgeNode.source_id != exclude_source_id))
    nodes = (await db.execute(node_q)).scalars().all()
    if not nodes:
        return None
    by_id = {n.id: n.node_key for n in nodes}
    card_q = select(Card.node_id, Card.text, Card.translation).where(
        Card.user_id == user_id, Card.subject == subject, Card.node_id.in_(list(by_id))).limit(max_cards)
    if exclude_source_id:
        card_q = card_q.where(or_(Card.source_id.is_(None), Card.source_id != exclude_source_id))
    cards: dict[str, list[dict]] = {}
    for node_id, q, a in (await db.execute(card_q)).all():
        cards.setdefault(by_id[node_id], []).append({"q": q, "a": a})
    has_intro = bool((await db.execute(select(func.count(KnowledgeNode.id)).where(
        KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject, KnowledgeNode.node_key == INTRO_KEY))).scalar())
    return {"nodes": [{"key": n.node_key, "name": n.name, "tier": n.tier, "summary": n.summary,
                       "parent": n.parent_key, "prereqs": list(n.prereq_keys or [])} for n in nodes], "cards": cards,
            "has_intro": has_intro}


async def save_learning_path(db, user_id: str, subject: str, result: dict, *, source_name: str | None = None,
                             text_hash: str | None = None, chars: int = 0, cost_usd: float = 0.0,
                             replace_source_id: int | None = None) -> dict:
    """Сохраняет карту, уроки и карточки НОВОГО материала в курс предмета. Прежние материалы, их карточки и повторения не трогаем.
    replace_source_id — заменить прежнюю нарезку именно этого материала (повторная загрузка того же файла); остальное остаётся.
    Порядок карточек: ярус → порядок узла → слой, после уже имеющихся."""
    subject = normalize_subject(subject)
    path_map, packs = result["map"], result["packs"]
    if replace_source_id:
        await delete_source(db, user_id, subject, replace_source_id)

    now = utc_now()
    existing = (await db.execute(
        select(KnowledgeNode.node_key, KnowledgeNode.order_idx).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject)
    )).all()
    taken = {r[0] for r in existing}
    order_offset = (max([r[1] for r in existing if r[0] != INTRO_KEY] or [-1]) + 1) if existing else 0
    rank = (await db.execute(
        select(func.max(Card.topological_rank)).where(Card.user_id == user_id, Card.subject == subject)
    )).scalar() or 0

    source = Source(user_id=user_id, subject=subject, name=(source_name or path_map.get("title") or "Материал")[:200],
                    title=path_map.get("title"), role="extra" if any(r[0] != INTRO_KEY for r in existing) else "main",
                    chars=chars, text_hash=text_hash, cost_usd=cost_usd, created_at=now)
    db.add(source)
    await db.flush()
    # Темы нового материала, совпавшие с уже имеющимися (result["merge"]), не создаются заново: их карточки ложатся в существующую тему
    merge = {k: v for k, v in (result.get("merge") or {}).items() if v in taken}
    existing_nodes = {n.node_key: n for n in (await db.execute(
        select(KnowledgeNode).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject))).scalars().all()}
    keys = _unique_keys([n["key"] for n in path_map["nodes"] if n["key"] not in merge], taken, f"s{source.id}")
    keys.update(merge)

    def mapped(k):
        return keys.get(k, k)

    facts_by_key = result.get("facts") or {}
    node_rows: dict[str, KnowledgeNode] = {}
    supplement_rows: dict[str, KnowledgeNode] = {}
    used_keys = set(taken) | set(keys.values())
    for n in path_map["nodes"]:
        if n["key"] in merge:
            ex = existing_nodes[merge[n["key"]]]
            pack = packs.get(n["key"]) or {}
            if pack.get("lesson") and len(pack.get("cards") or []) >= SUPPLEMENT_MIN_CARDS:
                # Новый материал добавил совпавшей теме заметный кусок: он идёт отдельной подтемой с коротким уроком-дополнением
                sup_key, i = f"{ex.node_key}__add{source.id}", 2
                while sup_key in used_keys:
                    sup_key, i = f"{ex.node_key}__add{source.id}_{i}", i + 1
                used_keys.add(sup_key)
                row = KnowledgeNode(
                    user_id=user_id, subject=subject, node_key=sup_key, name=f"{ex.name}: дополнение", tier=2,
                    parent_key=ex.node_key if ex.tier < 2 else ex.parent_key, prereq_keys=[ex.node_key],
                    order_idx=n["order"] + order_offset, summary=n["summary"], source_hint=n["src"], lesson=pack["lesson"],
                    lesson_status="ready", kind="core", source_id=source.id, created_at=now,
                    facts=merge_facts(None, facts_by_key.get(n["key"]), source.id) or None,
                )
                db.add(row)
                supplement_rows[n["key"]] = row
                node_rows[n["key"]] = row
            else:
                node_rows[n["key"]] = ex
            continue
        lesson = (packs.get(n["key"]) or {}).get("lesson")
        row = KnowledgeNode(
            user_id=user_id,
            subject=subject,
            node_key=mapped(n["key"]),
            name=n["name"],
            tier=n["tier"],
            parent_key=mapped(n["parent"]) if n["parent"] else None,
            prereq_keys=[mapped(p) for p in n["prereqs"]],
            order_idx=n["order"] + order_offset,
            summary=n["summary"],
            source_hint=n["src"],
            lesson=lesson,
            lesson_status="ready" if lesson else "failed",
            facts=merge_facts(None, facts_by_key.get(n["key"]), source.id) or None,
            kind=n.get("kind") or "core",
            source_id=source.id,
            created_at=now,
        )
        db.add(row)
        node_rows[n["key"]] = row
    for n in path_map["nodes"]:                  # у совпавшей темы без дополнения конспект пополняется фактами нового материала
        if n["key"] in merge and n["key"] not in supplement_rows and facts_by_key.get(n["key"]):
            row = node_rows[n["key"]]
            row.facts = merge_facts(row.facts, facts_by_key[n["key"]], source.id) or None
    have_edges = {(e.source_key, e.target_key, e.relation) for e in (await db.execute(
        select(KnowledgeEdge).where(KnowledgeEdge.user_id == user_id, KnowledgeEdge.subject == subject))).scalars().all()}
    for e in path_map["edges"]:
        edge = (mapped(e["from"]), mapped(e["to"]), e["relation"])
        if edge[0] == edge[1] or edge in have_edges:
            continue
        have_edges.add(edge)
        db.add(KnowledgeEdge(user_id=user_id, subject=subject, source_key=edge[0], target_key=edge[1], relation=edge[2], label=e["label"]))
    intro = result.get("intro")
    if intro and INTRO_KEY not in taken:
        db.add(KnowledgeNode(
            user_id=user_id, subject=subject, node_key=INTRO_KEY, name=INTRO_NAME, tier=0, parent_key=None,
            prereq_keys=[], order_idx=-1, summary=None, source_hint=None, lesson=intro, lesson_status="ready", created_at=now,
        ))
    await db.flush()

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
                organ_slug=node_rows[n["key"]].node_key,
                layer=c.get("layer", 1),
                topological_rank=rank,
                node_id=node_rows[n["key"]].id,
                source_id=source.id,
                answer_type=c.get("answer_type"),
                distractors=c.get("distractors"),
                next_review=now,
            ))
            cards_created += 1
    created_nodes = [r for k, r in node_rows.items() if k not in merge or k in supplement_rows]
    source.nodes_count = len(created_nodes)
    source.cards_count = cards_created
    source.kind = result.get("source_type")

    return {
        "subject": subject,
        "title": path_map.get("title") or subject,
        "source_id": source.id,
        "nodes": len(created_nodes),
        "merged_nodes": len(merge),
        "supplements": len(supplement_rows),
        "edges": len(path_map["edges"]),
        "cards": cards_created,
        "facts": sum(len(v) for v in facts_by_key.values()),
        "lessons_failed": sum(1 for r in created_nodes if r.lesson_status != "ready"),
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
    all_nodes = (await db.execute(
        select(KnowledgeNode).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject)
        .order_by(KnowledgeNode.order_idx)
    )).scalars().all()
    nodes = [n for n in all_nodes if n.node_key != INTRO_KEY]
    intro_node = next((n for n in all_nodes if n.node_key == INTRO_KEY), None)
    if not nodes:
        return {"subject": subject, "nodes": [], "edges": []}

    progress = {
        p.node_id: p for p in (await db.execute(
            select(NodeProgress).where(NodeProgress.user_id == user_id, NodeProgress.node_id.in_([n.id for n in all_nodes]))
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
    # Название курса — из первого материала предмета (показывается в центре графа); нет материалов — из последней нарезки
    first_source = (await db.execute(
        select(Source.title, Source.name).where(Source.user_id == user_id, Source.subject == subject).order_by(Source.id).limit(1)
    )).first()
    title = (first_source[0] or first_source[1]) if first_source else (await db.execute(
        select(GenerationJob.theme)
        .where(GenerationJob.user_id == user_id, GenerationJob.subject == subject, GenerationJob.status == "completed")
        .order_by(GenerationJob.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    return {
        "subject": subject,
        "title": title or subject,
        "plan": await _path_plan(db, user_id, subject),
        "intro": ({"id": intro_node.id, "done": bool(progress.get(intro_node.id) and progress[intro_node.id].lesson_done)}
                  if intro_node and intro_node.lesson else None),
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
    node = (await db.execute(select(KnowledgeNode).where(KnowledgeNode.id == node_id))).scalar_one_or_none()
    if node and node.node_key == INTRO_KEY:
        # Вводный урок не считается «темой дня»: без даты прохождения он не попадает в счётчики дня
        return progress
    if first_completion:
        await _apply_checkpoint_to_cards(db, user_id, node_id, checkpoint_score)
    progress.checkpoint_score = max(progress.checkpoint_score or 0, checkpoint_score)
    progress.lesson_done_at = progress.lesson_done_at or utc_now()
    return progress


def unlocked_node_ids_subquery(user_id: str):
    """Узлы с пройденным уроком: только их новые карточки выдаются в тренировку."""
    return select(NodeProgress.node_id).where(NodeProgress.user_id == user_id, NodeProgress.lesson_done == True)  # noqa: E712


async def ensure_intro(user_id: str, subject: str) -> bool:
    """Курс, загруженный до появления вводного урока, получает его фоном: один дешёвый вызов по карте (≈$0.003).
    Попытка одна за время жизни процесса, чтобы сбой API не превращался в повторяющиеся траты."""
    import asyncio
    from app.core.config import settings
    key = (user_id, subject)
    if getattr(settings, 'TESTING', False) or key in _intro_attempted:
        return False
    _intro_attempted.add(key)
    asyncio.create_task(_generate_intro(user_id, subject))
    return True


async def _generate_intro(user_id: str, subject: str) -> None:
    from app.database.session import AsyncSessionLocal
    from app.services.ai_gateway.path_builder import build_intro_lesson
    from app.services.generation_worker import _record_path_calls
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(KnowledgeNode).where(KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject)
                .order_by(KnowledgeNode.order_idx)
            )).scalars().all()
            if not rows or any(r.node_key == INTRO_KEY for r in rows):
                return
            title = (await db.execute(
                select(GenerationJob.theme).where(GenerationJob.user_id == user_id, GenerationJob.subject == subject,
                                                  GenerationJob.status == "completed").order_by(GenerationJob.id.desc()).limit(1)
            )).scalar_one_or_none()
            path_map = {"title": title or subject, "nodes": [
                {"key": r.node_key, "name": r.name, "tier": r.tier, "parent": r.parent_key,
                 "prereqs": r.prereq_keys or [], "order": r.order_idx, "summary": r.summary} for r in rows]}
        calls: list[dict] = []
        lesson = await build_intro_lesson(path_map, calls)
        if calls:
            await _record_path_calls(f"intro:{subject}", user_id, calls)
        if not lesson:
            return
        async with AsyncSessionLocal() as db:
            exists = (await db.execute(select(KnowledgeNode.id).where(
                KnowledgeNode.user_id == user_id, KnowledgeNode.subject == subject, KnowledgeNode.node_key == INTRO_KEY))).first()
            if not exists:
                db.add(KnowledgeNode(
                    user_id=user_id, subject=subject, node_key=INTRO_KEY, name=INTRO_NAME, tier=0, prereq_keys=[],
                    order_idx=-1, lesson=lesson, lesson_status="ready", created_at=utc_now()))
                await db.commit()
    except Exception as e:  # noqa: BLE001
        print(f"[Intro WARN] {subject}: {e}", flush=True)


# ---------------------------------------------------------------------------
# План дня и «Продолжить путь»: следующий шаг занятия одной кнопкой
# ---------------------------------------------------------------------------

# Сколько раз шаг каждого типа может встретиться за один запуск (защита от зацикливания;
# сколько уроков пройти за день, решает дневная норма, а не этот предохранитель)
RUN_STEP_LIMITS = {"review": 3, "cards": 6, "lesson": 6, "practice": 1, "intro": 1, "recap": 1}
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
            ticket_card_filter(),
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
    if scope == "exam":
        from app.services.exam_prep import next_exam_step
        return await next_exam_step(db, user_id, subject, done, extra)
    used = {t: done.count(t) for t in RUN_STEP_LIMITS}
    now = utc_now()
    base = [Card.user_id == user_id, Card.subject == subject]
    topic = scope == "topic"

    # 0. Вводный урок курса: один раз, самым первым шагом (масштаб, разделы и порядок — до первой темы)
    if not used["intro"]:
        intro_state = (await get_path_state(db, user_id, subject)).get("intro")
        if intro_state and not intro_state["done"]:
            return {"type": "intro", "node_id": intro_state["id"], "node_name": INTRO_NAME}
        if not intro_state:
            await ensure_intro(user_id, subject)

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

    # 4b. Закрепление в конце занятия: карточки, выученные недавно, ещё в заучивании (повтор через минуты) и «не дозрели»
    # к моменту, когда человек закончил. Без этого шага последняя тема дня оставалась без второго вспоминания.
    if not topic and used["recap"] < RUN_STEP_LIMITS["recap"]:
        recap = (await db.execute(
            select(func.count(Card.id)).where(*base, Card.state.in_([1, 3]), ticket_card_filter())
        )).scalar() or 0
        if recap:
            return {"type": "recap", "count": min(recap, RUN_REVIEW_BATCH * 2), "remaining": recap}

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
