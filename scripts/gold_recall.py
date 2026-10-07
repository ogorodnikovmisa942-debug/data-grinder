#!/usr/bin/env python3
"""
Покрытие эталона: какая доля «фактов, которые спросят на экзамене» есть в колоде.

Использование:
    python scripts/gold_recall.py результат.json эталон.txt [-v]

результат.json — файл прогона (scripts/run_book.py: карточки, уроки, конспект темы) либо экспорт колоды (только карточки).
эталон.txt — по строке на факт, поля через « | »:
    раздел | факт словами | группа слов; группа слов; ...
Факт считается покрытым, если в одной карточке (вопрос + ответ), одном факте конспекта темы или одном экране урока есть ВСЕ слова хотя бы
одной группы (слова в группе через пробел; ищутся как подстроки без учёта регистра, «ё» = «е»). Строки с «#» в начале и пустые игнорируются.
Считается отдельно: «спрашивает карточка», «есть в конспекте темы», «названо в уроке» и «хоть где-то» (то, что колода даёт ученику).
Ничего не отправляет в ИИ и ничего не пишет в базу.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path


def norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def load_gold(path: Path) -> list[tuple[str, str, list[list[str]]]]:
    facts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(" | ")]
        if len(parts) != 3:
            raise SystemExit(f"Строка эталона должна быть «раздел | факт | слова»: {line[:80]}")
        groups = [[norm(w) for w in g.split()] for g in parts[2].split(";") if g.strip()]
        facts.append((parts[0], parts[1], groups))
    return facts


def deck_from_result(data: dict) -> tuple[list[str], list[str], list[str]]:
    """(карточки как «вопрос ответ», факты конспекта темы, экраны уроков) из результата конвейера."""
    cards = [norm(f"{c['text']} {c['translation']}") for p in data["packs"].values() for c in p["cards"]]
    screens = [norm(s.get("say", "")) for p in data["packs"].values() if p.get("lesson") for s in p["lesson"].get("screens", [])]
    screens += [norm(s.get("say", "")) for s in ((data.get("intro") or {}).get("screens") or [])]
    facts = [norm(f) for fs in (data.get("facts") or {}).values() for f in fs]
    return cards, facts, screens


def load_deck(path: Path) -> tuple[list[str], list[str], list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "packs" in data:
        return deck_from_result(data)
    items = data.get("cards") if isinstance(data, dict) else data
    return [norm(f"{c.get('text', '')} {c.get('translation', '')}") for c in items if isinstance(c, dict)], [], []


def evaluate(cards: list[str], screens: list[str], facts, sheet: list[str] | None = None) -> dict:
    """facts — эталон, sheet — факты конспекта темы колоды, screens — экраны уроков."""
    sheet = sheet or []
    by = defaultdict(lambda: [0, 0, 0, 0, 0])
    missing = []
    total = [0, 0, 0, 0]
    for sec, label, groups in facts:
        in_card = any(all(w in c for w in g) for g in groups for c in cards)
        in_sheet = any(all(w in f for w in g) for g in groups for f in sheet)
        in_lesson = any(all(w in s for w in g) for g in groups for s in screens)
        anywhere = in_card or in_sheet or in_lesson
        row = by[sec]
        row[0] += 1
        row[1] += in_card
        row[2] += in_sheet
        row[3] += in_lesson
        row[4] += anywhere
        total[0] += in_card
        total[1] += in_sheet
        total[2] += in_lesson
        total[3] += anywhere
        if not anywhere:
            missing.append((sec, label, in_card, in_sheet, in_lesson))
    return {"facts": len(facts), "cards": total[0], "sheet": total[1], "lessons": total[2], "any": total[3],
            "cards_or_sheet": sum(1 for _, _, g in facts if any(all(w in c for w in gr) for gr in g for c in cards + sheet)),
            "by_section": dict(by), "missing": missing}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(args) != 2:
        print(__doc__)
        return 2
    facts = load_gold(Path(args[1]))
    cards, sheet, screens = load_deck(Path(args[0]))
    r = evaluate(cards, screens, facts, sheet)
    n = max(1, r["facts"])
    print(f"фактов в эталоне: {r['facts']}; карточек в колоде: {len(cards)}; фактов конспекта: {len(sheet)}; экранов уроков: {len(screens)}")
    print(f"  спрашивает карточка:     {r['cards']} ({100 * r['cards'] / n:.0f}%)")
    if sheet:
        print(f"  есть в конспекте темы:   {r['sheet']} ({100 * r['sheet'] / n:.0f}%); карточка ИЛИ конспект: {r['cards_or_sheet']} ({100 * r['cards_or_sheet'] / n:.0f}%)")
    if screens:
        print(f"  названо в уроках:        {r['lessons']} ({100 * r['lessons'] / n:.0f}%)")
    print(f"  ХОТЬ ГДЕ-ТО (карточка, конспект или урок): {r['any']} ({100 * r['any'] / n:.0f}%)")
    for sec, (total, c, f, l, a) in sorted(r["by_section"].items()):
        print(f"  §{sec}: фактов {total:2}  карточки {c:2}  конспект {f:2}  уроки {l:2}  хоть где-то {a:2} ({100 * a / total:3.0f}%)")
    if "-v" in sys.argv:
        print("  НЕ НАЙДЕНО НИГДЕ:")
        for sec, label, *_ in r["missing"]:
            print(f"    §{sec} {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
