"""
Конвейер «Путь знаний»: книга целиком → ярусная карта (MAP) → карточки с цитатами (CARDS) → проверка по книге
и добор непокрытых участков (AUDIT, FILL) → урок на каждый узел ИЗ его карточек (LESSON).

Качество карты и карточек важнее всего: урок пишется из карточек и выдержки книги и не может исправить их ошибки.
Запросы с книгой (MAP, GAPS, CARDS, LINKS) начинаются с одинакового префикса [system][книга], поэтому после первого
вызова DeepSeek читает книгу из кэша. Запросы AUDIT, FILL, LESSON книги не содержат (только найденные программой куски).
Расходы удерживает Budget (budget.py). Модуль чистый: ничего не пишет в БД, только возвращает результат
и телеметрию вызовов. Сохранение — в generation_worker.
"""
import asyncio
import json
import math
import os
import random
import re
import time
from collections import Counter

import httpx

from app.core.config import settings
from .blacklist import is_structurally_invalid_card, strip_secondary_spoilers
from .path_prompts import (
    PATH_BUILDER_SYSTEM_PROMPT,
    subtopic_target,
    build_source_block,
    build_map_task,
    build_cards_task,
    build_facts_task,
    build_links_task,
    build_intro_task,
    build_gaps_task,
    build_align_task,
    build_audit_task,
    build_fill_task,
    build_lessons_task,
)
from . import coverage
from . import card_quality
from . import glossary
from . import source_profile
from .budget import Budget

# ~1M токенов контекста deepseek-flash; оставляем запас под промпт, карту и ответ
MAX_SOURCE_CHARS = 1_800_000
# Платим только за фактический вывод, поэтому лимиты с запасом: обрезанный ответ дороже
MAP_MAX_TOKENS = 26000         # карта на 150 узлов — ~16 тыс. токенов; «зациклившийся» ответ на 40 тыс. стоил 2,4¢ впустую
PACK_MAX_TOKENS = 48000
LINKS_MAX_TOKENS = 8000
# Для учебника/конспекта такого объёма карта без тем (только основы) — брак, а не результат
MIN_SOURCE_CHARS_FOR_TOPICS = 15000
# Повтор карты: при 0.1–0.5 модель иногда снова отвечает «лениво» (только 8 узлов основ) — 0.7 в замере давал полную карту
RETRY_TEMPERATURE = 0.7
LAST_RETRY_TEMPERATURE = 0.9
COMPACT_MAP_NUDGE = (
    "\n\nNOTE: your previous answer was cut off because it was far too long. Return a COMPACT map: every summary at most 12 words, "
    "no repeated or near-duplicate nodes, no more nodes than the NODE BUDGET allows (plus a fifth), at most 40 edges."
)
DEGENERATE_MAP_NUDGE = (
    "\n\nNOTE: your previous answer was too thin for this source (too few nodes or missing tiers). "
    "The MAP must cover the WHOLE source with all four tiers: tier 1 (5-10 topics), tier 2 (the subtopic target of the task block), "
    "tier 3 (cases, when the budget has any), plus the edges, following the NODE BUDGET of the task block. Return the complete MAP JSON."
)
# Каждый запрос CARDS заново читает всю книгу из кэша (на книге в 340 тыс. токенов это ~$0.001), поэтому узлов в запросе много
PACK_BATCH_MAX_NODES = 28            # замер 2026-10-07: 21 запрос карточек на книгу = 21 чтение книги из кеша (2,1¢); ответ узла ~0,7 тыс. токенов
# Уроки пишутся без книги, только из карточек и выдержки — узлов в запросе можно больше, ответ короткий
LESSON_BATCH_NODES = 6
LESSON_MAX_TOKENS = 24000
# Выдержка из книги на узел: ~300 знаков на карточку, но не меньше 700 и не больше 2200
LESSON_EXCERPT_PER_CARD = 300
LESSON_EXCERPT_MIN = 700
LESSON_EXCERPT_MAX = 2200
# Потолки на ЧИСЛО карточек: на узел (coverage.MAX_CARDS_PER_NODE), на всю книгу (MAX_TOTAL_CARDS), по бюджету (Budget.card_cap),
# «сверх квоты» (CARD_OVER_QUOTA), на добор (MAX_FILL_SPANS, FILL_MAX_CARDS).
# Деньги (Budget, потолок PATH_BUDGET_USD) ограничивают число карточек ВСЕГДА, независимо от этого переключателя.
# ВЫКЛЮЧЕНЫ по умолчанию (решение пользователя 2026-10-05: сначала смотрим, сколько карточек нужно, чтобы покрыть книгу).
# PATH_CARD_CAPS=1 возвращает все потолки сразу.
CARD_CAPS = os.getenv("PATH_CARD_CAPS", "0").lower() in ("1", "true", "yes")
# Узел может дать карточек сверх квоты не больше, чем на столько (модель просили «N-1…N+1»); только при CARD_CAPS
CARD_OVER_QUOTA = 3
# Проверка подозрительных карточек по куску книги (AUDIT)
AUDIT_MAX_CARDS = 60
AUDIT_BATCH = 20
AUDIT_MAX_TOKENS = 8000
# Добор участков книги, на которые не пришлось ни одной карточки (FILL)
MAX_FILL_SPANS = 12
FILL_BATCH_SPANS = 3
FILL_MAX_TOKENS = 12000
FILL_CONCURRENCY = 4
FILL_MIN_CARDS, FILL_MAX_CARDS = 2, 6
# Правка урока, в котором не названы ответы карточек: берём узлы, где не хватает ≥ 2 ответов или ≥ 30% карточек
LESSON_OMITTED_BELOW = 0.5
LESSON_REPAIR_MIN_OMITTED = 2
LESSON_REPAIR_SHARE = 0.3
LESSON_IN_REPAIR_TOKENS = 1500      # для оценки расхода на правку одного урока
LESSON_OUT_REPAIR_TOKENS = 600
# Проверка охвата по оглавлению: сколько неупомянутых разделов отдаём модели на разбор за один запрос
MAX_GAP_SECTIONS = 30
GAPS_MAX_TOKENS = 8000
# Если узлы ссылаются хоть на столько крупных разделов — формат src пригоден для проверки; иначе она дала бы ложные «дыры»
MIN_REFERENCED_SHARE = 0.2
# ЦЕЛЬ по числу карточек: одна на столько знаков источника (≈ страница учебника; для книги в 1,1 млн знаков это ~490 карточек).
# Решение пользователя 2026-10-06: число карточек — цель по смыслу (колода = конспект, а не пересказ), а не результат расчёта по деньгам.
# Деньги (Budget) только ограничивают необязательные этапы и предупреждают в журнале, если цель не укладывается в потолок.
# Тот же коэффициент задаёт размер карты (сколько подтем просить: по ~6 карточек на подтему).
CHARS_PER_CARD = int(os.getenv("PATH_CHARS_PER_CARD", "2300"))
# «Конспект темы»: остаток текста, которого карточки не спрашивают, идёт готовым списком фактов узла (урок их не пересказывает).
# PATH_FACTS=0 выключает; сколько фактов — source_profile.FACTS_PER_CARD (PATH_FACTS_SCALE)
FACTS = os.getenv("PATH_FACTS", "1").lower() in ("1", "true", "yes")
FACT_MAX_WORDS = 30
# Факты запрашиваются отдельной задачей по блокам книги (по ~3000 знаков, подряд): так модель обязана пройти весь текст, а не пересказать
# узел в общих чертах (замер 2026-10-07 на 148 страницах: по узлам — 24% эталона в конспекте, длинные разделы получали мало фактов)
FACT_BATCH_BLOCKS = 60                 # блоков в одном запросе: ответ ~60 × 4 факта × 42 токена = 10 тыс. токенов; запрос заново читает книгу из кэша
FACTS_MAX_TOKENS = 32000               # платим за фактический вывод; при 16000 два из трёх замеренных прогонов получили обрезанный ответ, и батч делился заново
FACT_TOKENS_BASE, FACT_TOKENS_PER_ASKED = 800, 130      # потолок ответа: с запасом ×3 на запрошенное число фактов (если модель не слушается K, платим не больше)
FACT_OVER_K = 1.6                                       # лишнее сверх K отрезается при разборе: блок отвечает не больше чем K×1,6+1 фактами
MIN_FACT_SOURCE_CHARS = 3000           # короче этого факты не просим: весь текст и так укладывается в карточки
FACT_ANCHOR_WORDS = (10, 6)            # сколько слов начала и конца блока показать модели, чтобы она нашла блок в книге
MAX_TOTAL_CARDS = int(os.getenv("PATH_MAX_TOTAL_CARDS", "700"))
# Паки идут одновременно: книга к этому моменту уже в кэше после карты. Замер при 4: 17 запросов = 5 «волн» по ~75 с
PACK_CONCURRENCY = int(os.getenv("PATH_PACK_CONCURRENCY", "8"))
# Уроки не несут книги (≈11 тыс. токенов на запрос, ≈13 с): параллелизм у них масштабируется почти линейно, в отличие от запросов с книгой
LESSON_CONCURRENCY = int(os.getenv("PATH_LESSON_CONCURRENCY", "16"))
LLM_TIMEOUT_S = 600.0
# Карта строится до двух раз (вторая читает книгу из кэша, но её ответ — ещё ~$0.008 на книгу) — берём лучшую по программной оценке.
# Вторая нужна, только если первая получила оценку ниже MAP_GOOD_SCORE (из 8 возможных баллов score_map)
MAP_CANDIDATES = 2
MAP_GOOD_SCORE = 5.5
# Отбраковка непроверенных карточек и обрезка лишнего по важности (PATH_QUALITY_GATE=0 выключает)
QUALITY_GATE = os.getenv("PATH_QUALITY_GATE", "1").lower() in ("1", "true", "yes")
MAP_SECOND_TEMPERATURE = 0.4
# Уроки и карточки основ и тем (ярус 0–1) можно писать в режиме «обдумывания» (PATH_THINK_CORE=1).
# Замер на «Общей теории права» (481 с., дешёвые часы): +≈$0.035 к книге (~+20%), а метафоры и качество уроков
# без него уже на уровне — поэтому по умолчанию выключено.
THINK_CORE = os.getenv("PATH_THINK_CORE", "0").lower() in ("1", "true", "yes")
CORE_PACK_MAX_TOKENS = 64000
# Зависание DeepSeek: повторяем той же моделью (качество не прыгает), последняя попытка — страховочная модель
HANG_RETRIES = 2
HANG_BACKOFF_S = 20.0

VALID_RELATIONS = {
    "part_of", "depends_on", "kind_of", "demarcated_from", "leads_to", "applies_to", "example_of",
    # прежние типы связей: остаются допустимыми, чтобы не терять уже построенные курсы
    "appealed_to", "excludes_application", "subject_to_jurisdiction",
}
VALID_EMOTIONS = {"idle", "talk", "happy", "think", "surprised", "confused"}
VALID_ANSWER_TYPES = {
    "term", "organ", "person", "date", "number", "duration", "rule", "criterion", "consequence",
}
VALID_LEVELS = {"easy", "medium", "hard"}
BANNED_DISTRACTOR_RE = re.compile(r"(все\s+(выше)?перечисленн|ни\s+один\s+из|нет\s+верного|all of the above|none of the above)", re.IGNORECASE)


class PathBuildError(RuntimeError):
    pass


def _get_call_deepseek():
    # Позднее связывание: тесты патчат app.services.ai_gateway.client.call_deepseek
    from . import client
    return client.call_deepseek


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower())
    return re.sub(r"_+", "_", s).strip("_")[:64]


def _norm_text(value: str) -> str:
    return re.sub(r"[\s\.\,\;\:\!\?«»\"'()\-—]+", " ", str(value or "").lower()).strip()


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# MAP
# ---------------------------------------------------------------------------

def normalize_edges(raw_edges, valid_keys: set[str], existing: list[dict] | None = None) -> list[dict]:
    """Чистые рёбра: концы существуют, без петель и дублей пары узлов (в любом направлении)."""
    edges: list[dict] = []
    seen_pairs = {frozenset((e["from"], e["to"])) for e in (existing or [])}
    for e in raw_edges or []:
        if not isinstance(e, dict):
            continue
        a = _slug(e.get("from") or e.get("source") or "")
        b = _slug(e.get("to") or e.get("target") or "")
        if a not in valid_keys or b not in valid_keys or a == b:
            continue
        pair = frozenset((a, b))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        rel = str(e.get("relation") or "").strip().lower()
        if rel not in VALID_RELATIONS:
            rel = "depends_on"
        edges.append({"from": a, "to": b, "relation": rel, "label": str(e.get("label") or "").strip()[:60]})
    return edges


def normalize_map(raw: dict) -> dict:
    """Приводит карту к инвариантам: уникальные ключи, ярусы 0..3, валидные родители,
    ациклические пререквизиты (пререквизит всегда раньше по (tier, order)), чистые рёбра."""
    if not isinstance(raw, dict):
        raise PathBuildError("MAP: ответ не является объектом")

    nodes: list[dict] = []
    seen: set[str] = set()
    for idx, n in enumerate(raw.get("nodes") or [], 1):
        if not isinstance(n, dict):
            continue
        name = str(n.get("name") or "").strip()
        key = _slug(n.get("key") or n.get("id") or "")
        if not name or not key or key in seen:
            continue
        seen.add(key)
        nodes.append({
            "key": key,
            "name": name[:120],
            "tier": max(0, min(3, _as_int(n.get("tier"), 2))),
            "parent": _slug(n.get("parent") or "") or None,
            "prereqs": [_slug(p) for p in (n.get("prereqs") or []) if _slug(p)],
            "order": _as_int(n.get("order"), idx),
            "summary": str(n.get("summary") or "").strip(),
            "src": str(n.get("src") or "").strip()[:200],
            "kind": "core",                                    # все разделы книги — основной материал: деления «справочный/основной» нет
        })

    if not nodes:
        raise PathBuildError("MAP: нет ни одного узла")
    if not any(n["tier"] == 0 for n in nodes):
        raise PathBuildError("MAP: нет узлов яруса 0 (основы)")

    nodes.sort(key=lambda n: (n["tier"], n["order"]))
    for pos, n in enumerate(nodes, 1):
        n["order"] = pos
    by_key = {n["key"]: n for n in nodes}
    rank = {n["key"]: n["order"] for n in nodes}

    for n in nodes:
        parent = by_key.get(n["parent"]) if n["parent"] else None
        if n["tier"] == 0 or not parent or parent["tier"] >= n["tier"]:
            n["parent"] = None

        # Ацикличность: пререквизит обязан стоять раньше в дидактическом порядке
        prereqs = []
        for p in n["prereqs"]:
            if p in by_key and p != n["key"] and rank[p] < rank[n["key"]] and p not in prereqs:
                prereqs.append(p)
        if n["tier"] == 2 and n["parent"]:
            # Подтемы одной ветки не выстраиваем в цепочку: внутри открытой ветки порядок свободный
            prereqs = [p for p in prereqs if by_key[p]["parent"] != n["parent"]]
        if n["tier"] == 0:
            prereqs = []
        elif not prereqs:
            if n["tier"] == 1:
                prereqs = [m["key"] for m in nodes if m["tier"] == 0]
            elif n["tier"] == 3 and n["parent"]:
                prereqs = [m["key"] for m in nodes if m["parent"] == n["parent"] and m["tier"] == 2]
            if not prereqs and n["parent"]:
                prereqs = [n["parent"]]
            if not prereqs:
                prereqs = [m["key"] for m in nodes if m["tier"] == n["tier"] - 1 and rank[m["key"]] < rank[n["key"]]][:3]
        if n["parent"] and n["parent"] not in prereqs and n["tier"] == 2:
            prereqs.insert(0, n["parent"])
        n["prereqs"] = prereqs

    edges = normalize_edges(raw.get("edges") or [], set(by_key))

    domain = str(raw.get("domain") or "generic").strip().lower()
    source_type = str(raw.get("source_type") or "").strip().lower()

    return {
        "title": str(raw.get("title") or "").strip()[:160],
        "domain": domain,
        "source_type": source_type if source_type in ("textbook", "article", "notes", "lecture", "guide") else None,
        "nodes": nodes,
        "edges": edges,
    }


def plan_pack_batches(path_map: dict, max_nodes: int = PACK_BATCH_MAX_NODES, split_core: bool = False) -> list[list[str]]:
    """Группирует узлы в батчи запросов CARDS: основы отдельно, дальше — по веткам яруса 1.
    split_core=True: ядро (ярус 0–1) целиком идёт отдельными батчами — их пишем в режиме обдумывания,
    а подтемы и кейсы веток — обычным режимом."""
    nodes = path_map["nodes"]
    batches: list[list[str]] = []

    def push(keys: list[str]):
        for i in range(0, len(keys), max_nodes):
            if keys[i:i + max_nodes]:
                batches.append(keys[i:i + max_nodes])

    push([n["key"] for n in nodes if n["tier"] == 0])
    placed = {n["key"] for n in nodes if n["tier"] == 0}
    if split_core:
        tier1 = [n["key"] for n in nodes if n["tier"] == 1]
        push(tier1)
        placed.update(tier1)
        for branch_key in tier1:
            push([n["key"] for n in nodes if n["parent"] == branch_key])
            placed.update(n["key"] for n in nodes if n["parent"] == branch_key)
        push([n["key"] for n in nodes if n["key"] not in placed])
        return batches
    groups: list[list[str]] = []
    for branch in (n for n in nodes if n["tier"] == 1):
        members = [branch["key"]] + [n["key"] for n in nodes if n["parent"] == branch["key"]]
        placed.update(members)
        groups.append(members)
    groups.append([n["key"] for n in nodes if n["key"] not in placed])
    # Мелкие ветки склеиваем подряд, пока влезает в запрос: каждый лишний запрос — ещё одно чтение всей книги из кэша
    current: list[str] = []
    for members in groups:
        if current and len(current) + len(members) > max_nodes:
            push(current)
            current = []
        current += members
    push(current)
    return batches


# ---------------------------------------------------------------------------
# Разбор ответов CARDS и LESSON
# ---------------------------------------------------------------------------

def _clean_distractors(answer: str, raw_list) -> list[str] | None:
    ans_norm = _norm_text(answer)
    out: list[str] = []
    seen: set[str] = {ans_norm}
    for d in raw_list or []:
        text = str(d or "").strip()
        norm = _norm_text(text)
        if not norm or norm in seen or BANNED_DISTRACTOR_RE.search(text):
            continue
        # Частичная версия верного ответа — не дистрактор
        if len(norm) >= 4 and (norm in ans_norm or ans_norm in norm):
            continue
        seen.add(norm)
        out.append(text)
    return out[:3] if len(out) >= 2 else None


def _normalize_lesson(raw, valid_keys: set[str], max_screens: int = 10) -> dict | None:
    if not isinstance(raw, dict):
        return None
    screens = []
    for s in raw.get("screens") or []:
        if not isinstance(s, dict):
            continue
        say = str(s.get("say") or "").strip()
        if not say:
            continue
        emo = str(s.get("emo") or "talk").strip().lower()
        screens.append({
            "say": say,
            "emo": emo if emo in VALID_EMOTIONS else "talk",
            "focus": [k for k in (_slug(f) for f in (s.get("focus") or [])) if k in valid_keys][:3],
        })
    screens = screens[:max_screens]
    if len(screens) < 3:
        return None

    checks = []
    for c in raw.get("check") or []:
        if not isinstance(c, dict):
            continue
        q = str(c.get("q") or "").strip()
        options = [str(o).strip() for o in (c.get("options") or []) if str(o).strip()]
        answer = _as_int(c.get("answer"), -1)
        if not q or len(options) < 2 or not (0 <= answer < len(options)):
            continue
        if len({_norm_text(o) for o in options}) != len(options):
            continue
        checks.append({"q": q, "options": options[:4], "answer": answer, "why": str(c.get("why") or "").strip()})
    return {"screens": screens, "check": checks[:2]}


# Вопрос не должен ссылаться на текст: на карточке в приложении текста перед глазами нет
_TEXT_REF_CLAUSE = re.compile(
    r"\s*,?\s*(?:согласно|по)\s+(?:тексту|учебнику|источнику|главе|материалу|параграфу|определению\s+из\s+(?:главы|текста|учебника))\s*,?", re.IGNORECASE)
_TEXT_REF_LEFT = re.compile(
    r"(?:в|из|по)\s+(?:этом\s+|данном\s+)?(?:тексте|учебнике|главе|параграфе|источнике|материале|разделе)\b"
    r"|(?:отмечает|указывает|говорит|пишет|утверждает|называет)\s+(?:автор|источник|текст|учебник)\b|\bавтор\s+(?:текста|учебника)\b"
    r"|according to the (?:text|source|chapter)", re.IGNORECASE)


def strip_text_reference(question: str) -> tuple[str, bool]:
    """Вырезает оговорку «…, согласно тексту,…». Возвращает (вопрос, осталась ли ссылка на текст)."""
    cleaned = _TEXT_REF_CLAUSE.sub(" ", question)
    cleaned = re.sub(r"\s+([?,.])", r"\1", re.sub(r"\s{2,}", " ", cleaned)).strip()
    return cleaned, bool(_TEXT_REF_LEFT.search(cleaned))


LAYER_DIFFICULTY = {0: "easy", 1: "medium", 2: "hard"}


def _normalize_card(raw, node: dict, domain: str, rejected: list | None = None, course: str = "") -> dict | None:
    """Модель пишет только вопрос, ответ, цитату, слой, тип и дистракторы (пример — по желанию). Строку контекста «Курс | Тема»
    и сложность добавляет программа: это экономит около 20% вывода на карточку."""
    if not isinstance(raw, dict):
        return None
    card = {
        "text": str(raw.get("t") or "").strip(),
        "secondary_text": str(raw.get("s") or "").strip() or " | ".join(x for x in (course, node["name"]) if x),
        "translation": str(raw.get("d") or "").strip(),
        "example": str(raw.get("e") or "").strip(),
        "initial_difficulty_tier": str(raw.get("l") or "").strip().lower(),
        "layer": max(0, min(2, _as_int(raw.get("y"), 1))),
        "answer_type": str(raw.get("at") or "").strip().lower() or None,
        "theme": node["name"],
        "node_key": node["key"],
        "evidence": re.sub(r"\s+", " ", str(raw.get("ev") or "")).strip()[:240],
    }
    card["secondary_text"] = strip_secondary_spoilers(card["text"], card["translation"], card["secondary_text"])
    if card["answer_type"] not in VALID_ANSWER_TYPES:
        card["answer_type"] = None
    if node["tier"] == 3:
        card["layer"] = 2
    if card["initial_difficulty_tier"] not in VALID_LEVELS:
        card["initial_difficulty_tier"] = LAYER_DIFFICULTY.get(card["layer"], "medium")
    question, still_refers = strip_text_reference(card["text"])
    if question != card["text"] and not still_refers:
        card["text"] = question
        card["text_reference_removed"] = True
    blocked, reason = is_structurally_invalid_card(card)
    if not blocked and still_refers:
        blocked, reason = True, "ссылка на текст в вопросе"
    if blocked:
        if rejected is not None:
            rejected.append((reason, card["text"][:90]))
        return None
    card["distractors"] = _clean_distractors(card["translation"], raw.get("x"))
    return card


def normalize_facts(raw, known_numbers: set[str] | None = None, limit: int = 40) -> list[str]:
    """Факты узла: короткие утверждения по книге, которых нет в карточках. Отбрасываем пустое, слишком длинное
    и всё, где названо число, которого нет в книге (выдуманные цифры — самое дешёвое, что можно проверить без вызова модели)."""
    out: list[str] = []
    for x in raw if isinstance(raw, list) else []:
        text = re.sub(r"\s+", " ", str(x or "")).strip()
        if not text or len(text.split()) > FACT_MAX_WORDS or text in out:
            continue
        if known_numbers is not None and any(n not in known_numbers for n in re.findall(r"\d+", text)):
            continue
        out.append(text)
    return out[:limit]


def normalize_cards(raw: dict, path_map: dict, requested_keys: list[str],
                    quotas: dict[str, int] | None = None, rejected: list | None = None) -> dict[str, list[dict]]:
    """{node_key: [карточки]} только для запрошенных узлов. Повторы вопросов внутри узла отброшены; при CARD_CAPS лишнее
    сверх квоты отрезано с конца: карточки идут в порядке обучения."""
    if not isinstance(raw, dict):
        raise PathBuildError("CARDS: ответ не является объектом")
    by_key = {n["key"]: n for n in path_map["nodes"]}
    requested = set(requested_keys)
    domain = path_map.get("domain") or "generic"
    course = (path_map.get("title") or "").strip()
    quotas = quotas or {}

    result: dict[str, list[dict]] = {}
    for item in raw.get("nodes") or []:
        if not isinstance(item, dict):
            continue
        key = _slug(item.get("key") or "")
        if key not in requested or key in result:
            continue
        node = by_key[key]
        cards, seen_fronts = [], set()
        for c in item.get("cards") or []:
            card = _normalize_card(c, node, domain, rejected, course)
            if not card:
                continue
            front = _norm_text(card["text"])
            if front in seen_fronts:
                continue
            seen_fronts.add(front)
            cards.append(card)
        limit = (quotas[key] + CARD_OVER_QUOTA) if (CARD_CAPS and key in quotas) else None
        result[key] = cards[:limit] if limit else cards
    return result


def normalize_lessons(raw: dict, path_map: dict, requested_keys: list[str],
                      card_fronts: dict[str, set[str]] | None = None) -> dict[str, dict]:
    """{node_key: урок} только для запрошенных узлов, у которых получился годный урок (≥ 3 экранов)."""
    if not isinstance(raw, dict):
        raise PathBuildError("LESSON: ответ не является объектом")
    valid_keys = {n["key"] for n in path_map["nodes"]}
    requested = set(requested_keys)
    result: dict[str, dict] = {}
    for item in raw.get("nodes") or []:
        if not isinstance(item, dict):
            continue
        key = _slug(item.get("key") or "")
        if key not in requested or key in result:
            continue
        lesson = _normalize_lesson(item.get("lesson"), valid_keys)
        if lesson:
            lesson["check"] = _finalize_checks(lesson["check"], (card_fronts or {}).get(key, set()), key)
            result[key] = lesson
    return result


def _finalize_checks(checks: list[dict], card_fronts: set[str], node_key: str) -> list[dict]:
    """Убирает вопросы-дубли карточек и перемешивает варианты: модель почти всегда ставит верный ответ первым."""
    rng = random.Random(node_key)  # детерминированно: повторная нормализация даёт тот же порядок
    final = []
    for c in checks:
        if _norm_text(c["q"]) in card_fronts:
            continue
        correct = c["options"][c["answer"]]
        options = c["options"][:]
        rng.shuffle(options)
        final.append({**c, "options": options, "answer": options.index(correct)})
    return final


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _log_call(calls_log: list, label: str, started: float, meta: dict, error: str | None = None) -> None:
    calls_log.append({
        "label": label,
        "duration_ms": int((time.time() - started) * 1000),
        "prompt_tokens": meta.get("prompt_tokens", 0),
        "cache_hit_tokens": meta.get("cache_hit_tokens", 0),
        "cache_miss_tokens": meta.get("cache_miss_tokens", 0),
        "completion_tokens": meta.get("completion_tokens", 0),
        "cost_usd": meta.get("cost_usd", 0.0),
        "finish_reason": meta.get("finish_reason"),
        "is_truncated": meta.get("is_truncated", False),
        "model": meta.get("model_resolved"),
        "error": error,
        "raw_head": meta.get("raw_head"),
        "raw_tail": meta.get("raw_tail"),
    })


async def _call(user_prompt: str, max_tokens: int, label: str, calls_log: list, temperature: float = 0.1,
                thinking: bool = False) -> dict:
    from .client import LLMCallError, LLMTransientError, MODEL_FALLBACK
    last_err: Exception | None = None
    for attempt in range(HANG_RETRIES + 1):
        started = time.time()
        extra: dict = {}
        if thinking:
            extra["thinking"] = True
        if attempt == HANG_RETRIES and HANG_RETRIES > 0 and not isinstance(last_err, LLMTransientError):
            extra["model"] = MODEL_FALLBACK   # две попытки основной моделью не прошли (зависание) — страхуемся
            # При 429/5xx переходить на pro не нужно: он в 3–7 раз дороже, а перегружен поставщик, не модель
        try:
            res, meta = await _get_call_deepseek()(
                user_prompt,
                system_instruction=PATH_BUILDER_SYSTEM_PROMPT,
                max_tokens=max_tokens,
                timeout=LLM_TIMEOUT_S,
                temperature=temperature,
                **extra,
            )
        except LLMCallError as e:
            # Ответ оплачен, даже если он непригоден — учитываем расход
            _log_call(calls_log, label, started, e.meta, error=str(e)[:200])
            raise
        except (asyncio.TimeoutError, TimeoutError, httpx.TransportError, LLMTransientError) as e:
            _log_call(calls_log, label, started, {}, error=f"{type(e).__name__}: зависание/сеть")
            last_err = e
            print(f"[Path Builder WARN] {label}: нет ответа ({type(e).__name__}), попытка {attempt + 1}/{HANG_RETRIES + 1}", flush=True)
            if attempt < HANG_RETRIES:
                await asyncio.sleep(HANG_BACKOFF_S * (attempt + 1))
            continue
        _log_call(calls_log, label, started, meta)
        return res
    raise RuntimeError(f"{label}: DeepSeek не ответил за {HANG_RETRIES + 1} попытки: {last_err}")


BIG_SOURCE_CHARS = 200_000


def map_problem(path_map: dict, source_chars: int, budget: dict | None = None) -> str | None:
    """Карта слишком скудная для такого источника («ленивый» ответ модели)? Возвращает причину или None.
    Для коротких текстов проверяем только наличие тем; для книги — размер карты и подтемы."""
    if source_chars < MIN_SOURCE_CHARS_FOR_TOPICS:
        return None
    nodes = path_map["nodes"]
    tiers = [sum(1 for n in nodes if n["tier"] == t) for t in range(4)]
    if not any(n["tier"] >= 1 for n in nodes):
        return f"вырожденная карта: {len(nodes)} узлов, ни одной темы"
    if budget:
        if len(nodes) < max(5, round(0.5 * budget["total"])):
            return f"скудная карта: {len(nodes)} узлов при бюджете {budget['total']}"
        if source_chars >= BIG_SOURCE_CHARS and tiers[2] < max(2, round(0.4 * budget["tier2"])):
            return f"скудная карта: подтем {tiers[2]} при бюджете {budget['tier2']}"
        return None
    if len(nodes) < max(6, source_chars // 40_000):
        return f"скудная карта: {len(nodes)} узлов на {source_chars // 1000} тыс. знаков источника"
    if source_chars >= BIG_SOURCE_CHARS and (tiers[1] < 3 or tiers[2] < 8):
        return f"скудная карта: тем {tiers[1]}, подтем {tiers[2]} для книги"
    return None


_TIER_TARGETS = {0: (5, 8), 1: (5, 10), 2: (15, 30), 3: (5, 12)}


def score_map(path_map: dict, source_chars: int | None = None, budget: dict | None = None) -> float:
    """Программная оценка карты (больше — лучше): ярусы в заданных диапазонах (подтем — по размеру книги), у узлов есть
    summary и src, подтемы привязаны к темам, достаточно осмысленных связей. Не заменяет чтение карты человеком,
    но отсеивает брак."""
    nodes = path_map["nodes"]
    if not nodes:
        return 0.0
    targets = dict(_TIER_TARGETS)
    if budget:                           # ярусы — вокруг бюджета узлов этого материала, а не вокруг размеров «для книги»
        targets = {t: (max(0, round(0.6 * budget[f"tier{t}"])), max(1, round(1.5 * budget[f"tier{t}"]) + 1)) for t in range(4)}
    elif source_chars:
        k = subtopic_target(source_chars, CHARS_PER_CARD)
        targets[2] = (max(8, round(0.6 * k)), max(30, round(1.6 * k)))
    score = 0.0
    for tier, (lo, hi) in targets.items():
        n = sum(1 for x in nodes if x["tier"] == tier)
        score += 1.0 if lo <= n <= hi else max(0.0, 1.0 - (lo - n if n < lo else n - hi) / max(lo, 1))
    score += sum(1 for x in nodes if x["summary"]) / len(nodes)
    score += sum(1 for x in nodes if x["src"]) / len(nodes)
    deep = [x for x in nodes if x["tier"] >= 2]
    score += (sum(1 for x in deep if x["parent"]) / len(deep)) if deep else 0.0
    edges = len(path_map["edges"])
    lo, hi = (max(4, round(0.4 * len(nodes))), max(12, round(1.8 * len(nodes)))) if budget else (15, 60)
    score += 1.0 if lo <= edges <= hi else max(0.0, 1.0 - (lo - edges if edges < lo else edges - hi) / max(lo, 1))
    return round(score, 3)


async def build_knowledge_map(text: str, subject: str, calls_log: list, attempts: int = 3, candidates: int = 1,
                              source_kind: str | None = None) -> dict:
    from .client import LLMOutputTruncated
    # Бюджет узлов: из цели по карточкам (тип, названный пользователем, иначе по тексту — до того, как модель назовёт свой): ≈ цель / 4 узлов
    budget = source_profile.node_budget(source_profile.target_cards(source_profile.resolve_kind(source_kind, text), text))
    base_prompt = build_source_block(text) + build_map_task(subject, len(text), CHARS_PER_CARD, budget)
    prompt = base_prompt
    last_err = None
    best = None
    compact_retry_used = False
    for attempt in range(1, attempts + 1):
        try:
            # Повтор после брака — с большей температурой, иначе модель выдаст тот же ответ
            temperature = 0.1 if attempt == 1 else (RETRY_TEMPERATURE if attempt == 2 else LAST_RETRY_TEMPERATURE)
            path_map = normalize_map(await _call(prompt, MAP_MAX_TOKENS, f"map#{attempt}", calls_log, temperature))
            problem = map_problem(path_map, len(text), budget)
            if problem:
                # Подсказка уходит только в хвост запроса: префикс «system + книга» остаётся прежним (кэш DeepSeek)
                prompt = base_prompt + DEGENERATE_MAP_NUDGE
                raise PathBuildError(problem)
            best = path_map
            break
        except LLMOutputTruncated as e:
            # Тот же запрос обрежется снова: повторяем один раз, но с просьбой о компактной карте и теплее; второй обрыв — ошибка
            if compact_retry_used:
                raise PathBuildError(f"Карта знаний не поместилась в лимит ответа: {e}") from e
            compact_retry_used = True
            prompt = base_prompt + COMPACT_MAP_NUDGE
            last_err = e
            print(f"[Path Builder WARN] MAP попытка {attempt}/{attempts}: ответ обрезан, повторяю с просьбой о компактной карте", flush=True)
        except Exception as e:  # noqa: BLE001 — ретраим сбои сети/парсинга
            last_err = e
            print(f"[Path Builder WARN] MAP попытка {attempt}/{attempts}: {e}", flush=True)
    if best is None:
        raise PathBuildError(f"Не удалось построить карту знаний: {last_err}")

    # Вторая карта: книга уже в кэше, платим почти только за вывод. Берём лучшую; сбой второй карты не фатален.
    # Первая карта с хорошей оценкой — вторую не строим: экономия идёт в карточки
    for extra in range(2, candidates + 1):
        if score_map(best, len(text), budget) >= MAP_GOOD_SCORE:
            print(f"[Path Builder] MAP: оценка {score_map(best, len(text), budget)} достаточна, вторую карту не строим", flush=True)
            break
        try:
            other = normalize_map(await _call(prompt, MAP_MAX_TOKENS, f"map#alt{extra}", calls_log, MAP_SECOND_TEMPERATURE))
            if map_problem(other, len(text), budget):
                continue
            s_best, s_other = score_map(best, len(text), budget), score_map(other, len(text), budget)
            print(f"[Path Builder] MAP: оценки {s_best} (1) и {s_other} (альтернатива)", flush=True)
            if s_other > s_best:
                best = other
        except Exception as e:  # noqa: BLE001
            print(f"[Path Builder WARN] MAP-альтернатива {extra}: {e}", flush=True)
    return best


def _section_ref(section: dict) -> str:
    return f"Гл. {section['chapter']}, §{section['section']}"


def _gap_prompt_lines(text: str, sections: list[dict]) -> str:
    lines = []
    for i, s in enumerate(sections, 1):
        body = re.sub(r"\s+", " ", text[s["start"]:s["end"]])
        head = body[len(s["title"]):][:90].strip() if body.startswith(s["title"]) else body[:90]
        lines.append(f"S{i} | {_section_ref(s)} | «{s['title'][:90]}» | ~{s['chars'] // 1000} тыс. знаков | begins: «{head}»")
    return "\n".join(lines)


def apply_gap_answer(path_map: dict, raw: dict, sections: list[dict]) -> tuple[dict, dict[str, int], dict]:
    """Вставляет в карту узлы, которые модель предложила для неупомянутых разделов.
    Возвращает (новая карта, {ключ нового узла: объём источника в знаках}, отчёт). Ничего не придумываем: узел без имени,
    с чужим родителем или дубль существующего ключа отбрасываются/исправляются."""
    by_key = {n["key"]: n for n in path_map["nodes"]}
    tier1 = [n for n in path_map["nodes"] if n["tier"] == 1]
    new_nodes: list[dict] = []
    size_hints: dict[str, int] = {}
    report = {"sections": len(sections), "covered": 0, "added": 0, "skipped": 0}
    answers = {str(g.get("id")): g for g in ((raw or {}).get("gaps") or []) if isinstance(g, dict)}
    for i, s in enumerate(sections, 1):
        g = answers.get(f"S{i}") or {}
        proposed = [x for x in (g.get("nodes") or []) if isinstance(x, dict) and str(x.get("name") or "").strip()][:2]
        if not proposed:
            report["covered" if g.get("covered_by") else "skipped"] += 1
            continue
        for x in proposed:
            key = _slug(x.get("key") or x.get("name"))
            if not key:
                continue
            while key in by_key or any(n["key"] == key for n in new_nodes):
                key += "_2"
            parent = _slug(x.get("parent") or "")
            if parent not in by_key or by_key[parent]["tier"] != 1:
                same_chapter = [n for n in tier1 if s["chapter"] in {c for c, _ in coverage.parse_src_refs(n.get("src") or "")}]
                parent = (same_chapter or tier1 or [{"key": None}])[0]["key"]
            if not parent:
                continue
            new_nodes.append({
                "key": key, "name": str(x["name"]).strip()[:120], "tier": 2, "parent": parent,
                "prereqs": [p for p in (_slug(q) for q in (x.get("prereqs") or [])) if p in by_key],
                "order": max([n["order"] for n in path_map["nodes"] if n.get("parent") == parent] or [by_key[parent]["order"]]),
                "summary": str(x.get("summary") or "").strip(), "src": _section_ref(s),
                "kind": "core",
            })
            size_hints[key] = s["chars"] // len(proposed)
            report["added"] += 1
    if not new_nodes:
        return path_map, {}, report
    merged = normalize_map({
        "title": path_map.get("title"), "domain": path_map.get("domain"),
        "nodes": path_map["nodes"] + new_nodes, "edges": path_map["edges"],
    })
    return merged, size_hints, report


async def fill_map_gaps(text: str, path_map: dict, calls_log: list) -> tuple[dict, dict[str, int], dict | None]:
    """Проверка охвата по оглавлению: разделы книги, на которые не ссылается ни один узел, отдаются модели —
    она либо отвечает «уже покрыто», либо добавляет узел. Сбой не фатален: карта остаётся прежней."""
    sections = coverage.extract_sections(text)
    if not sections:
        return path_map, {}, None
    big = [s for s in sections if s["chars"] >= coverage.MIN_SECTION_CHARS]
    missing = coverage.uncovered_sections(sections, path_map["nodes"])
    if not big or not missing:
        return path_map, {}, {"sections": len(big), "covered": 0, "added": 0, "skipped": 0}
    referenced = len(big) - len(missing)
    if referenced < MIN_REFERENCED_SHARE * len(big):
        print(f"[Path Builder] охват: узлы ссылаются только на {referenced} из {len(big)} разделов — формат src не годится, проверка пропущена", flush=True)
        return path_map, {}, None
    missing = sorted(missing, key=lambda s: -s["chars"])[:MAX_GAP_SECTIONS]
    missing.sort(key=lambda s: s["start"])
    compact = [{"key": n["key"], "name": n["name"], "tier": n["tier"], "parent": n.get("parent"), "src": n.get("src")}
               for n in path_map["nodes"]]
    prompt = (build_source_block(text) +
              build_gaps_task(json.dumps(compact, ensure_ascii=False, separators=(",", ":")), _gap_prompt_lines(text, missing)))
    try:
        raw = await _call(prompt, GAPS_MAX_TOKENS, "gaps#1", calls_log)
    except Exception as e:  # noqa: BLE001
        print(f"[Path Builder WARN] GAPS: {e}", flush=True)
        return path_map, {}, {"sections": len(missing), "covered": 0, "added": 0, "skipped": len(missing), "error": str(e)[:120]}
    try:
        return apply_gap_answer(path_map, raw, missing)
    except PathBuildError as e:
        print(f"[Path Builder WARN] GAPS: карта после вставки не прошла проверку: {e}", flush=True)
        return path_map, {}, {"sections": len(missing), "covered": 0, "added": 0, "skipped": len(missing), "error": str(e)[:120]}


def question_opener_stats(cards: list[dict]) -> dict:
    """Насколько однотипны вопросы: доля самого частого начала из двух слов и доля ответов с цифрой.
    Не блокирует нарезку — идёт в отчёт, чтобы видеть, помогла ли правка промпта."""
    if not cards:
        return {"cards": 0, "top_opener": None, "top_opener_share": 0.0, "numeric_answers_share": 0.0}
    openers = Counter(" ".join(re.findall(r"\w+", c["text"].lower())[:2]) for c in cards)
    top, n = openers.most_common(1)[0]
    numeric = sum(1 for c in cards if re.search(r"\d", c["translation"]))
    return {"cards": len(cards), "top_opener": top, "top_opener_share": round(n / len(cards), 3),
            "numeric_answers_share": round(numeric / len(cards), 3)}


def node_sizes_with_hints(index: "coverage.SourceIndex", path_map: dict, size_hints: dict[str, int] | None = None) -> dict[str, int]:
    sizes = coverage.node_source_sizes(index, path_map["nodes"])
    sizes.update({k: v for k, v in (size_hints or {}).items() if k in sizes})
    return sizes


def plan_card_quotas(text: str, path_map: dict, size_hints: dict[str, int] | None = None,
                     total: int | None = None, index: "coverage.SourceIndex | None" = None,
                     sizes: dict[str, int] | None = None, floor_override: dict[str, int] | None = None) -> dict[str, int] | None:
    """Квоты карточек по узлам. total — ЦЕЛЬ по числу карточек колоды; по умолчанию одна на CHARS_PER_CARD знаков источника
    (coverage.target_cards). Деньги цель не меняют. Кому сколько — coverage.allocate_cards (минимум по ярусу, остальное по размеру
    куска книги). Короткий или безбуквенный текст (индекс по окнам неприменим): размеры узлов неизвестны, карточки делятся поровну по минимумам
    ярусов — цель всё равно соблюдается (шпаргалка на две страницы получает свои 30 карточек).
    size_hints — известный объём (узлы, добавленные проверкой охвата). MAX_TOTAL_CARDS и потолок на узел — только при CARD_CAPS."""
    index = index or coverage.SourceIndex(text)
    if sizes is None:
        sizes = node_sizes_with_hints(index, path_map, size_hints) if index.usable else {}
    goal = coverage.target_cards(len(text), CHARS_PER_CARD) if total is None else max(1, total)
    if CARD_CAPS:
        goal = min(goal, MAX_TOTAL_CARDS)
    return coverage.allocate_cards(path_map["nodes"], sizes, goal, floor_override=floor_override,
                                   max_cards=coverage.MAX_CARDS_PER_NODE if CARD_CAPS else None)


def quota_report(path_map: dict, quotas: dict[str, int] | None, sizes: dict[str, int] | None,
                 cards_by_node: dict[str, list[dict]]) -> dict:
    """Диагностика качества карты и нарезки (в журнал): «толстые» узлы — кусок книги больше, чем покрывает потолок квоты,
    значит узел стоило бы разбить; «короткие» узлы — модель дала заметно меньше карточек, чем просили."""
    if not quotas:
        return {}
    names = {n["key"]: n["name"] for n in path_map["nodes"]}
    fat = sorted(((sz, k) for k, sz in (sizes or {}).items() if k in quotas and sz > (coverage.MAX_CARDS_PER_NODE + 0.5) * CHARS_PER_CARD), reverse=True)
    short = [k for k, q in quotas.items() if q >= 4 and len(cards_by_node.get(k) or []) < 0.6 * q]
    return {"asked": sum(quotas.values()), "got": sum(len(v) for v in cards_by_node.values()),
            "fat_nodes": [f"{names[k]} ({sz // 1000} тыс. знаков)" for sz, k in fat[:6]],
            "short_nodes": [names[k] for k in short[:8]]}


# ---------------------------------------------------------------------------
# Общий запуск по списку узлов: повтор недостающих, деление пополам при обрезанном ответе
# ---------------------------------------------------------------------------

async def _run_batched(keys: list[str], make_prompt, normalize, max_tokens: int, label: str, calls_log: list,
                       attempts: int = 3, thinking: bool = False) -> dict:
    """make_prompt(missing_keys) -> текст запроса; normalize(raw, missing_keys) -> {ключ: результат} только для
    узлов, которые действительно получены. Недостающие запрашиваются повторно."""
    from .client import LLMOutputTruncated
    collected: dict = {}
    for attempt in range(1, attempts + 1):
        missing = [k for k in keys if k not in collected]
        if not missing:
            break
        try:
            # Повтор с той же температурой даёт тот же брак (замер: битый JSON у одной темы три раза подряд): с каждой попыткой теплее
            raw = await _call(make_prompt(missing), max_tokens, f"{label}{'*' if thinking else ''}[{','.join(missing)}]#{attempt}",
                              calls_log, temperature=(0.1, 0.5, 0.8)[min(attempt - 1, 2)], thinking=thinking)
            collected.update(normalize(raw, missing))
        except LLMOutputTruncated as e:
            if len(missing) == 1:
                print(f"[Path Builder WARN] {label} {missing}: узел не помещается в лимит ответа, пропуск: {e}", flush=True)
                break
            # Делим батч пополам вместо повтора того же запроса
            half = len(missing) // 2
            for part in (missing[:half], missing[half:]):
                collected.update(await _run_batched(part, make_prompt, normalize, max_tokens, label, calls_log,
                                                    attempts - attempt + 1, thinking))
            break
        except Exception as e:  # noqa: BLE001
            print(f"[Path Builder WARN] {label} {missing} попытка {attempt}/{attempts}: {e}", flush=True)
    return collected


# ---------------------------------------------------------------------------
# CARDS
# ---------------------------------------------------------------------------

async def build_cards(text: str, path_map: dict, node_keys: list[str], calls_log: list, attempts: int = 3,
                      thinking: bool = False, quotas: dict[str, int] | None = None,
                      rejected: list | None = None, source_type: str | None = None,
                      already_asked: dict[str, list[str]] | None = None) -> dict[str, list[dict]]:
    """Карточки узлов с цитатами из книги. Книга идёт префиксом запроса (кэш DeepSeek). Узел без единой годной карточки
    считается не полученным и запрашивается повторно."""
    map_json = json.dumps(path_map, ensure_ascii=False, separators=(",", ":"))

    def make_prompt(missing: list[str]) -> str:
        return build_source_block(text) + build_cards_task(map_json, missing, quotas, source_type, already_asked)

    def normalize(raw, missing):
        return {k: v for k, v in normalize_cards(raw, path_map, missing, quotas, rejected).items() if v}

    return await _run_batched(node_keys, make_prompt, normalize, CORE_PACK_MAX_TOKENS if thinking else PACK_MAX_TOKENS,
                              "cards", calls_log, attempts, thinking)


# ---------------------------------------------------------------------------
# FACTS: конспект темы по блокам книги
# ---------------------------------------------------------------------------

def _anchor(text: str, a: int, b: int) -> tuple[str, str]:
    """Слова начала и конца блока: по ним модель находит блок в книге. Кавычки убираем, чтобы не ломать строку запроса."""
    def words(chunk: str) -> list[str]:
        return re.sub(r"[«»\"]", "", chunk).split()
    head = words(card_quality._clean_cut(text, a, min(b, a + 220)))[:FACT_ANCHOR_WORDS[0]]
    tail = words(card_quality._clean_cut(text, max(a, b - 160), b))[-FACT_ANCHOR_WORDS[1]:]
    return " ".join(head), " ".join(tail)


def plan_fact_blocks(text: str, index: "coverage.SourceIndex", verifier: "card_quality.CardVerifier", weights: list[float] | None,
                     total: int) -> list[dict]:
    """Блоки книги, в которых просим факты: [{id, i, a, b, k, start, end}] по порядку. total фактов раскладывается по блокам
    пропорционально длине и весу (насыщенность × новизна), в оглавлении, литературе и таблицах фактов нет."""
    spans = coverage.fact_blocks(text)
    usable, wts = [], []
    for a, b in spans:
        chunk = text[a:b]
        size = max(1, len(chunk))
        letters = sum(1 for ch in chunk if ch.isalpha())
        digits = sum(1 for ch in chunk if ch.isdigit())
        teaching = letters / size >= 0.6 and digits / size <= 0.06 and chunk.count("....") <= 3 and (b - a) >= 600
        win = verifier.window_of((a + b) // 2) if index.usable else 0
        usable.append(teaching and (not index.usable or verifier.substantive(win)))
        wts.append(weights[win] if (weights and win < len(weights)) else 1.0)
    ks = coverage.block_fact_targets(spans, wts, usable, total)
    out = []
    for i, ((a, b), k) in enumerate(zip(spans, ks)):
        if k > 0:
            start, end = _anchor(text, a, b)
            out.append({"id": f"B{i + 1}", "i": i, "a": a, "b": b, "k": k, "start": start, "end": end})
    return out


def normalize_fact_blocks(raw, wanted: dict[str, int] | set[str], known_numbers: set[str] | None = None) -> dict[str, list[str]]:
    """{id блока: факты} для запрошенных блоков, которые модель вернула (пустой список — тоже ответ: блок без учебного текста).
    wanted — {id: K}; сверх K×FACT_OVER_K+1 фактов блока отбрасываются (они идут в порядке блока, отрезается конец)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("blocks"), list):
        raise PathBuildError("FACTS: ответ без списка blocks")
    out: dict[str, list[str]] = {}
    for item in raw["blocks"]:
        if not isinstance(item, dict):
            continue
        bid = str(item.get("id") or "").strip().upper()
        if bid in wanted and bid not in out:
            facts = normalize_facts(item.get("facts"), known_numbers)
            k = wanted[bid] if isinstance(wanted, dict) else None
            out[bid] = facts[:int(k * FACT_OVER_K) + 1] if k else facts
    if not out:
        raise PathBuildError("FACTS: ни один запрошенный блок не вернулся")
    return out


async def build_facts(text: str, blocks: list[dict], calls_log: list, semaphore: "asyncio.Semaphore | None" = None,
                      attempts: int = 3) -> list[dict]:
    """Конспект темы: [{t, blk, seq}] — факты по блокам в порядке книги. Книга идёт тем же префиксом, что и у карточек (кэш DeepSeek)."""
    if not blocks:
        return []
    by_id = {b["id"]: b for b in blocks}
    known_numbers = set(re.findall(r"\d+", text))
    sem = semaphore or asyncio.Semaphore(PACK_CONCURRENCY)

    def lines(ids: list[str]) -> list[str]:
        return [f'{i} | K={by_id[i]["k"]} | starts: «{by_id[i]["start"]}» | ends: «{by_id[i]["end"]}»' for i in ids]

    async def run(batch: list[dict]):
        ids = [b["id"] for b in batch]

        def make_prompt(missing: list[str]) -> str:
            return build_source_block(text) + build_facts_task(lines(missing))

        def normalize(raw, missing):
            return normalize_fact_blocks(raw, {i: by_id[i]["k"] for i in missing}, known_numbers)

        limit = min(FACTS_MAX_TOKENS, FACT_TOKENS_BASE + FACT_TOKENS_PER_ASKED * sum(b["k"] for b in batch))
        async with sem:
            return await _run_batched(ids, make_prompt, normalize, limit, "facts", calls_log, attempts)

    out: list[dict] = []
    for part in await asyncio.gather(*(run(blocks[i:i + FACT_BATCH_BLOCKS]) for i in range(0, len(blocks), FACT_BATCH_BLOCKS))):
        for bid, facts in part.items():
            out += [{"t": t, "blk": by_id[bid]["i"], "seq": j} for j, t in enumerate(facts)]
    return out


# ---------------------------------------------------------------------------
# AUDIT: подозрительные карточки проверяются по куску книги
# ---------------------------------------------------------------------------

def _audit_items(verifier: "card_quality.CardVerifier", cards: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(cards, 1):
        passage = verifier.passage(c) or "(not found)"
        blocks.append(f"C{i} | node: {c.get('theme', '')}\nQ: {c['text']}\nA: {c['translation']}\n"
                      f"QUOTE: {c.get('evidence') or '-'}\nPASSAGE: «{passage}»")
    return "\n\n".join(blocks)


def apply_audit(raw: dict, cards: list[dict], verifier: "card_quality.CardVerifier",
                cards_by_node: dict[str, list[dict]]) -> dict[str, int]:
    """Применяет вердикты проверки: ok — подтверждена, fix — исправлена, drop — удалена, skip или нет ответа — как была."""
    answers = {str(a.get("id")): a for a in ((raw or {}).get("audit") or []) if isinstance(a, dict)}
    report: Counter = Counter()
    dropped: set[int] = set()
    for i, c in enumerate(cards, 1):
        a = answers.get(f"C{i}")
        verdict = str((a or {}).get("v") or "").strip().lower()
        ev = re.sub(r"\s+", " ", str((a or {}).get("ev") or "")).strip()[:240]
        if verdict == "ok":
            if ev:
                c["evidence"] = ev
            c["support"] = "audited"
            report["ok"] += 1
        elif verdict == "fix" and str(a.get("d") or "").strip():
            c["translation"] = str(a["d"]).strip()
            new_q = str(a.get("t") or "").strip()
            if new_q:
                c["text"] = new_q
            c["distractors"] = _clean_distractors(c["translation"], a.get("x"))
            c["secondary_text"] = strip_secondary_spoilers(c["text"], c["translation"], c.get("secondary_text") or "")
            if ev:
                c["evidence"] = ev
            c["support"] = "fixed"
            report["fixed"] += 1
        elif verdict == "drop":
            dropped.add(id(c))
            report["dropped"] += 1
            continue
        else:
            report["skipped" if verdict == "skip" else "unanswered"] += 1
            continue
        span = verifier.locator.find(c.get("evidence") or "")
        if span:
            c["src_span"] = [span[0], span[1]]
            c["src_window"] = verifier.window_of(span[0])
    if dropped:
        for key, lst in cards_by_node.items():
            cards_by_node[key] = [c for c in lst if id(c) not in dropped]
    return dict(report)


async def audit_cards(verifier: "card_quality.CardVerifier", cards_by_node: dict[str, list[dict]], flagged: list[dict],
                      calls_log: list, budget: Budget, must_keep: float) -> dict:
    """Одна проверка подозрительных карточек (не больше AUDIT_MAX_CARDS, сколько позволяет бюджет). Сбой не фатален."""
    if not flagged:
        return {"checked": 0}
    # Сначала карточки с найденной цитатой, но расходящимся ответом (вероятна ошибка в числе или слове), потом остальные
    ordered = sorted(flagged, key=lambda c: 0 if c.get("src_span") else 1)
    n = budget.affordable("проверка карточек", budget.audit_cost(1), must_keep,
                          min(len(ordered), AUDIT_MAX_CARDS) if CARD_CAPS else len(ordered))
    if n <= 0:
        return {"checked": 0, "budget_skipped": len(flagged)}
    ordered = ordered[:n]
    chunks = [ordered[i:i + AUDIT_BATCH] for i in range(0, len(ordered), AUDIT_BATCH)]

    async def one(chunk: list[dict], idx: int) -> dict:
        try:
            raw = await _call(build_audit_task(_audit_items(verifier, chunk)), AUDIT_MAX_TOKENS, f"audit#{idx}", calls_log)
            return apply_audit(raw, chunk, verifier, cards_by_node)
        except Exception as e:  # noqa: BLE001 — карточки остаются как есть
            print(f"[Path Builder WARN] AUDIT#{idx}: {e}", flush=True)
            return {"error": 1}

    total: Counter = Counter()
    for part in await asyncio.gather(*(one(ch, i) for i, ch in enumerate(chunks, 1))):
        total.update(part)
    return {"checked": len(ordered), **dict(total)}


# ---------------------------------------------------------------------------
# FILL: участки книги без карточек
# ---------------------------------------------------------------------------

def _fill_job_blocks(jobs: list[dict], path_map: dict, cards_by_node: dict[str, list[dict]]) -> tuple[str, str]:
    """Список ВСЕХ узлов курса (чтобы модель выбрала подходящий, а не только «предложенный» по совпадению слов) и сами участки."""
    node_lines = [f"{n['key']} | {n['name']} | {(n.get('summary') or '-')[:110]}" for n in path_map["nodes"] if n["tier"] < 3]
    passages: list[str] = []
    for j in jobs:
        asked = "; ".join(c["text"][:90] for c in (cards_by_node.get(j["node"]) or [])[:14]) or "-"
        passages.append(f"{j['id']} | suggested node: {j['node']} | target: {j['target']} cards\nALREADY ASKED: {asked}\nPASSAGE: «{j['text']}»")
    return "\n".join(node_lines), "\n\n".join(passages)


def spread_pick(items: list, n: int) -> list:
    """n элементов, равномерно рассыпанных по списку (список идёт в порядке книги): иначе при нехватке денег добор достался бы
    только началу книги."""
    if n >= len(items):
        return list(items)
    if n <= 0:
        return []
    if n == 1:
        return [items[len(items) // 2]]
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


async def fill_cards(verifier: "card_quality.CardVerifier", path_map: dict, cards_by_node: dict[str, list[dict]],
                     calls_log: list, budget: Budget, must_keep: float, rejected: list | None = None,
                     max_cards: int | None = None) -> dict:
    """Добор карточек на абзацах книги, которых не коснулась ни одна карточка (по точным местам цитат). Передаётся только сам
    кусок (не вся книга). Новые карточки проходят ту же проверку по книге; не прошедшие отбрасываются. Сбой не фатален.
    max_cards — сколько карточек не хватает до цели: участков берём не больше, чем нужно, и сверх цели не добавляем."""
    all_cards = [c for lst in cards_by_node.values() for c in lst]
    spans = verifier.uncovered_passages(all_cards)                  # абзацы, которых не коснулась ни одна карточка
    if CARD_CAPS:
        spans = sorted(spans, key=lambda sp: -sp["chars"])[:MAX_FILL_SPANS]
    if not spans:
        return {"spans": 0, "added": 0}
    wanted_spans = len(spans) if max_cards is None else min(len(spans), -(-max_cards // FILL_MIN_CARDS))
    n = budget.affordable("добор участков", budget.fill_cost(1), must_keep, wanted_spans)
    if n <= 0:
        return {"spans": 0, "added": 0, "skipped": len(spans)}
    spans = spread_pick(spans, n)
    by_key = {nd["key"]: nd for nd in path_map["nodes"]}
    domain = path_map.get("domain") or "generic"
    jobs = []
    for i, sp in enumerate(spans, 1):
        passage = re.sub(r"\s+", " ", coverage.join_wrapped(verifier.text[sp["start"]:sp["end"]])).strip()
        key = verifier.suggest_node(path_map["nodes"], passage)
        if key not in by_key:
            continue
        target = max(FILL_MIN_CARDS, round(sp["chars"] / CHARS_PER_CARD))
        if CARD_CAPS:
            target = min(target, FILL_MAX_CARDS)
        jobs.append({"id": f"P{i}", "span": sp, "node": key, "target": target, "text": passage})
    if not jobs:
        return {"spans": 0, "added": 0}
    existing = {_norm_text(c["text"]) for c in all_cards}
    report: Counter = Counter({"spans": len(jobs)})

    async def one(batch: list[dict], idx: int):
        nodes_text, passages_text = _fill_job_blocks(batch, path_map, cards_by_node)
        try:
            raw = await _call(build_fill_task(nodes_text, passages_text), FILL_MAX_TOKENS, f"fill#{idx}", calls_log)
        except Exception as e:  # noqa: BLE001
            print(f"[Path Builder WARN] FILL#{idx}: {e}", flush=True)
            return
        by_id = {j["id"]: j for j in batch}
        for item in (raw or {}).get("fill") or []:
            job = by_id.get(str((item or {}).get("id"))) if isinstance(item, dict) else None
            if not job:
                continue
            key = _slug(item.get("node") or "")
            if key not in by_key or by_key[key]["tier"] >= 3:
                key = job["node"]
            for rc in (item.get("cards") or [])[:job["target"] + 1]:
                if max_cards is not None and report["added"] >= max_cards:      # цель достигнута: больше не добавляем
                    break
                report["asked"] += 1
                card = _normalize_card(rc, by_key[key], domain, rejected, (path_map.get("title") or "").strip())
                front = _norm_text(card["text"]) if card else ""
                if not card or front in existing:
                    report["rejected"] += 1
                    continue
                res = verifier.check(card)
                inside = res.get("span") and job["span"]["start"] <= res["span"][0] < job["span"]["end"]
                if res["status"] == "flagged" or (res.get("span") and not inside):
                    report["rejected"] += 1
                    continue
                card["support"] = res["status"]
                if "span" in res:
                    card["src_span"] = [res["span"][0], res["span"][1]]
                    card["src_window"] = verifier.window_of(res["span"][0])
                elif "window" in res:
                    card["src_window"] = res["window"]
                existing.add(front)
                cards_by_node.setdefault(key, []).append(card)
                report["added"] += 1

    batches = [jobs[i:i + FILL_BATCH_SPANS] for i in range(0, len(jobs), FILL_BATCH_SPANS)]
    sem = asyncio.Semaphore(FILL_CONCURRENCY)

    async def guarded(b, i):
        async with sem:
            await one(b, i)

    await asyncio.gather(*(guarded(b, i) for i, b in enumerate(batches, 1)))
    return dict(report)


# ---------------------------------------------------------------------------
# LESSON: урок узла пишется из его карточек и выдержки из книги (книги в запросе нет)
# ---------------------------------------------------------------------------

def _related_lines(key: str, by_key: dict, edges: list[dict]) -> str:
    rank = {"demarcated_from": 0, "excludes_application": 0}
    mine = sorted((e for e in edges if key in (e["from"], e["to"])), key=lambda e: rank.get(e["relation"], 1))[:4]
    out = []
    for e in mine:
        a, b = by_key.get(e["from"]), by_key.get(e["to"])
        if a and b:
            out.append(f"{a['name']} {e.get('label') or e['relation']} {b['name']}")
    return "; ".join(out)


def _lesson_block(node: dict, cards: list[dict], path_map: dict, by_key: dict, verifier: "card_quality.CardVerifier",
                  omitted: list[str] | None = None, supplement: bool = False) -> str:
    keys = [node["key"]] + list(node.get("prereqs") or [])
    if node.get("parent"):
        keys.append(node["parent"])
        keys += [m["key"] for m in path_map["nodes"] if m["parent"] == node["parent"] and m["key"] != node["key"] and m["tier"] < 3][:3]
    for e in path_map["edges"]:
        if node["key"] in (e["from"], e["to"]):
            keys += [e["from"], e["to"]]
    keys = [k for k in dict.fromkeys(keys) if k in by_key][:12]
    prereqs = "; ".join(by_key[p]["name"] for p in node.get("prereqs") or [] if p in by_key) or "-"
    size = max(LESSON_EXCERPT_MIN, min(LESSON_EXCERPT_MAX, LESSON_EXCERPT_PER_CARD * len(cards)))
    excerpt = verifier.excerpt(cards, size) or "(none)"
    lines = [
        f"### {node['key']} | {node['name']} | tier {node['tier']}",
        f"SUMMARY: {node.get('summary') or '-'}",
        f"PREREQS: {prereqs}",
        f"RELATED: {_related_lines(node['key'], by_key, path_map['edges']) or '-'}",
        "KEYS: " + "; ".join(f"{k}={by_key[k]['name']}" for k in keys),
        "CARDS:",
    ]
    lines += [f"{i}. Q: {c['text']} | A: {c['translation']}" for i, c in enumerate(cards, 1)]
    lines.append(f"EXCERPT: «{excerpt}»")
    if supplement:
        lines.append("SUPPLEMENT: the learner already finished the lesson of this topic from another material; these cards are what THIS material adds.")
    if omitted:
        lines.append("THE PREVIOUS LESSON OMITTED THESE ANSWERS: " + " ; ".join(omitted))
    return "\n".join(lines)


async def build_lessons(verifier: "card_quality.CardVerifier", path_map: dict, cards_by_node: dict[str, list[dict]],
                        calls_log: list, keys: list[str] | None = None, omitted: dict[str, list[str]] | None = None,
                        attempts: int = 3, source_type: str | None = None, supplements: set[str] | None = None) -> dict[str, dict]:
    """Уроки узлов: по LESSON_BATCH_NODES узлов в запросе, запросы идут одновременно. Узел без карточек урока не получает."""
    by_key = {n["key"]: n for n in path_map["nodes"]}
    order = [n["key"] for n in path_map["nodes"] if (keys is None or n["key"] in keys) and cards_by_node.get(n["key"])]
    fronts = {k: {_norm_text(c["text"]) for c in cards_by_node[k]} for k in order}
    chunks = [order[i:i + LESSON_BATCH_NODES] for i in range(0, len(order), LESSON_BATCH_NODES)]
    sem = asyncio.Semaphore(LESSON_CONCURRENCY)

    def make_prompt(missing: list[str]) -> str:
        blocks = [_lesson_block(by_key[k], cards_by_node[k], path_map, by_key, verifier, (omitted or {}).get(k), k in (supplements or set()))
                  for k in missing]
        return build_lessons_task("\n\n".join(blocks), source_type)

    def normalize(raw, missing):
        return normalize_lessons(raw, path_map, missing, fronts)

    async def run(chunk: list[str]):
        async with sem:
            return await _run_batched(chunk, make_prompt, normalize, LESSON_MAX_TOKENS, "lesson", calls_log, attempts)

    lessons: dict[str, dict] = {}
    for part in await asyncio.gather(*(run(c) for c in chunks)):
        lessons.update(part)
    return lessons


def lessons_needing_repair(cards_by_node: dict[str, list[dict]], lessons: dict[str, dict]) -> dict[str, list[str]]:
    """Узлы, где в уроке не названы ответы карточек: {ключ: [пропущенные ответы]}."""
    out: dict[str, list[str]] = {}
    for key, lesson in lessons.items():
        cards = cards_by_node.get(key) or []
        have = set(coverage.stems(coverage.lesson_text(lesson)))
        miss = [c["translation"] for c in cards if coverage.card_in_lesson(c, have) < LESSON_OMITTED_BELOW]
        if miss and (len(miss) >= LESSON_REPAIR_MIN_OMITTED or len(miss) / len(cards) >= LESSON_REPAIR_SHARE):
            out[key] = miss
    return out


async def repair_lessons(verifier: "card_quality.CardVerifier", path_map: dict, cards_by_node: dict[str, list[dict]],
                         lessons: dict[str, dict], calls_log: list, budget: Budget) -> dict:
    """Один повторный запрос для уроков, в которых не хватает ответов карточек; берём новый урок, только если он лучше."""
    todo = lessons_needing_repair(cards_by_node, lessons)
    if not todo:
        return {"nodes": 0}
    unit = budget.cost(miss=LESSON_IN_REPAIR_TOKENS, out=LESSON_OUT_REPAIR_TOKENS)
    n = budget.affordable("правка уроков", unit, 0.0, len(todo))
    if n <= 0:
        return {"nodes": 0, "skipped": len(todo)}
    keys = sorted(todo, key=lambda k: -len(todo[k]))[:n]
    new = await build_lessons(verifier, path_map, cards_by_node, calls_log, keys=keys,
                              omitted={k: todo[k] for k in keys}, attempts=1)
    better = 0
    for key, lesson in new.items():
        before = len(todo[key])
        after = len(lessons_needing_repair({key: cards_by_node[key]}, {key: lesson}).get(key, []))
        if after < before:
            lessons[key] = lesson
            better += 1
    return {"nodes": len(keys), "improved": better}


# ---------------------------------------------------------------------------
# LINKS, INTRO
# ---------------------------------------------------------------------------

async def build_cross_links(text: str, path_map: dict, calls_log: list) -> list[dict]:
    """Связи между основами и темами разных веток. Сбой не фатален: путь остаётся без дополнительных связей."""
    core = {n["key"]: n for n in path_map["nodes"] if n["tier"] <= 1}
    if len(core) < 3:
        return []
    map_json = json.dumps(path_map, ensure_ascii=False, separators=(",", ":"))
    prompt = build_source_block(text) + build_links_task(map_json)
    try:
        raw = await _call(prompt, LINKS_MAX_TOKENS, "links#1", calls_log)
    except Exception as e:  # noqa: BLE001
        print(f"[Path Builder WARN] LINKS: {e}", flush=True)
        return []
    parent_pairs = {frozenset((n["key"], n["parent"])) for n in path_map["nodes"] if n["parent"]}
    links = normalize_edges((raw or {}).get("edges") or [], set(core), existing=path_map["edges"])
    return [e for e in links if frozenset((e["from"], e["to"])) not in parent_pairs][:35]


INTRO_MAX_TOKENS = 6000
INTRO_KEY = "__intro__"


def _intro_facts(nodes: list[dict]) -> str:
    n = [sum(1 for x in nodes if x["tier"] == t) for t in range(4)]
    return f"{n[0]} foundations (tier 0), {n[1]} topics (tier 1), {n[2]} subtopics (tier 2), {n[3]} case nodes (tier 3)"


async def build_intro_lesson(path_map: dict, calls_log: list) -> dict | None:
    """Вводный урок курса по одной карте (без книги). Сбой не фатален: курс просто останется без вводного."""
    nodes = path_map.get("nodes") or []
    if len(nodes) < 3:
        return None
    compact = {"title": path_map.get("title"), "nodes": [
        {"key": n["key"], "name": n["name"], "tier": n["tier"], "parent": n.get("parent"),
         "prereqs": n.get("prereqs") or [], "order": n.get("order"), "summary": n.get("summary")}
        for n in nodes
    ]}
    prompt = build_intro_task(json.dumps(compact, ensure_ascii=False, separators=(",", ":")), _intro_facts(nodes))
    for attempt in (1, 2):
        try:
            raw = await _call(prompt, INTRO_MAX_TOKENS, f"intro#{attempt}", calls_log, 0.1 if attempt == 1 else RETRY_TEMPERATURE)
        except Exception as e:  # noqa: BLE001
            print(f"[Path Builder WARN] INTRO: {e}", flush=True)
            continue
        lesson = _normalize_lesson((raw or {}).get("lesson"), {n["key"] for n in nodes}, max_screens=8)
        if lesson:
            lesson["check"] = []
            return lesson
    return None


# ---------------------------------------------------------------------------
# Полный прогон
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ALIGN: темы нового материала против тем курса, который уже есть
# ---------------------------------------------------------------------------

ALIGN_MAX_TOKENS = 8000
ALIGN_CANDIDATES = 3
ALIGN_MIN_SCORE = 0.12


def node_candidates(new_nodes: list[dict], existing_nodes: list[dict], k: int = ALIGN_CANDIDATES,
                    min_score: float = ALIGN_MIN_SCORE) -> dict[str, list[str]]:
    """Для каждого нового узла — до k похожих узлов курса по названию и описанию (TF-IDF по стеммам, без вызовов ИИ).
    Модель выбирает только из них, поэтому запрос короткий и ошибиться «слишком далеко» ей нечем."""
    def toks(n: dict) -> list[str]:
        return coverage.stems(f"{n['name']} {n['name']} {n.get('summary') or ''}")

    docs = [toks(n) for n in existing_nodes]
    df = Counter(w for d in docs for w in set(d))
    total = max(1, len(docs))
    idf = {w: math.log(total / (1 + c)) + 1.0 for w, c in df.items()}

    def vec(tokens: list[str]) -> dict[str, float]:
        tf = Counter(tokens)
        v = {w: (1 + math.log(c)) * idf.get(w, 1.0) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    ex_vecs = [vec(d) for d in docs]
    out: dict[str, list[str]] = {}
    for n in new_nodes:
        q = vec(toks(n))
        scored = sorted(((sum(x * ev.get(w, 0.0) for w, x in q.items()), existing_nodes[i]["key"]) for i, ev in enumerate(ex_vecs)), reverse=True)
        out[n["key"]] = [key for s_, key in scored[:k] if s_ >= min_score]
    return out


def normalize_align(raw: dict, candidates: dict[str, list[str]], new_by_key: dict, existing_by_key: dict) -> dict[str, str]:
    """{ключ нового узла: ключ узла курса}: только из предложенных кандидатов, не больше одного нового узла на существующий,
    ярусы не дальше чем на один, кейсы не сливаем."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for item in (raw or {}).get("align") or []:
        if not isinstance(item, dict):
            continue
        nk, ek = _slug(item.get("new") or ""), _slug(item.get("same") or "")
        if not ek or nk not in candidates or ek not in candidates[nk] or ek in used or nk in mapping:
            continue
        nn, en = new_by_key[nk], existing_by_key[ek]
        if nn["tier"] >= 3 or en.get("tier", 0) >= 3 or abs(nn["tier"] - en.get("tier", 0)) > 1:
            continue
        mapping[nk] = ek
        used.add(ek)
    return mapping


def orphan_foundations(path_map: dict, merge: dict[str, str], existing_nodes: list[dict]) -> dict[str, str]:
    """Основы (ярус 0) нового материала, у которых не осталось собственных тем: все темы, опирающиеся на основу, слиты с имеющимися.
    Такая основа повисла бы в графе одиночной точкой. Возвращает {ключ новой основы: ключ основы курса}: карточки основы лягут в основу
    курса, на которую опираются те самые имеющиеся темы (программа, без вызовов ИИ). Нет такой основы — основа остаётся как есть."""
    existing = {n["key"]: n for n in existing_nodes}

    def foundations_of(key: str, depth: int = 0) -> list[str]:
        """Основы курса над узлом: поднимаемся по пререквизитам и родителю (не выше трёх шагов)."""
        node = existing.get(key)
        if not node or depth > 3:
            return []
        if node.get("tier", 0) == 0:
            return [key]
        out: list[str] = []
        for up in [*(node.get("prereqs") or []), node.get("parent")]:
            if up:
                out += foundations_of(up, depth + 1)
        return out

    result: dict[str, str] = {}
    for base in path_map["nodes"]:
        if base["tier"] != 0 or base["key"] in merge:
            continue
        kids = [n for n in path_map["nodes"] if n["tier"] > 0 and (base["key"] in (n.get("prereqs") or []) or n.get("parent") == base["key"])]
        if not kids or not all(k["key"] in merge for k in kids):
            continue
        votes = Counter(f for k in kids for f in set(foundations_of(merge[k["key"]])))
        if votes:
            result[base["key"]] = votes.most_common(1)[0][0]
    return result


async def build_align(path_map: dict, course: dict, calls_log: list) -> tuple[dict[str, str], dict]:
    """Какие темы нового материала — те же, что уже есть в курсе. Сбой не фатален: без слияния новые темы просто станут отдельными."""
    new_nodes = [n for n in path_map["nodes"] if n["tier"] < 3]
    existing = [n for n in (course.get("nodes") or []) if n.get("tier", 0) < 3]
    if not new_nodes or not existing:
        return {}, {"asked": 0}
    cands = node_candidates(new_nodes, existing)
    todo = {k: v for k, v in cands.items() if v}
    if not todo:
        return {}, {"asked": 0}
    new_by_key = {n["key"]: n for n in new_nodes}
    existing_by_key = {n["key"]: n for n in existing}
    lines = []
    for nk, ek_list in todo.items():
        n = new_by_key[nk]
        cand_txt = "; ".join(f'{ek} "{existing_by_key[ek]["name"]}" (tier {existing_by_key[ek].get("tier", 0)}) '
                             f'{(existing_by_key[ek].get("summary") or "")[:90]}' for ek in ek_list)
        lines.append(f'{nk} | "{n["name"]}" (tier {n["tier"]}) {(n.get("summary") or "")[:100]} -> candidates: {cand_txt}')
    try:
        raw = await _call(build_align_task("\n".join(lines)), ALIGN_MAX_TOKENS, "align#1", calls_log)
    except Exception as e:  # noqa: BLE001
        print(f"[Path Builder WARN] ALIGN: {e}", flush=True)
        return {}, {"asked": len(todo), "error": str(e)[:100]}
    mapping = normalize_align(raw, todo, new_by_key, existing_by_key)
    return mapping, {"asked": len(todo), "merged": len(mapping)}


# ---------------------------------------------------------------------------
# Полный прогон
# ---------------------------------------------------------------------------

async def build_learning_path(text: str, subject: str, calls: list | None = None, card_total: int | None = None,
                              course: dict | None = None, depth: str | None = None, source_kind: str | None = None) -> dict:
    """Полный прогон: MAP → тип материала и цель → (ALIGN с имеющимся курсом) → CARDS → проверка по книге (AUDIT) → добор до цели (FILL)
    → LESSON из карточек. Возвращает карту, пакеты узлов {урок, карточки} и телеметрию.
    Цель по карточкам зависит от типа материала (source_profile): учебник ≈ одна на страницу, конспект ≈ по карточке на пункт.
    card_total задаёт другое число (scripts/run_book.py --cards). depth — «насколько подробно» (compact | standard | detailed),
    source_kind — тип материала, названный пользователем (иначе его называет модель или определяет код).
    Размер колоды задаёт source_profile.deck_goal: содержание материала, глубина и общий потолок карточек предмета (несколько источников
    не складываются в тысячи карточек); деньги размер не меняют.
    course — курс предмета, если материал добавляется к уже имеющемуся: {"nodes": [{key, name, tier, summary}], "cards": {key: [{"q", "a"}]}}.
    Тогда темы, совпавшие с имеющимися, сливаются (result["merge"]), уже покрытые куски получают меньше карточек, а по совпавшим
    темам модель видит, что уже спрошено, и пишет только новое."""
    text = (text or "").strip()
    if not text:
        raise PathBuildError("Пустой источник")
    if len(text) > MAX_SOURCE_CHARS:
        raise PathBuildError(f"Источник слишком большой ({len(text)} знаков, максимум {MAX_SOURCE_CHARS})")

    # Словарь (явный выбор пользователя или очевидный словарь без выбора): статьи разбирает программа, ИИ не вызывается, расход нулевой
    if source_kind == "glossary" or (not source_kind and glossary.looks_like_glossary(text)):
        try:
            return await asyncio.to_thread(glossary.build_glossary_path, text, subject, course, depth, card_total)
        except glossary.GlossaryError as e:
            raise PathBuildError(str(e)) from e

    # Список вызовов передаётся снаружи, чтобы расходы учитывались и при сбое
    calls = calls if calls is not None else []
    budget = Budget(calls)
    path_map = await build_knowledge_map(text, subject, calls, candidates=MAP_CANDIDATES, source_kind=source_kind)
    path_map, size_hints, gap_report = await fill_map_gaps(text, path_map, calls)

    kind = source_profile.resolve_kind(source_kind or path_map.get("source_type"), text)
    depth = source_profile.normalize_depth(depth)
    index = coverage.SourceIndex(text)
    verifier = card_quality.CardVerifier(text, index)

    # Что в курсе уже есть: какие куски нового текста покрыты имеющимися карточками и какие темы совпали
    course = course or {}
    existing_cards = course.get("cards") or {}
    card_texts = [f"{c['q']} {c['a']}" for qs in existing_cards.values() for c in qs]
    novelty = coverage.window_novelty(index, card_texts) if (index.usable and card_texts) else None
    saturation = coverage.window_saturation(index) if index.usable else None
    weights = None
    if saturation:
        weights = [s_ * (coverage.COVERED_FLOOR + (1 - coverage.COVERED_FLOOR) * (novelty[i] if novelty else 1.0)) for i, s_ in enumerate(saturation)]
    merge, align_report = (await build_align(path_map, course, calls)) if course.get("nodes") else ({}, {})
    # Основа нового материала, все темы которой влились в имеющиеся, иначе осталась бы одинокой точкой без ветки: сливаем её с основой курса
    orphans = orphan_foundations(path_map, merge, course.get("nodes") or [])
    merge.update(orphans)
    if orphans:
        align_report = {**align_report, "orphan_foundations_merged": len(orphans)}

    node_sizes = coverage.node_source_sizes(index, path_map["nodes"], weights) if index.usable else None
    if node_sizes:
        node_sizes.update({k: v for k, v in size_hints.items() if k in node_sizes})
    # Короткий текст: окон слишком мало для оценки кусков, но цель по типу материала остаётся (карточки по узлам — поровну)
    core_keys = {n["key"] for n in path_map["nodes"] if n["tier"] <= 1}
    batches = plan_pack_batches(path_map, split_core=THINK_CORE)
    effective = coverage.effective_chars(index, novelty) if novelty else len(text)
    planned = card_total or max(1, round(source_profile.target_cards(kind, text) * effective / max(1, len(text))))
    existing_total = sum(len(v) for v in existing_cards.values())
    new_share = effective / max(1, len(text))
    if card_total is None:
        policy = source_profile.deck_goal(planned, depth, existing_total, new_share)
    else:
        policy = {"goal": card_total, "planned": card_total, "mult": 1.0, "source_cap": None, "room": None, "limited_by": "explicit"}
    goal = policy["goal"]
    use_facts = bool(FACTS and len(text) >= MIN_FACT_SOURCE_CHARS)
    fact_ratio = source_profile.facts_per_card(kind) if use_facts else 0.0
    fact_blocks_est = len(coverage.fact_blocks(text)) if use_facts else 0
    fact_calls = -(-fact_blocks_est // FACT_BATCH_BLOCKS)
    fits = budget.card_cap(len(text), len(path_map["nodes"]), len(batches), facts=fact_ratio, fact_blocks=fact_blocks_est, fact_calls=fact_calls)
    print(f"[Path Builder] размер колоды: по содержанию {planned}, глубина «{depth}», в курсе уже {existing_total} карточек; цель {goal} "
          f"({'ограничение: ' + policy['limited_by'] if policy['limited_by'] else 'без ограничений'})", flush=True)
    quotas = plan_card_quotas(text, path_map, size_hints, total=goal, index=index, sizes=node_sizes,
                              floor_override={k: 1 for k in merge})
    target = sum(quotas.values()) if quotas else None                                    # цель по числу карточек (не зависит от денег)
    fact_blocks = plan_fact_blocks(text, index, verifier, weights, round(fact_ratio * (target or goal))) if (use_facts and fact_ratio) else []
    asked_by_new = {nk: [c["q"] for c in existing_cards.get(ek, [])] for nk, ek in merge.items() if existing_cards.get(ek)}
    projected = budget.projected_cost(len(text), len(path_map["nodes"]), len(batches), target or 0, facts=fact_ratio,
                                      fact_blocks=len(fact_blocks), fact_calls=-(-len(fact_blocks) // FACT_BATCH_BLOCKS))
    print(f"[Path Builder] тип материала: {kind} (модель назвала: {path_map.get('source_type')}); цель: {target if target else '—'} карточек; "
          f"покрыто курсом {0 if not novelty else sum(1 for x in novelty if x < 0.5)} из {len(novelty or [])} окон; слито тем: {len(merge)}; "
          f"потрачено ${budget.spent():.4f}, ожидаемо всего ~${projected:.3f} (аварийный потолок ${budget.limit:.2f})"
          f"{'' if not target or target <= fits else f' — ВНИМАНИЕ: дороже аварийного потолка (в него помещается ~{fits})'}; "
          f"прочие потолки числа карточек {'ВКЛ' if CARD_CAPS else 'выкл'}", flush=True)

    semaphore = asyncio.Semaphore(PACK_CONCURRENCY)

    rejected: list[tuple[str, str]] = []                       # карточки, отброшенные при разборе ответа: (причина, вопрос)

    async def run(batch: list[str]):
        async with semaphore:
            return await build_cards(text, path_map, batch, calls, thinking=THINK_CORE and all(k in core_keys for k in batch),
                                     quotas=quotas, rejected=rejected,
                                     source_type=kind, already_asked=asked_by_new)

    # Карточки идут первыми в очереди семафора, конспект — следом и дожидается позже (перед process_facts): проверка и добор карточек
    # не ждут хвоста конспекта. Вводный урок у курса один: у курса, где он уже есть, повторно (и зря) его не пишем.
    card_tasks = [asyncio.create_task(run(b)) for b in batches]
    facts_task = asyncio.create_task(build_facts(text, fact_blocks, calls, semaphore))
    results = await asyncio.gather(
        build_cross_links(text, path_map, calls),
        asyncio.sleep(0) if course.get("has_intro") else build_intro_lesson(path_map, calls),
        *card_tasks,
    )
    path_map["edges"].extend(results[0])
    intro = results[1]
    cards_by_node: dict[str, list[dict]] = {}
    for part in results[2:]:
        cards_by_node.update(part)

    # Узлы, по которым ответа так и не пришло (сеть, битый JSON три раза подряд), иначе остались бы пустыми в готовом курсе
    absent = [n["key"] for n in path_map["nodes"] if n["key"] not in cards_by_node and n["key"] not in merge]
    if absent:
        print(f"[Path Builder WARN] без карточек остались {len(absent)} узлов, повторный запрос", flush=True)
        for part in await asyncio.gather(*(run(absent[i:i + 10]) for i in range(0, len(absent), 10))):
            cards_by_node.update(part)

    # Совпавшая тема: не повторяем то, что в курсе уже спрошено дословно
    dropped_existing = 0
    for nk, ek in merge.items():
        have_fronts = {_norm_text(c["q"]) for c in existing_cards.get(ek, [])}
        if nk in cards_by_node:
            kept = [c for c in cards_by_node[nk] if _norm_text(c["text"]) not in have_fronts]
            dropped_existing += len(cards_by_node[nk]) - len(kept)
            cards_by_node[nk] = kept

    # Качество карточек: повторы, проверка по книге, добор — до уроков, потому что урок строится из окончательного набора
    order = [n["key"] for n in path_map["nodes"]]
    dedupe_removed = card_quality.dedupe_cards(cards_by_node, order)
    # Повторы того, что другой материал курса уже спрашивает (иначе словарь и учебник дают две карточки на один факт)
    dedupe_removed += card_quality.dedupe_against(cards_by_node, [(c["q"], c["a"]) for qs in existing_cards.values() for c in qs])
    all_cards = [c for k in order for c in cards_by_node.get(k, [])]
    support, flagged = verifier.verify(all_cards)

    def reserve() -> float:
        return budget.lessons_cost(sum(1 for k, v in cards_by_node.items() if v and k not in merge),
                                   sum(len(v) for k, v in cards_by_node.items() if k not in merge))

    audit_report = await audit_cards(verifier, cards_by_node, flagged, calls, budget, reserve())
    # Непроверенное ученику не показываем; но если сама проверка не состоялась (сбой, не хватило денег), карточки остаются как есть
    dropped_unsupported = (card_quality.drop_unsupported(cards_by_node)
                           if QUALITY_GATE and not audit_report.get("error") and not audit_report.get("budget_skipped") else 0)
    # Добор — только чтобы дотянуть до цели, если модель дала меньше квот (не раздувает колоду сверх неё); берёт абзацы без карточек
    have = sum(len(v) for v in cards_by_node.values())
    deficit = (target - have) if target else None
    fill_report = ({"spans": 0, "added": 0, "skipped": "цель достигнута"} if deficit is not None and deficit < FILL_MIN_CARDS
                   else await fill_cards(verifier, path_map, cards_by_node, calls, budget, reserve(), rejected, max_cards=deficit))
    dedupe_removed += card_quality.dedupe_cards(cards_by_node, order)       # добранные карточки могут повторять уже имеющиеся
    trimmed = card_quality.trim_to_goal(cards_by_node, target, weights) if (QUALITY_GATE and target) else 0    # лишнее сверх цели — наименее важное

    # Конспект темы: проверка по книге и порядок книги (до уроков: сами уроки факты не пересказывают)
    raw_facts = await facts_task
    matcher = coverage.NodeMatcher(index, path_map["nodes"], cards_by_node) if raw_facts else None
    facts_by_node, facts_report = card_quality.process_facts(verifier, raw_facts, coverage.fact_blocks(text),
                                                             matcher, cards_by_node, order)
    # У совпавшей с курсом темы основной урок уже есть; если новый материал добавил ей не меньше SUPPLEMENT_MIN_CARDS карточек,
    # они получают короткий урок-дополнение (в базе это отдельная подтема «… : дополнение»)
    supplements = {k for k in merge if len(cards_by_node.get(k) or []) >= source_profile.SUPPLEMENT_MIN_CARDS}
    lesson_keys = [k for k in order if cards_by_node.get(k) and (k not in merge or k in supplements)]
    lessons = await build_lessons(verifier, path_map, cards_by_node, calls, keys=lesson_keys, source_type=kind, supplements=supplements)
    repair_report = await repair_lessons(verifier, path_map, cards_by_node, lessons, calls, budget)

    packs = {k: {"lesson": lessons.get(k), "cards": cards_by_node[k]} for k in order if cards_by_node.get(k)}
    final_cards = [c for p in packs.values() for c in p["cards"]]
    support_final = Counter(c.get("support") for c in final_cards)

    return {
        "map": path_map,
        "packs": packs,
        "intro": intro,
        "merge": merge,
        "supplements": sorted(k for k in supplements if lessons.get(k)),
        "facts": facts_by_node,
        "source_type": kind,
        "quotas": quotas,
        "gap_report": gap_report,
        "stats": {**question_opener_stats(final_cards),
                  "source": {"kind": kind, "declared": path_map.get("source_type"), "target": goal, "planned": planned, "items": source_profile.count_items(text),
                            "effective_chars": effective, "covered_windows": 0 if not novelty else sum(1 for x in novelty if x < 0.5),
                            "windows": len(novelty or []), "align": align_report, "merged_nodes": len(merge),
                            "dropped_existing_fronts": dropped_existing},
                  "quality": card_quality.quality_report(final_cards, dropped_unsupported, trimmed, weights),
                  "deck_policy": {**policy, "depth": depth, "existing_cards": existing_total, "new_share": round(new_share, 3)},
                  "lesson_alignment": coverage.lesson_alignment(packs),
                  "support": {**dict(support_final), "checked_first_pass": support},
                  "dedupe_removed": dedupe_removed, "audit": audit_report, "fill": fill_report,
                  "lesson_repair": repair_report, "budget": budget.report(),
                  "facts": {"asked": sum(b["k"] for b in fact_blocks), "blocks": len(fact_blocks), "per_card": fact_ratio, **facts_report,
                            "chars_per_fact": round(len(text) / max(1, facts_report["kept"]))},
                  "density": {"chars_per_card": round(len(text) / max(1, len(final_cards))),
                              "cards_per_10k_chars": round(10000 * len(final_cards) / max(1, len(text)), 2),
                              "cards_per_node": round(len(final_cards) / max(1, len(packs)), 2)},
                  "quota": {**quota_report(path_map, quotas, node_sizes, cards_by_node), "target": target,
                            "projected_cost_usd": round(projected, 4), "target_fits_ceiling": (not target) or target <= fits},
                  "rejected": {"by_reason": dict(Counter(r for r, _ in rejected)), "examples": [f"{r}: {t}" for r, t in rejected[:8]],
                               "text_reference_removed": sum(1 for c in final_cards if c.get("text_reference_removed"))}},
        "missing_nodes": [n["key"] for n in path_map["nodes"] if n["key"] not in packs],
        "calls": calls,
        "cost_usd": round(sum(c["cost_usd"] for c in calls), 6),
    }


__all__ = [
    "PathBuildError",
    "normalize_map",
    "plan_pack_batches",
    "normalize_cards",
    "normalize_lessons",
    "build_knowledge_map",
    "build_cards",
    "build_lessons",
    "build_cross_links",
    "normalize_edges",
    "build_learning_path",
    "build_align",
    "node_candidates",
    "normalize_align",
]
