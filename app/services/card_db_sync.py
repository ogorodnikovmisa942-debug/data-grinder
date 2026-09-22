# app/services/card_db_sync.py
import re
from datetime import datetime
from typing import Optional, List, Tuple
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, update

from app.database.models import (
    Card, Phrase, UserSession, TopicKnowledgeGraph, PracticeItem, utc_now
)
from app.services.graph_service import (
    resolve_subject_alias, get_all_subject_aliases, synthesize_graph_from_cards
)
from app.services.practice_service import generate_practice_session
from app.core.config import settings


def normalize_card_text_for_dedup(txt: str) -> str:
    """Нормализует текст вопроса/ответа для строгого и нечеткого сравнения дубликатов."""
    t = re.sub(r'\{\{c\d+::(.*?)(?:::.*?)?\}\}', r'\1', str(txt or ""))
    t = re.sub(r'^\s*(?:\d+[\.\)]|[-•*])\s*', '', t)  # отсекаем нумерацию "1. " или "1) "
    t = re.sub(r'[^\w\s]', '', t.lower())  # удаляем знаки препинания
    return re.sub(r'\s+', ' ', t).strip()


def deduplicate_cards_batch(cards_data: list) -> list:
    """
    Устраняет дубликаты и квазидубликаты внутри списка карточек:
    1. Исключает точные совпадения нормализованного текста вопроса.
    2. Исключает семантические дубликаты (одинаковый semantic fingerprint вопросов при совпадении/схожести ответов).
    3. Исключает карточки с идентичным ответом при высокой схожести вопроса (>0.70).
    4. Исключает квазидубликаты длинных вопросов (>25 симв, sim >= 0.92) при схожих ответах.
    """
    if not cards_data or len(cards_data) <= 1:
        return cards_data

    from difflib import SequenceMatcher
    from app.services.ai_gateway.blacklist import semantic_normalize_front

    deduped = []
    seen_norm_questions = set()
    seen_semantic_fingerprints = set()
    seen_qa_pairs = []  # list of tuples: (norm_q, norm_a, sem_key, original_card)

    for c in cards_data:
        c_text = (c.get("text", "") if isinstance(c, dict) else getattr(c, "text", "")) or ""
        c_trans = (c.get("translation", "") if isinstance(c, dict) else getattr(c, "translation", "")) or ""
        if not c_text.strip() or not c_trans.strip():
            continue

        norm_q = normalize_card_text_for_dedup(c_text)
        norm_a = normalize_card_text_for_dedup(c_trans)

        if not norm_q:
            continue

        # 1. Прямое совпадение нормализованного вопроса
        if norm_q in seen_norm_questions:
            continue

        sem_key = semantic_normalize_front(c_text)

        is_duplicate = False
        for prev_q, prev_a, prev_sem, _ in seen_qa_pairs:
            # 2. Одинаковый семантический отпечаток вопроса при совпадении/схожести ответов:
            if sem_key and prev_sem and sem_key == prev_sem:
                sim_a = SequenceMatcher(None, norm_a, prev_a).ratio() if (norm_a and prev_a) else 1.0
                if norm_a == prev_a or sim_a >= 0.70:
                    is_duplicate = True
                    break

            # 3. Идентичный ответ (norm_a == prev_a) при схожести вопросов >= 0.70:
            if norm_a and prev_a and norm_a == prev_a:
                sim_q = SequenceMatcher(None, norm_q, prev_q).ratio()
                if sim_q >= 0.70:
                    is_duplicate = True
                    break

            # 4. Почти идентичные длинные вопросы (>25 симв) со схожестью >= 0.92 и схожим ответом:
            if len(norm_q) > 25 and len(prev_q) > 25:
                sim_q = SequenceMatcher(None, norm_q, prev_q).ratio()
                if sim_q >= 0.92:
                    sim_a = SequenceMatcher(None, norm_a, prev_a).ratio() if (norm_a and prev_a) else 1.0
                    if sim_a >= 0.60:
                        is_duplicate = True
                        break

        if is_duplicate:
            continue

        seen_norm_questions.add(norm_q)
        if sem_key:
            seen_semantic_fingerprints.add(sem_key)
        seen_qa_pairs.append((norm_q, norm_a, sem_key, c))
        deduped.append(c)

    return deduped


def is_admin_or_dev(user_id: str) -> bool:
    """Проверяет, является ли пользователь администратором или dev-пользователем."""
    user_clean = str(user_id or "").strip()
    if not user_clean:
        return False
    if user_clean in ("default_user", "dev_user"):
        return True
    admin_id_str = str(getattr(settings, "ADMIN_TELEGRAM_ID", "") or "").strip()
    if admin_id_str:
        admins = [x.strip() for x in admin_id_str.split(",") if x.strip()]
        if user_clean in admins:
            return True
    return False


async def check_experiment_lock(current_user: str, db: AsyncSession):
    """Проверяет блокировку модификации колоды и настроек для участников научного эксперимента (Фаза 1).
    Администраторы и тестовые пользователи освобождены от блокировки.
    """
    if is_admin_or_dev(current_user):
        return

    session_res = await db.execute(select(UserSession).filter(UserSession.user_id == current_user))
    user_sess = session_res.scalars().first()
    if user_sess and user_sess.is_experiment_participant and user_sess.experiment_phase == 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Действие заблокировано на период проведения научного эксперимента"
        )


async def save_cards_to_database(
    cards_data: list,
    subject_slug: str,
    phrase_title: str,
    user_id: str,
    db: AsyncSession
) -> Tuple[int, str, str]:
    """Вспомогательная функция для создания карточек в БД."""
    clean_sub = subject_slug.strip().lower() or "generic"
    canonical = resolve_subject_alias(clean_sub)
    all_aliases = get_all_subject_aliases(clean_sub)
    if clean_sub not in all_aliases:
        all_aliases.append(clean_sub)
    if canonical not in all_aliases:
        all_aliases.append(canonical)

    clean_title = phrase_title.strip() or "Новый блок знаний"
    cards_data = deduplicate_cards_batch(cards_data)

    # Кэш существующих карточек пользователя по всем алиасам для исключения дубликатов
    stmt_existing = select(Card).filter(Card.user_id == user_id, Card.subject.in_(all_aliases))
    res_existing = await db.execute(stmt_existing)
    existing_cards = res_existing.scalars().all()
    existing_map = {normalize_card_text_for_dedup(c.text): c for c in existing_cards if c.text}

    # Кэш тем (Phrases) для поддержки мульти-тематической кластеризации в одном пакете карточек
    stmt_phrases = select(Phrase).filter(Phrase.user_id == user_id, Phrase.subject.in_(all_aliases))
    res_phrases = await db.execute(stmt_phrases)
    phrase_cache = {p.text.strip(): p for p in res_phrases.scalars().all() if p.text}

    cards_created = 0
    now = utc_now()
    for c in cards_data:
        c_text = (c.get("text", "") if isinstance(c, dict) else getattr(c, "text", "")) or ""
        c_trans = (c.get("translation", "") if isinstance(c, dict) else getattr(c, "translation", "")) or ""
        if not c_text.strip() or not c_trans.strip():
            continue

        c_text_clean = c_text.strip()
        c_key = normalize_card_text_for_dedup(c_text_clean)
        c_theme = ((c.get("theme", "") if isinstance(c, dict) else getattr(c, "theme", "")) or "").strip() or clean_title

        if c_theme not in phrase_cache:
            phrase = Phrase(text=c_theme, subject=canonical, user_id=user_id)
            db.add(phrase)
            await db.flush()
            phrase_cache[c_theme] = phrase

        target_phrase = phrase_cache[c_theme]

        c_sec = (c.get("secondary_text", "") if isinstance(c, dict) else getattr(c, "secondary_text", "")) or ""
        c_ex = (c.get("example", "") if isinstance(c, dict) else getattr(c, "example", "")) or ""
        c_tier = (c.get("initial_difficulty_tier", "medium") if isinstance(c, dict) else getattr(c, "initial_difficulty_tier", "medium"))
        c_mnem = c.get("mnemonic", None) if isinstance(c, dict) else getattr(c, "mnemonic", None)
        c_organ = (c.get("organ_slug") if isinstance(c, dict) else getattr(c, "organ_slug", None)) or None
        c_layer = int(c.get("layer", 1) if isinstance(c, dict) else getattr(c, "layer", 1) or 1)
        c_rank = int(c.get("topological_rank", 0) if isinstance(c, dict) else getattr(c, "topological_rank", 0) or 0)

        if c_key in existing_map:
            # Обновляем существующую карточку (сохраняя прогресс FSRS) и нормализуем subject
            card = existing_map[c_key]
            card.translation = c_trans
            card.subject = canonical
            card.phrase_id = target_phrase.id
            if c_sec:
                card.secondary_text = c_sec
            if c_ex:
                card.example = c_ex
            if c_mnem is not None:
                card.mnemonic = c_mnem
            if c_organ:
                card.organ_slug = c_organ
            if c_layer:
                card.layer = c_layer
            if c_rank:
                card.topological_rank = c_rank
            cards_created += 1
        else:
            difficulty = 5.5
            if c_tier == "easy":
                difficulty = 3.5
            elif c_tier == "hard":
                difficulty = 7.5

            stability = 1.0
            if c_mnem:
                if isinstance(c_mnem, dict) and c_mnem.get("keyword"):
                    stability = 1.5
                elif isinstance(c_mnem, str) and c_mnem.strip():
                    stability = 1.5

            c_type = (c.get("content_type") if isinstance(c, dict) else getattr(c, "content_type", None)) or ("cloze" if "{{c" in c_text_clean else "text")

            card = Card(
                phrase_id=target_phrase.id,
                user_id=user_id,
                subject=canonical,
                text=c_text_clean,
                secondary_text=c_sec,
                translation=c_trans,
                example=c_ex,
                difficulty=difficulty,
                stability=stability,
                state=0,
                mnemonic=c_mnem,
                content_type=c_type,
                organ_slug=c_organ,
                layer=c_layer,
                topological_rank=c_rank,
                next_review=now
            )
            db.add(card)
            existing_map[c_key] = card
            cards_created += 1

    return cards_created, canonical, clean_title


async def append_or_sync_cards_to_database(
    cards_data: list,
    subject_slug: str,
    phrase_title: str,
    user_id: str,
    db: AsyncSession
) -> Tuple[int, int, str, str]:
    """
    Дозагружает и синхронизирует карточки для конкретного пользователя:
    - Существующие карточки определяются по совпадению нормализованного текста вопроса (text.strip().lower()).
      Для них обновляются формулировки (translation, secondary_text, example, mnemonic),
      но полностью сохраняется когнитивный прогресс FSRS v4 (state, stability, difficulty, next_review, last_review, lapses, reps, has_seen_intro).
    - Новые карточки добавляются в базу со state=0 и next_review=now.
    Возвращает (cards_created, cards_updated, clean_sub, clean_title).
    """
    clean_sub = subject_slug.strip().lower() or "generic"
    canonical = resolve_subject_alias(clean_sub)
    all_aliases = get_all_subject_aliases(clean_sub)
    if clean_sub not in all_aliases:
        all_aliases.append(clean_sub)
    if canonical not in all_aliases:
        all_aliases.append(canonical)

    clean_title = phrase_title.strip() or "Новый блок знаний"
    cards_data = deduplicate_cards_batch(cards_data)

    # Загружаем существующие карточки пользователя по всем алиасам данного предмета
    stmt_existing = select(Card).filter(Card.user_id == user_id, Card.subject.in_(all_aliases))
    res_existing = await db.execute(stmt_existing)
    existing_cards = res_existing.scalars().all()
    existing_map = {normalize_card_text_for_dedup(c.text): c for c in existing_cards if c.text}

    # Кэш тем (Phrases)
    stmt_phrases = select(Phrase).filter(Phrase.user_id == user_id, Phrase.subject.in_(all_aliases))
    res_phrases = await db.execute(stmt_phrases)
    phrase_cache = {p.text.strip(): p for p in res_phrases.scalars().all() if p.text}

    cards_created = 0
    cards_updated = 0
    now = utc_now()

    for c in cards_data:
        c_text = (c.get("text", "") if isinstance(c, dict) else getattr(c, "text", "")) or ""
        c_trans = (c.get("translation", "") if isinstance(c, dict) else getattr(c, "translation", "")) or ""
        if not c_text.strip() or not c_trans.strip():
            continue

        c_text_clean = c_text.strip()
        c_key = normalize_card_text_for_dedup(c_text_clean)
        c_sec = (c.get("secondary_text", "") if isinstance(c, dict) else getattr(c, "secondary_text", "")) or ""
        c_ex = (c.get("example", "") if isinstance(c, dict) else getattr(c, "example", "")) or ""
        c_tier = (c.get("initial_difficulty_tier", "medium") if isinstance(c, dict) else getattr(c, "initial_difficulty_tier", "medium"))
        c_mnem = c.get("mnemonic", None) if isinstance(c, dict) else getattr(c, "mnemonic", None)

        if c_key in existing_map:
            # Существующая карточка: обновляем только текстовые поля, сохраняя весь прогресс FSRS
            card = existing_map[c_key]
            card.translation = c_trans
            card.subject = canonical  # гарантируем нормализацию предмета к каноническому
            if c_sec:
                card.secondary_text = c_sec
            if c_ex:
                card.example = c_ex
            if c_mnem is not None:
                card.mnemonic = c_mnem
            cards_updated += 1
        else:
            # Новая карточка: привязываем к Phrase и добавляем в очередь
            c_theme = ((c.get("theme", "") if isinstance(c, dict) else getattr(c, "theme", "")) or "").strip() or clean_title

            if c_theme not in phrase_cache:
                phrase = Phrase(text=c_theme, subject=canonical, user_id=user_id)
                db.add(phrase)
                await db.flush()
                phrase_cache[c_theme] = phrase

            target_phrase = phrase_cache[c_theme]

            difficulty = 5.5
            if c_tier == "easy":
                difficulty = 3.5
            elif c_tier == "hard":
                difficulty = 7.5

            stability = 1.0
            if c_mnem:
                if isinstance(c_mnem, dict) and c_mnem.get("keyword"):
                    stability = 1.5
                elif isinstance(c_mnem, str) and c_mnem.strip():
                    stability = 1.5

            c_type = (c.get("content_type") if isinstance(c, dict) else getattr(c, "content_type", None)) or ("cloze" if "{{c" in c_text_clean else "text")
            new_card = Card(
                phrase_id=target_phrase.id,
                user_id=user_id,
                subject=canonical,
                text=c_text_clean,
                secondary_text=c_sec,
                translation=c_trans,
                example=c_ex,
                difficulty=difficulty,
                stability=stability,
                state=0,
                mnemonic=c_mnem,
                content_type=c_type,
                next_review=now
            )
            db.add(new_card)
            existing_map[c_key] = new_card  # предотвращаем дубли внутри пачки
            cards_created += 1

    return cards_created, cards_updated, canonical, clean_title


async def sync_subject_knowledge_and_practice(
    db: AsyncSession,
    user_id: str,
    subject_slug: str,
    cards_data: Optional[list] = None,
    kg_data: Optional[dict] = None,
    fallback_title: Optional[str] = None
) -> None:
    """
    Автоматическая синхронизация графа знаний и интерактивных практических заданий (R2).
    - Очищает устаревшие дублирующие записи по всем алиасам предмета.
    - Обеспечивает актуальный граф знаний (25-45 узлов) и свежие практические задания.
    """
    clean_sub = subject_slug.strip().lower() or "generic"
    canonical = resolve_subject_alias(clean_sub)
    all_aliases = get_all_subject_aliases(clean_sub)
    if clean_sub not in all_aliases:
        all_aliases.append(clean_sub)
    if canonical not in all_aliases:
        all_aliases.append(canonical)

    # 1. Удаляем устаревшие конфликтующие записи графа по не-каноническим алиасам
    await db.execute(
        delete(TopicKnowledgeGraph).where(
            TopicKnowledgeGraph.user_id == user_id,
            TopicKnowledgeGraph.subject.in_(all_aliases),
            TopicKnowledgeGraph.subject != canonical
        )
    )

    # 2. Формируем актуальный граф знаний
    now = utc_now()
    graph_data = None
    tree_data = None

    # Проверяем, есть ли уже сохраненный граф для данного пользователя и предмета
    kg_stmt = select(TopicKnowledgeGraph).where(
        TopicKnowledgeGraph.user_id == user_id,
        TopicKnowledgeGraph.subject == canonical
    )
    kg_rec = (await db.execute(kg_stmt)).scalars().first()

    stmt_c = select(Card).where(Card.user_id == user_id, Card.subject.in_(all_aliases))
    res_c = await db.execute(stmt_c)
    deck_cards = res_c.scalars().all()

    if not deck_cards and not cards_data:
        # Если в предмете не осталось карточек — полностью удаляем граф и практику для ЛЮБОГО предмета
        await db.execute(delete(TopicKnowledgeGraph).where(
            TopicKnowledgeGraph.user_id == user_id,
            TopicKnowledgeGraph.subject.in_(all_aliases)
        ))
        await db.execute(delete(PracticeItem).where(
            PracticeItem.user_id == user_id,
            PracticeItem.subject.in_(all_aliases)
        ))
    elif kg_data and kg_data.get("nodes") and len(kg_data.get("nodes", [])) >= 20:
        # Явно передан свежий семантический граф от ИИ (генерация / импорт)
        graph_data = {"nodes": kg_data.get("nodes", []), "edges": kg_data.get("edges", [])}
        tree_data = kg_data.get("tree_data")
        if kg_rec:
            kg_rec.graph_data = graph_data
            kg_rec.tree_data = tree_data
            kg_rec.updated_at = now
        else:
            new_kg = TopicKnowledgeGraph(
                user_id=user_id,
                subject=canonical,
                graph_data=graph_data,
                tree_data=tree_data,
                created_at=now,
                updated_at=now
            )
            db.add(new_kg)
    elif kg_rec:
        # Граф уже существует! При удалении/редактировании отдельных карточек НЕ разрушаем структуру графа
        if isinstance(kg_rec.graph_data, dict):
            kg_rec.graph_data["deck_size"] = len(deck_cards)
            kg_rec.updated_at = now
    else:
        # Графа еще нет совсем, но карточки есть — выполняем первичный синтез
        cards_payload = [
            {
                "text": c.text,
                "translation": c.translation,
                "secondary_text": c.secondary_text or "",
                "example": c.example or "",
                "phrase": {"text": fallback_title or canonical}
            }
            for c in deck_cards
        ]
        if not cards_payload and cards_data:
            cards_payload = cards_data

        syn = synthesize_graph_from_cards(cards_payload, fallback_title=fallback_title or canonical)
        if syn and syn.get("graph_data", {}).get("nodes"):
            graph_data = syn["graph_data"]
            graph_data["deck_size"] = len(deck_cards) if deck_cards else len(cards_payload)
            tree_data = syn.get("tree_data")
            new_kg = TopicKnowledgeGraph(
                user_id=user_id,
                subject=canonical,
                graph_data=graph_data,
                tree_data=tree_data,
                created_at=now,
                updated_at=now
            )
            db.add(new_kg)

    # 3. Синхронизируем интерактивную практику
    try:
        await generate_practice_session(user_id=user_id, subject=canonical, count=10, db=db)
    except Exception as e:
        print(f"[Practice Sync] Ошибка синхронизации практики: {e}")
