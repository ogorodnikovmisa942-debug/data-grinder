"""
Конвейер «Путь знаний»: книга целиком → ярусная карта (MAP) → урок + карточки + дистракторы на узел (NODE_PACK).

Все вызовы начинаются с одинакового префикса [system][книга], поэтому после первого вызова
DeepSeek читает книгу из кэша. Модуль чистый: ничего не пишет в БД, только возвращает результат
и телеметрию вызовов. Сохранение — в generation_worker.
"""
import asyncio
import json
import os
import random
import re
import time

import httpx

from app.core.config import settings
from .blacklist import is_blacklisted_card, strip_secondary_spoilers
from .path_prompts import (
    PATH_BUILDER_SYSTEM_PROMPT,
    build_source_block,
    build_map_task,
    build_node_pack_task,
    build_links_task,
    build_intro_task,
)

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
    "\n\nNOTE: your previous answer was incomplete — it contained only tier-0 nodes. "
    "The MAP must cover the WHOLE source with all four tiers: tier 1 (5-10 topics), tier 2 (15-30 subtopics), "
    "tier 3 (5-12 cases), plus the edges. Return the complete MAP JSON."
)
PACK_BATCH_MAX_NODES = 10
PACK_CONCURRENCY = 4
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
    "part_of", "depends_on", "kind_of", "demarcated_from", "appealed_to",
    "excludes_application", "subject_to_jurisdiction", "leads_to",
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

    return {
        "title": str(raw.get("title") or "").strip()[:160],
        "domain": str(raw.get("domain") or "generic").strip().lower(),
        "nodes": nodes,
        "edges": edges,
    }


def plan_pack_batches(path_map: dict, max_nodes: int = PACK_BATCH_MAX_NODES, split_core: bool = False) -> list[list[str]]:
    """Группирует узлы в батчи NODE_PACK: основы отдельно, дальше — по веткам яруса 1.
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
# NODE_PACK
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


def _normalize_lesson(raw, valid_keys: set[str], max_screens: int = 6) -> dict | None:
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


def _normalize_card(raw, node: dict, domain: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    card = {
        "text": str(raw.get("t") or "").strip(),
        "secondary_text": str(raw.get("s") or "").strip(),
        "translation": str(raw.get("d") or "").strip(),
        "example": str(raw.get("e") or "").strip(),
        "initial_difficulty_tier": str(raw.get("l") or "medium").strip().lower(),
        "layer": max(0, min(2, _as_int(raw.get("y"), 1))),
        "answer_type": str(raw.get("at") or "").strip().lower() or None,
        "theme": node["name"],
        "node_key": node["key"],
    }
    card["secondary_text"] = strip_secondary_spoilers(card["text"], card["translation"], card["secondary_text"])
    if card["initial_difficulty_tier"] not in VALID_LEVELS:
        card["initial_difficulty_tier"] = "medium"
    if card["answer_type"] not in VALID_ANSWER_TYPES:
        card["answer_type"] = None
    if node["tier"] == 3:
        card["layer"] = 2
    blocked, _ = is_blacklisted_card(card, subject_domain=domain)
    if blocked:
        return None
    card["distractors"] = _clean_distractors(card["translation"], raw.get("x"))
    return card


def normalize_pack(raw: dict, path_map: dict, requested_keys: list[str]) -> dict[str, dict]:
    """Возвращает {node_key: {"lesson": ..., "cards": [...]}} только для запрошенных узлов."""
    if not isinstance(raw, dict):
        raise PathBuildError("NODE_PACK: ответ не является объектом")
    by_key = {n["key"]: n for n in path_map["nodes"]}
    valid_keys = set(by_key)
    requested = set(requested_keys)
    domain = path_map.get("domain") or "generic"

    result: dict[str, dict] = {}
    for item in raw.get("nodes") or []:
        if not isinstance(item, dict):
            continue
        key = _slug(item.get("key") or "")
        if key not in requested or key in result:
            continue
        node = by_key[key]
        cards, seen_fronts = [], set()
        for c in item.get("cards") or []:
            card = _normalize_card(c, node, domain)
            if not card:
                continue
            front = _norm_text(card["text"])
            if front in seen_fronts:
                continue
            seen_fronts.add(front)
            cards.append(card)
        lesson = _normalize_lesson(item.get("lesson"), valid_keys)
        if lesson:
            lesson["check"] = _finalize_checks(lesson["check"], seen_fronts, key)
        result[key] = {"lesson": lesson, "cards": cards}
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


_TIER_TARGETS = {0: (5, 8), 1: (5, 10), 2: (15, 30), 3: (5, 12)}


def score_map(path_map: dict) -> float:
    """Программная оценка карты (больше — лучше): ярусы в заданных диапазонах, у узлов есть summary и src,
    подтемы привязаны к темам, достаточно осмысленных связей. Не заменяет чтение карты человеком, но отсеивает брак."""
    nodes = path_map["nodes"]
    if not nodes:
        return 0.0
    score = 0.0
    for tier, (lo, hi) in _TIER_TARGETS.items():
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
    base_prompt = build_source_block(text) + build_map_task(subject)
    prompt = base_prompt
    last_err = None
    best = None
    for attempt in range(1, attempts + 1):
        try:
            # Повтор после брака — с большей температурой, иначе модель выдаст тот же ответ
            temperature = 0.1 if attempt == 1 else (RETRY_TEMPERATURE if attempt == 2 else LAST_RETRY_TEMPERATURE)
            path_map = normalize_map(await _call(prompt, MAP_MAX_TOKENS, f"map#{attempt}", calls_log, temperature))
            if len(text) >= MIN_SOURCE_CHARS_FOR_TOPICS and not any(n["tier"] >= 1 for n in path_map["nodes"]):
                # Подсказка уходит только в хвост запроса: префикс «system + книга» остаётся прежним (кэш DeepSeek)
                prompt = base_prompt + DEGENERATE_MAP_NUDGE
                raise PathBuildError(f"вырожденная карта: {len(path_map['nodes'])} узлов, ни одной темы")
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
            if len(text) >= MIN_SOURCE_CHARS_FOR_TOPICS and not any(n["tier"] >= 1 for n in other["nodes"]):
                continue
            s_best, s_other = score_map(best), score_map(other)
            print(f"[Path Builder] MAP: оценки {s_best} (1) и {s_other} (альтернатива)", flush=True)
            if s_other > s_best:
                best = other
        except Exception as e:  # noqa: BLE001
            print(f"[Path Builder WARN] MAP-альтернатива {extra}: {e}", flush=True)
    return best


async def build_node_pack(text: str, path_map: dict, node_keys: list[str], calls_log: list, attempts: int = 3,
                          thinking: bool = False) -> dict[str, dict]:
    from .client import LLMOutputTruncated
    map_json = json.dumps(path_map, ensure_ascii=False, separators=(",", ":"))
    collected: dict[str, dict] = {}
    for attempt in range(1, attempts + 1):
        missing = [k for k in node_keys if k not in collected]
        if not missing:
            break
        prompt = build_source_block(text) + build_node_pack_task(map_json, missing)
        try:
            raw = await _call(prompt, CORE_PACK_MAX_TOKENS if thinking else PACK_MAX_TOKENS,
                              f"pack{'*' if thinking else ''}[{','.join(missing)}]#{attempt}", calls_log, thinking=thinking)
            collected.update(normalize_pack(raw, path_map, missing))
        except LLMOutputTruncated as e:
            if len(missing) == 1:
                print(f"[Path Builder WARN] NODE_PACK {missing}: узел не помещается в лимит ответа, пропуск: {e}", flush=True)
                break
            # Делим батч пополам вместо повтора того же запроса
            half = len(missing) // 2
            for part in (missing[:half], missing[half:]):
                collected.update(await build_node_pack(text, path_map, part, calls_log, attempts=attempts - attempt + 1, thinking=thinking))
            break
        except Exception as e:  # noqa: BLE001
            print(f"[Path Builder WARN] NODE_PACK {missing} попытка {attempt}/{attempts}: {e}", flush=True)
    return collected


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


async def build_learning_path(text: str, subject: str, calls: list | None = None) -> dict:
    """Полный прогон: MAP → NODE_PACK по веткам. Возвращает карту, пакеты узлов и телеметрию."""
    text = (text or "").strip()
    if not text:
        raise PathBuildError("Пустой источник")
    if len(text) > MAX_SOURCE_CHARS:
        raise PathBuildError(f"Источник слишком большой ({len(text)} знаков, максимум {MAX_SOURCE_CHARS})")

    # Список вызовов передаётся снаружи, чтобы расходы учитывались и при сбое
    calls = calls if calls is not None else []
    path_map = await build_knowledge_map(text, subject, calls, candidates=MAP_CANDIDATES)

    semaphore = asyncio.Semaphore(PACK_CONCURRENCY)
    core_keys = {n["key"] for n in path_map["nodes"] if n["tier"] <= 1}

    async def run(batch: list[str]):
        async with semaphore:
            return await build_node_pack(text, path_map, batch, calls, thinking=THINK_CORE and all(k in core_keys for k in batch))

    packs: dict[str, dict] = {}
    results = await asyncio.gather(
        build_cross_links(text, path_map, calls),
        build_intro_lesson(path_map, calls),
        *(run(b) for b in plan_pack_batches(path_map, split_core=THINK_CORE)),
    )
    path_map["edges"].extend(results[0])
    intro = results[1]
    for part in results[2:]:
        packs.update(part)

    return {
        "map": path_map,
        "packs": packs,
        "intro": intro,
        "missing_nodes": [n["key"] for n in path_map["nodes"] if n["key"] not in packs],
        "calls": calls,
        "cost_usd": round(sum(c["cost_usd"] for c in calls), 6),
    }


__all__ = [
    "PathBuildError",
    "normalize_map",
    "plan_pack_batches",
    "normalize_pack",
    "build_knowledge_map",
    "build_node_pack",
    "build_cross_links",
    "normalize_edges",
    "build_learning_path",
]
