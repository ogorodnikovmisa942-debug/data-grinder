"""
Разбор билетов: пачками по TICKET_BATCH вопросов к курсу (узлы + карточки), параллельно.
Префикс «system + курс» общий для всех пачек — после первой пачки он читается из кэша DeepSeek.
"""
import asyncio
import time

from .exam_prompts import EXAM_MATCHER_SYSTEM_PROMPT, build_course_block, build_tickets_task

TICKET_BATCH = 10
MATCH_CONCURRENCY = 4
MATCH_MAX_TOKENS = 8000
MATCH_TIMEOUT_S = 180.0
# Жёсткий предел на попытку: таймаут httpx — это пауза между байтами, а перегруженный DeepSeek
# держит соединение пустыми строками до 10 минут. Без этого предела разбор «висел» бы.
MATCH_HARD_LIMIT_S = 150.0
MAX_NODES_PER_TICKET = 5
MAX_POINTS_PER_TICKET = 8


class ExamMatchError(Exception):
    pass


class ExamBudgetExceeded(ExamMatchError):
    """Потолок расхода на разбор исчерпан: ни одна пачка не запущена (или запуск прекращён)."""


def _get_call_deepseek():
    from .client import call_deepseek
    return call_deepseek


def normalize_ticket_results(raw: dict, numbers: list[int], valid_keys: set[str]) -> dict[int, dict]:
    """Ответ модели → {номер: {"nodes": [keys], "found": bool, "points": [{text, variants, weight}]}}.
    Несуществующие ключи отбрасываются; билет без единой найденной темы считается ненайденным."""
    out: dict[int, dict] = {}
    items = (raw or {}).get("tickets") if isinstance(raw, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            i = int(item.get("i"))
        except (TypeError, ValueError):
            continue
        if i not in numbers or i in out:
            continue
        keys = []
        for k in item.get("nodes") or []:
            k = str(k).strip().lstrip("@")
            if k in valid_keys and k not in keys:
                keys.append(k)
        points = []
        for p in item.get("points") or []:
            if not isinstance(p, dict):
                continue
            text = " ".join(str(p.get("t") or "").split())
            if not text:
                continue
            variants = [" ".join(str(v).split()) for v in (p.get("v") or []) if str(v).strip()]
            try:
                weight = float(p.get("w", 1))
            except (TypeError, ValueError):
                weight = 1.0
            points.append({"text": text[:300], "variants": [v[:300] for v in variants[:6]], "weight": max(0.5, min(2.0, weight))})
        keys = keys[:MAX_NODES_PER_TICKET]
        found = bool(item.get("found")) and bool(keys) and bool(points)
        out[i] = {"nodes": keys, "found": found, "points": points[:MAX_POINTS_PER_TICKET] if found else []}
    return out


async def match_tickets(nodes: list[dict], cards_by_node: dict, questions: list[str], calls_log: list,
                        max_cost_usd: float | None = None) -> list[dict | None]:
    """Возвращает по элементу на вопрос (в том же порядке). None — билет не разобран: его пачка не удалась дважды или упёрлась в потолок
    расхода max_cost_usd (проверяется перед каждой попыткой, перерасход не больше вызовов, уже бывших в полёте). Остальные билеты
    возвращаются как есть, чтобы оплаченное не пропадало, а повторять пришлось только неразобранные. Не разобрана ни одна пачка — ошибка.
    Первая пачка идёт одна: она прогревает кэш курса, иначе первые параллельные пачки платили бы за весь курс по цене промаха."""
    from .client import LLMCallError

    prefix = build_course_block(nodes, cards_by_node)
    valid_keys = {n["key"] for n in nodes}
    numbered = list(enumerate(questions, start=1))
    batches = [numbered[i:i + TICKET_BATCH] for i in range(0, len(numbered), TICKET_BATCH)]
    semaphore = asyncio.Semaphore(MATCH_CONCURRENCY)

    def over_budget() -> bool:
        return max_cost_usd is not None and sum(c.get("cost_usd", 0.0) for c in calls_log) >= max_cost_usd

    async def run(batch_idx: int, batch: list[tuple[int, str]]) -> dict[int, dict]:
        numbers = [i for i, _ in batch]
        last_err = None
        for attempt in (1, 2):
            started = time.time()
            async with semaphore:
                if over_budget():
                    raise ExamBudgetExceeded(f"Билеты {numbers[0]}–{numbers[-1]}: исчерпан лимит расходов на разбор")
                try:
                    res, meta = await asyncio.wait_for(_get_call_deepseek()(
                        prefix + build_tickets_task(batch),
                        system_instruction=EXAM_MATCHER_SYSTEM_PROMPT,
                        max_tokens=MATCH_MAX_TOKENS,
                        timeout=MATCH_TIMEOUT_S,
                        temperature=0.1 if attempt == 1 else 0.4,
                    ), MATCH_HARD_LIMIT_S)
                except LLMCallError as e:
                    _log(calls_log, f"exam#{batch_idx}.{attempt}", started, e.meta, str(e)[:200])
                    last_err = e
                    continue
                except Exception as e:  # noqa: BLE001 — сеть/таймаут: пробуем ещё раз
                    _log(calls_log, f"exam#{batch_idx}.{attempt}", started, {}, (str(e) or type(e).__name__)[:200])
                    last_err = e
                    continue
            _log(calls_log, f"exam#{batch_idx}.{attempt}", started, meta)
            parsed = normalize_ticket_results(res, numbers, valid_keys)
            if len(parsed) >= max(1, len(numbers) - 1):
                return parsed
            last_err = ExamMatchError(f"в ответе {len(parsed)} из {len(numbers)} билетов")
        raise ExamMatchError(f"Не удалось разобрать билеты {numbers[0]}–{numbers[-1]}: {last_err}")

    outcomes = list(await asyncio.gather(run(1, batches[0]), return_exceptions=True)) if batches else []
    if len(batches) > 1:
        outcomes += await asyncio.gather(*(run(k, b) for k, b in enumerate(batches[1:], start=2)), return_exceptions=True)
    merged: dict[int, dict] = {}
    failed: set[int] = set()
    last_err: Exception | None = None
    for batch, out in zip(batches, outcomes):
        if isinstance(out, Exception):
            last_err = out
            failed.update(i for i, _ in batch)
        else:
            merged.update(out)
    if batches and not merged:
        raise last_err if isinstance(last_err, ExamMatchError) else ExamMatchError(str(last_err))
    return [None if i in failed else merged.get(i, {"nodes": [], "found": False, "points": []}) for i, _ in numbered]


def _log(calls_log: list, label: str, started: float, meta: dict, error: str | None = None) -> None:
    calls_log.append({
        "label": label,
        "duration_ms": int((time.time() - started) * 1000),
        "prompt_tokens": meta.get("prompt_tokens", 0),
        "cache_hit_tokens": meta.get("cache_hit_tokens", 0),
        "completion_tokens": meta.get("completion_tokens", 0),
        "cost_usd": meta.get("cost_usd", 0.0),
        "finish_reason": meta.get("finish_reason"),
        "model": meta.get("model_resolved"),
        "error": error,
    })


__all__ = ["ExamMatchError", "ExamBudgetExceeded", "TICKET_BATCH", "normalize_ticket_results", "match_tickets"]
