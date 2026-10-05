#!/usr/bin/env python3
"""
Отчёт: насколько колода покрывает книгу.

Использование:
    python scripts/coverage_report.py книга.pdf колода.json

Колода — экспорт из бота («Моя колода (JSON)») или список карточек. Книга — PDF или .txt.
Печатает проверку по книге (есть ли ответ карточки в тексте; для колод с полем evidence — найдена ли цитата), плотность карточек
по главам и разделам, самые «голодные» разделы, однотипность вопросов и долю ответов с числами/сроками.
Ничего не отправляет в ИИ и ничего не пишет в базу.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ai_gateway import card_quality, coverage  # noqa: E402
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

    verifier = card_quality.CardVerifier(text)
    cards = [{**c, "translation": c.get("translation") or c.get("answer") or "", "evidence": c.get("evidence") or ""} for c in cards]
    summary, flagged = verifier.verify(cards)
    print(f"\nПроверка по книге: цитата найдена точно — {summary['grounded']}, почти — {summary['near']}, "
          f"ответ найден рядом по словам — {summary['lexical']}, не проверить — {summary['unchecked']}, "
          f"подозрительных — {summary['flagged']} ({100 * summary['flagged'] / max(1, summary['cards']):.0f}%)")
    for c in flagged[:15]:
        print(f"  ? {c['text'][:80]} => {c['translation'][:50]}  [{c.get('support_reason')}]")
    thin = verifier.thin_spans(cards)
    print(f"Участков книги без единой карточки (от {card_quality.MIN_THIN_SPAN_CHARS // 1000} тыс. знаков): {len(thin)}, "
          f"всего {sum(t['chars'] for t in thin) // 1000} тыс. знаков")

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
