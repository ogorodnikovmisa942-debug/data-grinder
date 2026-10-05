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
    build_links_task,
    build_intro_task,
    build_gaps_task,
    build_audit_task,
    build_fill_task,
    build_lessons_task,
)
from . import coverage
from . import card_quality
from .budget import Budget

# ~1M токенов контекста deepseek-flash; оставляем запас под промпт, карту и ответ
MAX_SOURCE_CHARS = 1_800_000
# Платим только за фактический вывод, поэтому лимиты с запасом: обрезанный ответ дороже
MAP_MAX_TOKENS = 40000
PACK_MAX_TOKENS = 48000
LINKS_MAX_TOKENS = 8000
# Для учебника/конспекта такого объёма карта без тем (только основы) — брак, а не результат
MIN_SOURCE_CHARS_FOR_TOPICS = 15000
# Повтор карты: при 0.1–0.5 модель иногда снова отвечает «лениво» (только 8 узлов основ) — 0.7 в замере давал полную карту
RETRY_TEMPERATURE = 0.7
LAST_RETRY_TEMPERATURE = 0.9
DEGENERATE_MAP_NUDGE = (
    "\n\nNOTE: your previous answer was too thin for this source (too few nodes or missing tiers). "
    "The MAP must cover the WHOLE source with all four tiers: tier 1 (5-10 topics), tier 2 (the subtopic target of the task block), "
    "tier 3 (5-12 cases), plus the edges. Return the complete MAP JSON."
)
PACK_BATCH_MAX_NODES = 10
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
# Сколько карточек просить на узел: пропорционально его куску книги (≈ 1 карточка на столько знаков), в рамках границ
CHARS_PER_CARD = int(os.getenv("PATH_CHARS_PER_CARD", "2300"))
MAX_TOTAL_CARDS = int(os.getenv("PATH_MAX_TOTAL_CARDS", "700"))
# Паки идут одновременно: книга к этому моменту уже в кэше после карты. Замер при 4: 17 запросов = 5 «волн» по ~75 с
PACK_CONCURRENCY = int(os.getenv("PATH_PACK_CONCURRENCY", "8"))
LLM_TIMEOUT_S = 600.0
# Карта строится дважды (вторая попытка читает книгу из кэша почти бесплатно) — берём лучшую по программной оценке
MAP_CANDIDATES = 2
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

    return {
        "title": str(raw.get("title") or "").strip()[:160],
        "domain": domain,
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
    for branch in (n for n in nodes if n["tier"] == 1):
        members = [branch["key"]] + [n["key"] for n in nodes if n["parent"] == branch["key"]]
        placed.update(members)
        push(members)
    push([n["key"] for n in nodes if n["key"] not in placed])
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
    from .client import LLMCallError, MODEL_FALLBACK
    last_err: Exception | None = None
    for attempt in range(HANG_RETRIES + 1):
        started = time.time()
        extra: dict = {}
        if thinking:
            extra["thinking"] = True
        if attempt == HANG_RETRIES and HANG_RETRIES > 0:
            extra["model"] = MODEL_FALLBACK   # две попытки основной моделью не прошли — страхуемся
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
        except (asyncio.TimeoutError, TimeoutError, httpx.TransportError) as e:
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


def map_problem(path_map: dict, source_chars: int) -> str | None:
    """Карта слишком скудная для такого источника («ленивый» ответ модели)? Возвращает причину или None.
    Для коротких текстов проверяем только наличие тем; для книги — размер карты и подтемы."""
    if source_chars < MIN_SOURCE_CHARS_FOR_TOPICS:
        return None
    nodes = path_map["nodes"]
    tiers = [sum(1 for n in nodes if n["tier"] == t) for t in range(4)]
    if not any(n["tier"] >= 1 for n in nodes):
        return f"вырожденная карта: {len(nodes)} узлов, ни одной темы"
    if len(nodes) < max(6, source_chars // 40_000):
        return f"скудная карта: {len(nodes)} узлов на {source_chars // 1000} тыс. знаков источника"
    if source_chars >= BIG_SOURCE_CHARS and (tiers[1] < 3 or tiers[2] < 8):
        return f"скудная карта: тем {tiers[1]}, подтем {tiers[2]} для книги"
    return None


_TIER_TARGETS = {0: (5, 8), 1: (5, 10), 2: (15, 30), 3: (5, 12)}


def score_map(path_map: dict, source_chars: int | None = None) -> float:
    """Программная оценка карты (больше — лучше): ярусы в заданных диапазонах (подтем — по размеру книги), у узлов есть
    summary и src, подтемы привязаны к темам, достаточно осмысленных связей. Не заменяет чтение карты человеком,
    но отсеивает брак."""
    nodes = path_map["nodes"]
    if not nodes:
        return 0.0
    targets = dict(_TIER_TARGETS)
    if source_chars:
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
    score += 1.0 if 15 <= edges <= 60 else max(0.0, 1.0 - (15 - edges if edges < 15 else edges - 60) / 15)
    return round(score, 3)


async def build_knowledge_map(text: str, subject: str, calls_log: list, attempts: int = 3, candidates: int = 1) -> dict:
    from .client import LLMOutputTruncated
    base_prompt = build_source_block(text) + build_map_task(subject, len(text), CHARS_PER_CARD)
    prompt = base_prompt
    last_err = None
    best = None
    for attempt in range(1, attempts + 1):
        try:
            # Повтор после брака — с большей температурой, иначе модель выдаст тот же ответ
            temperature = 0.1 if attempt == 1 else (RETRY_TEMPERATURE if attempt == 2 else LAST_RETRY_TEMPERATURE)
            path_map = normalize_map(await _call(prompt, MAP_MAX_TOKENS, f"map#{attempt}", calls_log, temperature))
            problem = map_problem(path_map, len(text))
            if problem:
                # Подсказка уходит только в хвост запроса: префикс «system + книга» остаётся прежним (кэш DeepSeek)
                prompt = base_prompt + DEGENERATE_MAP_NUDGE
                raise PathBuildError(problem)
            best = path_map
            break
        except LLMOutputTruncated as e:
            # Тот же запрос обрежется снова — не тратим деньги на повтор
            raise PathBuildError(f"Карта знаний не поместилась в лимит ответа: {e}") from e
        except Exception as e:  # noqa: BLE001 — ретраим сбои сети/парсинга
            last_err = e
            print(f"[Path Builder WARN] MAP попытка {attempt}/{attempts}: {e}", flush=True)
    if best is None:
        raise PathBuildError(f"Не удалось построить карту знаний: {last_err}")

    # Вторая карта: книга уже в кэше, платим почти только за вывод. Берём лучшую; сбой второй карты не фатален
    for extra in range(2, candidates + 1):
        try:
            other = normalize_map(await _call(prompt, MAP_MAX_TOKENS, f"map#alt{extra}", calls_log, MAP_SECOND_TEMPERATURE))
            if map_problem(other, len(text)):
                continue
            s_best, s_other = score_map(best, len(text)), score_map(other, len(text))
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


def plan_card_quotas(text: str, path_map: dict, size_hints: dict[str, int] | None = None,
                     total_cap: int | None = None, index: "coverage.SourceIndex | None" = None) -> dict[str, int] | None:
    """Квоты карточек по размеру куска книги, который покрывает узел. None — текст слишком короткий/без слов: тогда
    промпт использует обычные диапазоны по ярусам. size_hints — известный объём (узлы, добавленные проверкой охвата).
    total_cap — сколько карточек позволяет бюджет (действует всегда); MAX_TOTAL_CARDS и потолок на узел — только при CARD_CAPS."""
    index = index or coverage.SourceIndex(text)
    if not index.usable:
        return None
    sizes = coverage.node_source_sizes(index, path_map["nodes"])
    sizes.update({k: v for k, v in (size_hints or {}).items() if k in sizes})
    cap = total_cap
    if CARD_CAPS:
        cap = MAX_TOTAL_CARDS if cap is None else min(MAX_TOTAL_CARDS, cap)
    if cap is not None:
        cap = max(1, cap)       # бюджет исчерпан (0) — это «минимум на узел», а не «без ограничения»: card_quotas считает 0 за отсутствие лимита
    return coverage.card_quotas(path_map["nodes"], sizes, chars_per_card=CHARS_PER_CARD,
                                max_cards=coverage.MAX_CARDS_PER_NODE if CARD_CAPS else None, total_cap=cap)


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
            raw = await _call(make_prompt(missing), max_tokens, f"{label}{'*' if thinking else ''}[{','.join(missing)}]#{attempt}",
                              calls_log, thinking=thinking)
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
                      rejected: list | None = None, strict: bool = False) -> dict[str, list[dict]]:
    """Карточки узлов с цитатами из книги. Книга идёт префиксом запроса (кэш DeepSeek). Узел без единой годной карточки
    считается не полученным и запрашивается повторно."""
    map_json = json.dumps(path_map, ensure_ascii=False, separators=(",", ":"))

    def make_prompt(missing: list[str]) -> str:
        return build_source_block(text) + build_cards_task(map_json, missing, quotas, strict)

    def normalize(raw, missing):
        return {k: v for k, v in normalize_cards(raw, path_map, missing, quotas, rejected).items() if v}

    return await _run_batched(node_keys, make_prompt, normalize, CORE_PACK_MAX_TOKENS if thinking else PACK_MAX_TOKENS,
                              "cards", calls_log, attempts, thinking)


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
        return {"checked": 0, "skipped": len(flagged)}
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


async def fill_cards(verifier: "card_quality.CardVerifier", path_map: dict, cards_by_node: dict[str, list[dict]],
                     calls_log: list, budget: Budget, must_keep: float, rejected: list | None = None) -> dict:
    """Добор карточек на абзацах книги, которых не коснулась ни одна карточка (по точным местам цитат). Передаётся только сам
    кусок (не вся книга). Новые карточки проходят ту же проверку по книге; не прошедшие отбрасываются. Сбой не фатален."""
    all_cards = [c for lst in cards_by_node.values() for c in lst]
    spans = verifier.uncovered_passages(all_cards)                  # абзацы, которых не коснулась ни одна карточка
    if CARD_CAPS:
        spans = sorted(spans, key=lambda sp: -sp["chars"])[:MAX_FILL_SPANS]
    if not spans:
        return {"spans": 0, "added": 0}
    n = budget.affordable("добор участков", budget.fill_cost(1), must_keep, len(spans))
    if n <= 0:
        return {"spans": 0, "added": 0, "skipped": len(spans)}
    spans = spans[:n]
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
                  omitted: list[str] | None = None) -> str:
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
    if omitted:
        lines.append("THE PREVIOUS LESSON OMITTED THESE ANSWERS: " + " ; ".join(omitted))
    return "\n".join(lines)


async def build_lessons(verifier: "card_quality.CardVerifier", path_map: dict, cards_by_node: dict[str, list[dict]],
                        calls_log: list, keys: list[str] | None = None, omitted: dict[str, list[str]] | None = None,
                        attempts: int = 3) -> dict[str, dict]:
    """Уроки узлов: по LESSON_BATCH_NODES узлов в запросе, запросы идут одновременно. Узел без карточек урока не получает."""
    by_key = {n["key"]: n for n in path_map["nodes"]}
    order = [n["key"] for n in path_map["nodes"] if (keys is None or n["key"] in keys) and cards_by_node.get(n["key"])]
    fronts = {k: {_norm_text(c["text"]) for c in cards_by_node[k]} for k in order}
    chunks = [order[i:i + LESSON_BATCH_NODES] for i in range(0, len(order), LESSON_BATCH_NODES)]
    sem = asyncio.Semaphore(PACK_CONCURRENCY)

    def make_prompt(missing: list[str]) -> str:
        blocks = [_lesson_block(by_key[k], cards_by_node[k], path_map, by_key, verifier, (omitted or {}).get(k)) for k in missing]
        return build_lessons_task("\n\n".join(blocks))

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

async def build_learning_path(text: str, subject: str, calls: list | None = None) -> dict:
    """Полный прогон: MAP → CARDS → проверка по книге (AUDIT) и добор (FILL) → LESSON из карточек.
    Возвращает карту, пакеты узлов {урок, карточки} и телеметрию."""
    text = (text or "").strip()
    if not text:
        raise PathBuildError("Пустой источник")
    if len(text) > MAX_SOURCE_CHARS:
        raise PathBuildError(f"Источник слишком большой ({len(text)} знаков, максимум {MAX_SOURCE_CHARS})")

    # Список вызовов передаётся снаружи, чтобы расходы учитывались и при сбое
    calls = calls if calls is not None else []
    budget = Budget(calls)
    path_map = await build_knowledge_map(text, subject, calls, candidates=MAP_CANDIDATES)
    path_map, size_hints, gap_report = await fill_map_gaps(text, path_map, calls)

    index = coverage.SourceIndex(text)
    verifier = card_quality.CardVerifier(text, index)
    node_sizes = coverage.node_source_sizes(index, path_map["nodes"]) if index.usable else None
    if node_sizes:
        node_sizes.update({k: v for k, v in size_hints.items() if k in node_sizes})
    core_keys = {n["key"] for n in path_map["nodes"] if n["tier"] <= 1}
    batches = plan_pack_batches(path_map, split_core=THINK_CORE)
    cap = budget.card_cap(len(text), len(path_map["nodes"]), len(batches))
    quotas = plan_card_quotas(text, path_map, size_hints, total_cap=cap, index=index)
    wanted = plan_card_quotas(text, path_map, size_hints, total_cap=None, index=index)       # сколько нужно по объёму книги, без учёта денег
    strict = bool(quotas and wanted and sum(quotas.values()) < sum(wanted.values()))        # денег не хватает на всё: берём самое важное
    print(f"[Path Builder] бюджет: потрачено ${budget.spent():.4f} из ${budget.limit:.2f}; карточек по карману: {cap}; "
          f"по объёму книги нужно {sum(wanted.values()) if wanted else '—'}, квот {sum(quotas.values()) if quotas else '—'}"
          f"{' (STRICT: берём самое важное)' if strict else ''}; прочие потолки числа карточек {'ВКЛ' if CARD_CAPS else 'выкл'}", flush=True)

    semaphore = asyncio.Semaphore(PACK_CONCURRENCY)

    rejected: list[tuple[str, str]] = []                       # карточки, отброшенные при разборе ответа: (причина, вопрос)

    async def run(batch: list[str]):
        async with semaphore:
            return await build_cards(text, path_map, batch, calls, thinking=THINK_CORE and all(k in core_keys for k in batch),
                                     quotas=quotas, rejected=rejected, strict=strict)

    results = await asyncio.gather(
        build_cross_links(text, path_map, calls),
        build_intro_lesson(path_map, calls),
        *(run(b) for b in batches),
    )
    path_map["edges"].extend(results[0])
    intro = results[1]
    cards_by_node: dict[str, list[dict]] = {}
    for part in results[2:]:
        cards_by_node.update(part)

    # Качество карточек: повторы, проверка по книге, добор — до уроков, потому что урок строится из окончательного набора
    order = [n["key"] for n in path_map["nodes"]]
    dedupe_removed = card_quality.dedupe_cards(cards_by_node, order)
    all_cards = [c for k in order for c in cards_by_node.get(k, [])]
    support, flagged = verifier.verify(all_cards)

    def reserve() -> float:
        return budget.lessons_cost(sum(1 for v in cards_by_node.values() if v), sum(len(v) for v in cards_by_node.values()))

    audit_report = await audit_cards(verifier, cards_by_node, flagged, calls, budget, reserve())
    fill_report = await fill_cards(verifier, path_map, cards_by_node, calls, budget, reserve(), rejected)
    dedupe_removed += card_quality.dedupe_cards(cards_by_node, order)       # добранные карточки могут повторять уже имеющиеся

    lessons = await build_lessons(verifier, path_map, cards_by_node, calls)
    repair_report = await repair_lessons(verifier, path_map, cards_by_node, lessons, calls, budget)

    packs = {k: {"lesson": lessons.get(k), "cards": cards_by_node[k]} for k in order if cards_by_node.get(k)}
    final_cards = [c for p in packs.values() for c in p["cards"]]
    support_final = Counter(c.get("support") for c in final_cards)

    return {
        "map": path_map,
        "packs": packs,
        "intro": intro,
        "quotas": quotas,
        "gap_report": gap_report,
        "stats": {**question_opener_stats(final_cards),
                  "lesson_alignment": coverage.lesson_alignment(packs),
                  "support": {**dict(support_final), "checked_first_pass": support},
                  "dedupe_removed": dedupe_removed, "audit": audit_report, "fill": fill_report,
                  "lesson_repair": repair_report, "budget": budget.report(),
                  "quota": {**quota_report(path_map, quotas, node_sizes, cards_by_node), "strict": strict,
                            "wanted_by_book_size": sum(wanted.values()) if wanted else None},
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
]
