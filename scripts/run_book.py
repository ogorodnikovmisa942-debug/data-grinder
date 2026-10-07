#!/usr/bin/env python3
"""
Пробный прогон нарезки на ЦЕЛОЙ книге (БЕЗ записи в базу): карта, карточки, уроки, расход по вызовам, отчёт о балансе колоды.

Граф и плотность осмысленны только на целой книге: карта строится по всему учебнику (основы → темы → подтемы → кейсы, связи между
ветками), на отдельных главах она получается мелкой и разрозненной. Кусок книги годится лишь для проверки качества самих карточек.

Цель по карточкам — одна на PATH_CHARS_PER_CARD знаков (по умолчанию 2300, ≈ страница); --cards задаёт другое число.

Использование:
    python scripts/run_book.py книга.pdf метка                 # план: цель, прогноз стоимости; ничего не тратит
    python scripts/run_book.py книга.pdf метка --yes           # реальный прогон: ПЛАТНЫЕ вызовы DeepSeek
    python scripts/run_book.py книга.txt метка --cards 400 --yes

Результат — метка.json рядом с книгой (карта, карточки, уроки, статистика).
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ai_gateway import coverage, path_builder as pb  # noqa: E402
from app.services.ai_gateway.budget import Budget  # noqa: E402


def load_book(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8")


def balance_report(text: str, res: dict) -> list[str]:
    """Равномерность колоды: карточек на каждую пятую часть книги, окна без карточек, ключевые мысли."""
    cards = [c for p in res["packs"].values() for c in p["cards"]]
    lines = [f"карточек {len(cards)} на {len(text) // 1000} тыс. знаков → 1 на {len(text) // max(1, len(cards))} знаков; "
             f"узлов с карточками {len(res['packs'])} из {len(res['map']['nodes'])}"]
    index = coverage.SourceIndex(text)
    if index.usable and cards:
        per_window = coverage.card_density(index, cards)
        fifth = max(1, len(per_window) // 5)
        parts = [sum(per_window[i:i + fifth]) for i in range(0, fifth * 5, fifth)]
        lines.append("карточек по пятым частям книги (начало → конец): " + " | ".join(str(x) for x in parts))
        empty = sum(1 for x in per_window if x == 0)
        lines.append(f"окон по {coverage.WINDOW_CHARS} знаков без единой карточки: {empty} из {len(per_window)}")
    fs = res["stats"].get("facts") or {}
    lines.append(f"фактов конспекта темы: {fs.get('kept', 0)} (просили {fs.get('asked', 0)}, получили {fs.get('received', 0)}; "
                 f"отброшено: нет опоры в книге {fs.get('unsupported', 0)}, повтор карточки {fs.get('repeat_card', 0)}, повтор факта {fs.get('repeat_fact', 0)}); "
                 f"1 факт на {fs.get('chars_per_fact', 0)} знаков")
    return lines


async def run(text: str, tag: str, cards: int | None, out: Path) -> None:
    calls: list = []
    t0 = time.time()
    res = await pb.build_learning_path(text, tag, calls, card_total=cards)
    print(f"\n=== {tag}: {time.time() - t0:.0f} с, знаков {len(text)}")
    for c in calls:
        print(f"{c['label'][:46]:46} in={c['prompt_tokens']:7} hit={c['cache_hit_tokens']:7} out={c['completion_tokens']:6} "
              f"${c['cost_usd']:.4f} {c['duration_ms'] // 1000:4}с {c.get('finish_reason')}")
    print(f"ИТОГО ${sum(c['cost_usd'] for c in calls):.4f} за {len(calls)} вызовов")
    for line in balance_report(text, res):
        print(line)
    print("stats:", json.dumps({k: res["stats"][k] for k in ("quota", "density", "budget", "fill", "audit", "lesson_repair", "facts")
                                if k in res["stats"]}, ensure_ascii=False)[:1800])
    out.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print("сохранено:", out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("book", help="книга: .pdf или .txt (UTF-8)")
    ap.add_argument("tag", help="метка прогона (имя файла результата и slug предмета)")
    ap.add_argument("--cards", type=int, default=0, help="цель по числу карточек вместо «одна на PATH_CHARS_PER_CARD знаков»")
    ap.add_argument("--yes", action="store_true", help="выполнить реально (платные вызовы); без флага — только план")
    args = ap.parse_args()

    path = Path(args.book)
    text = load_book(path).strip()
    goal = args.cards or coverage.target_cards(len(text), pb.CHARS_PER_CARD)
    price = Budget.estimate_book_cost(len(text), goal)                    # цены вне пика; в пик та же работа вдвое дороже
    ceiling = Budget([], offpeak=True).limit
    print(f"книга: {len(text)} знаков (~{len(text) // 2500} страниц); цель {goal} карточек (1 на {len(text) // goal} знаков); "
          f"прогноз стоимости ~${price:.3f} вне пика при потолке ${ceiling:.2f}"
          + ("  <-- ДОРОЖЕ ПОТОЛКА" if price > ceiling else ""))
    if len(text) < 150_000:
        print("Внимание: это кусок, а не целая книга. Граф получится мелким и разрозненным; смотреть стоит только на качество карточек.")
    if not args.yes:
        print("План без запуска. Добавь --yes, чтобы выполнить реальный прогон (платные вызовы DeepSeek).")
        return 0
    asyncio.run(run(text, args.tag, args.cards or None, path.with_name(f"{args.tag}.json")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
