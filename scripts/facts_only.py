#!/usr/bin/env python3
"""
Только «Конспект темы» для уже готового прогона (дёшево): берёт результат scripts/run_book.py (карта и карточки) и книгу, заново
строит факты по блокам книги и сохраняет копию результата с новыми фактами. Для настройки конспекта без повторной оплаты карты,
карточек и уроков (≈ 1/5 цены прогона).

Использование:
    python scripts/facts_only.py книга.txt результат.json новый_результат.json            # план: блоки, число фактов, прогноз цены
    python scripts/facts_only.py книга.txt результат.json новый_результат.json --yes      # реальный прогон (платные вызовы DeepSeek)
    ... --scale 1.5      # больше/меньше фактов (во сколько раз от обычного)
Потом: python scripts/gold_recall.py новый_результат.json эталон.txt
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ai_gateway import card_quality, coverage, path_builder as pb, source_profile  # noqa: E402
from app.services.ai_gateway.budget import Budget  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("book")
    ap.add_argument("result")
    ap.add_argument("out")
    ap.add_argument("--yes", action="store_true", help="выполнить реально (платные вызовы)")
    ap.add_argument("--scale", type=float, default=1.0, help="множитель числа фактов относительно обычного")
    ap.add_argument("--total", type=int, default=0, help="общее число запрашиваемых фактов вместо расчёта по числу карточек")
    args = ap.parse_args()

    text = Path(args.book).read_text(encoding="utf-8").strip()
    res = json.loads(Path(args.result).read_text(encoding="utf-8"))
    index = coverage.SourceIndex(text)
    verifier = card_quality.CardVerifier(text, index)
    weights = coverage.window_saturation(index)
    kind = res.get("source_type") or source_profile.heuristic_kind(text)
    target = (res.get("stats", {}).get("quota") or {}).get("target") or sum(len(p["cards"]) for p in res["packs"].values())
    ratio = source_profile.facts_per_card(kind) * args.scale
    total = args.total or round(ratio * target)
    blocks = pb.plan_fact_blocks(text, index, verifier, weights, total)
    calls = -(-len(blocks) // pb.FACT_BATCH_BLOCKS)
    bt = Budget([], offpeak=True).book_tokens(len(text))
    est = Budget([], offpeak=True).facts_cost(total) + Budget([], offpeak=True).facts_overhead(bt, len(blocks), calls)
    print(f"тип {kind}; карточек {target}; фактов просим {sum(b['k'] for b in blocks)} ({ratio:.2f} на карточку) в {len(blocks)} блоках, запросов {calls}; "
          f"прогноз ~${est:.4f} (если книга в кэше; без кэша плюс чтение книги)")
    if not args.yes:
        print("План без запуска. Добавь --yes для реального прогона.")
        return 0

    log: list = []
    raw = asyncio.run(pb.build_facts(text, blocks, log))
    cards_by_node = {k: p["cards"] for k, p in res["packs"].items()}
    order = [n["key"] for n in res["map"]["nodes"]]
    matcher = coverage.NodeMatcher(index, res["map"]["nodes"], cards_by_node)
    facts, rep = card_quality.process_facts(verifier, raw, coverage.fact_blocks(text), matcher, cards_by_node, order)
    cost = sum(c["cost_usd"] for c in log)
    for c in log:
        print(f"{c['label'][:40]:40} in={c['prompt_tokens']:7} hit={c['cache_hit_tokens']:7} out={c['completion_tokens']:6} ${c['cost_usd']:.4f} {c['duration_ms'] // 1000}с")
    print(f"ИТОГО ${cost:.4f}; получено {len(raw)} фактов, оставлено {rep['kept']} (нет опоры {rep['unsupported']}, повтор карточки {rep['repeat_card']}, "
          f"повтор факта {rep['repeat_fact']}); 1 факт на {len(text) // max(1, rep['kept'])} знаков")
    res["facts"] = facts
    res.setdefault("stats", {})["facts"] = {"asked": sum(b["k"] for b in blocks), "blocks": len(blocks), "per_card": ratio, **rep}
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print("сохранено:", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
