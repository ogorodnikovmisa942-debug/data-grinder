"""
Тип материала и плотность: учебник, статья/доклад, конспект (шпаргалка, тезисы, слайды), лекция.

Один и тот же способ нарезки не годится всем: в учебнике важных фактов мало на страницу (≈ карточка на страницу),
в шпаргалке почти каждая строка факт, в лекции много воды. Тип приходит от модели вместе с картой (поле source_type, ничего не стоит),
а код считает «пункты фактов» и подстраховывает, если модели ответ не дался. Всё остальное — чистая арифметика, без вызовов ИИ.
"""
import os
import re

KINDS = ("textbook", "article", "notes", "lecture")

# Знаков источника на одну карточку по типу. Учебник — ≈ страница (решение пользователя 2026-10-06); короткая статья или доклад
# плотнее: там почти нет «воды»; лекция реже: много повторов и отступлений. Для конспекта считаем по пунктам (см. target_cards).
# Для учебника: ≈ карточка на страницу. Цена целой книги при такой плотности ≈ 17¢ (замер на «Судоустройстве», 1 млн знаков), при 2000 знаках — уже ≈ 18,6¢.
CHARS_PER_CARD = {"textbook": int(os.getenv("PATH_CHARS_PER_CARD", "2300")), "article": 800, "lecture": 3200, "notes": 600}
# Фактов «Конспекта темы» на одну карточку: остаток текста, которого карточки не спрашивают, идёт готовым списком. Учебник: 2,6 факта на
# карточку = ~1 факт на 900 знаков (замер 2026-10-07: ~13% фактов повторяют карточку и отбрасываются, поэтому просим с запасом).
# В конспекте почти каждый пункт уже карточка — фактов там немного. PATH_FACTS_SCALE меняет всё сразу (0 — без фактов).
FACTS_PER_CARD = {"textbook": 2.6, "article": 1.7, "lecture": 2.2, "notes": 0.4}
FACTS_SCALE = float(os.getenv("PATH_FACTS_SCALE", "1.0"))
NOTES_CARDS_PER_ITEM = 0.85          # конспект: почти по карточке на пункт (часть пунктов — пояснения и повторы)
ARTICLE_MAX_CHARS = 40_000           # короче этого экспозиционный текст считаем статьёй/докладом, а не учебником

_ITEM_SPLIT = re.compile(
    r"\n+|(?<=[.!?;)])\s*(?=\d{3,4}\b\s*[—–-])|(?<=[.!?;)])\s+(?=[А-ЯЁA-Z«\"(])|\s+[•·▪●]\s+|(?<=\.)\s*(?=[А-ЯЁ][а-яё]+\s*[—–-]\s)"
)
_YEAR_DASH = re.compile(r"\b\d{3,4}(?:,\s*\d{3,4})*\s*[—–-]\s")
_TERM_DASH = re.compile(r"(?:^|[.;)\n])\s*[А-ЯЁA-Z][^—–\n.]{1,45}\s[—–]\s")
_SPOKEN = re.compile(r"\b(давайте|итак|как вы видите|как вы понимаете|значит|в общем|смотрите|запишите|обратите внимание|скажем так|ну вот)\b", re.I)


def count_items(text: str) -> int:
    """Сколько «пунктов» в тексте: строки, маркеры, «дата — событие», «термин — пояснение», предложения. Для конспекта это
    число фактов; для учебника не используется."""
    parts = [p.strip() for p in _ITEM_SPLIT.split(text or "") if p and len(p.strip()) > 3]
    return len(parts)


def metrics(text: str) -> dict:
    n = max(1, len(text))
    items = count_items(text)
    return {
        "chars": len(text),
        "items": items,
        "avg_item_chars": round(n / max(1, items)),
        "year_dash_per_1k": round(1000 * len(_YEAR_DASH.findall(text)) / n, 2),
        "term_dash_per_1k": round(1000 * len(_TERM_DASH.findall(text)) / n, 2),
        "spoken_per_1k": round(1000 * len(_SPOKEN.findall(text)) / n, 2),
    }


def heuristic_kind(text: str) -> str:
    """Запасная оценка типа по тексту, когда модель тип не назвала. Осторожная: сомнение — «учебник»/«статья»."""
    m = metrics(text)
    if m["spoken_per_1k"] >= 1.5:
        return "lecture"
    # «дата — событие» или «термин — пояснение» чаще раза на 250 знаков: это перечень фактов
    if m["year_dash_per_1k"] + m["term_dash_per_1k"] >= 4.0 and m["avg_item_chars"] <= 160:
        return "notes"
    return "article" if m["chars"] <= ARTICLE_MAX_CHARS else "textbook"


def resolve_kind(declared: str | None, text: str) -> str:
    """Тип от модели, если он из списка (глава учебника остаётся «учебником» при любом размере: плотность части книги такая же, как у целой);
    иначе по тексту."""
    kind = (declared or "").strip().lower()
    return kind if kind in KINDS else heuristic_kind(text)


def target_cards(kind: str, text: str) -> int:
    """Цель по числу карточек для материала данного типа (ориентир, а не жёсткое число)."""
    chars = len(text)
    if kind == "notes":
        by_items = round(NOTES_CARDS_PER_ITEM * count_items(text))
        return max(1, min(by_items, round(chars / 60)))           # не больше карточки на ~60 знаков: дальше это уже пересказ слов
    return max(1, round(chars / CHARS_PER_CARD.get(kind, CHARS_PER_CARD["textbook"])))


def facts_per_card(kind: str | None) -> float:
    """Сколько фактов конспекта темы приходится на карточку для материала этого типа."""
    return round(FACTS_PER_CARD.get(kind or "", FACTS_PER_CARD["textbook"]) * FACTS_SCALE, 2)


# Подсказка модели в хвосте запроса (статичный системный промпт не меняется, кэш не страдает)
KIND_HINTS = {
    "notes": ("SOURCE TYPE: notes (cheat sheet, theses, slides). Every item is already an exam fact: write a card for nearly every item and keep the "
              "author's items; do not summarize several items into one. The TARGET is close to the number of items."),
    "article": "SOURCE TYPE: short article or report. It has little filler: most paragraphs carry a fact worth a card.",
    "lecture": ("SOURCE TYPE: lecture. It repeats itself and digresses: take the substance once; the lecturer's stress phrases "
                "(\"write this down\", \"this is important\", \"this will be on the exam\") mark the most important facts."),
}


CARDS_PER_NODE = 4              # узел — связный кусок, который несёт ~4 карточки: меньше — плоская колода и дорогие уроки, больше — узел «кашей»


def node_budget(target_cards: int) -> dict:
    """Сколько узлов просить у модели при данной цели по карточкам: всего ≈ цель / 4, ярусы пропорционально.
    Замер 2026-10-07: без этого карта давала узлов почти столько же, сколько карточек (36 узлов на 36 карточек), а у книги в 180 узлов
    урок на каждом стоил 4,5¢. Малый материал получает малую карту (шпаргалка на страницу — около 9 узлов)."""
    total = max(6, min(150, round(target_cards / CARDS_PER_NODE)))
    t0 = max(1, min(8, round(0.10 * total)))
    t1 = max(2, min(10, round(0.14 * total)))
    t3 = 0 if total < 8 else max(1, min(12, round(0.08 * total)))
    t2 = max(2, total - t0 - t1 - t3)
    return {"total": t0 + t1 + t2 + t3, "tier0": t0, "tier1": t1, "tier2": t2, "tier3": t3}


def kind_hint(kind: str | None) -> str:
    return KIND_HINTS.get(kind or "", "")


__all__ = ["KINDS", "CHARS_PER_CARD", "FACTS_PER_CARD", "facts_per_card", "CARDS_PER_NODE", "node_budget", "count_items", "metrics", "heuristic_kind", "resolve_kind", "target_cards", "kind_hint"]
