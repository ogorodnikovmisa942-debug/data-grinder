#!/usr/bin/env python3
"""
Отчёт: насколько колода покрывает книгу.

Использование:
    python scripts/coverage_report.py книга.pdf колода.json

Колода — экспорт из бота («Моя колода (JSON)») или список карточек. Книга — PDF или .txt.
Печатает плотность карточек по главам и разделам, самые «голодные» разделы, однотипность вопросов и долю ответов
с числами/сроками. Ничего не отправляет в ИИ и ничего не пишет в базу.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ai_gateway import coverage  # noqa: E402
from app.services.ai_gateway.path_builder import question_opener_stats  # noqa: E402


def load_book(path: str) -> str:
    if path.lower().endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    return Path(path).read_text(encoding="utf-8")


def load_cards(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cards = data.get("cards") if isinstance(data, dict) else data
    return [c for c in cards if isinstance(c, dict) and c.get("text")]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    text, cards = load_book(sys.argv[1]), load_cards(sys.argv[2])
    sections = coverage.extract_sections(text)
    stats = question_opener_stats(cards)
    print(f"Карточек: {stats['cards']} на {len(text) // 1000} тыс. знаков книги "
          f"(одна карточка на {len(text) // max(1, stats['cards'])} знаков)")
    print(f"Самое частое начало вопроса: «{stats['top_opener']}» — {stats['top_opener_share'] * 100:.0f}% карточек")
    print(f"Ответов с цифрой: {stats['numeric_answers_share'] * 100:.1f}%")
    if not sections:
        print("Оглавление в тексте не распознано (нет нумерованных разделов): отчёт по разделам недоступен.")
        return 0

    report = coverage.section_card_report(text, sections, cards)
    by_chapter = defaultdict(lambda: [0, 0])
    for r in report:
        by_chapter[r["chapter"]][0] += r["chars"]
        by_chapter[r["chapter"]][1] += r["cards"]
    print(f"\nРазделов в книге: {len(sections)}, глав: {len(by_chapter)}")
    print("Глава | тыс. знаков | карточек | на 10 тыс. знаков")
    for ch, (chars, n) in sorted(by_chapter.items()):
        print(f"{ch:5} | {chars // 1000:11} | {n:8} | {10000 * n / max(1, chars):.1f}")
    weak = coverage.weak_sections(report)
    empty = [r for r in report if r["cards"] == 0 and r["chars"] >= coverage.MIN_SECTION_CHARS]
    print(f"\nКрупных разделов совсем без карточек: {len(empty)}; с плотностью ниже 40% средней: {len(weak)}")
    for r in sorted(weak, key=lambda r: -r["chars"])[:15]:
        print(f"  Гл. {r['chapter']} §{r['section']}  {r['chars'] // 1000} тыс. знаков, карточек {r['cards']}  {r['title'][:60]}")
    print("\nОграничение: карточка привязывается к разделу по совпадению слов, поэтому цифры приблизительные.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
