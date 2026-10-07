"""
Качество карточек: проверка по книге, выдержки для уроков, поиск непокрытых участков (всё считает программа, без вызовов ИИ).

Зачем. Урок теперь пишется ИЗ карточек, значит карточка обязана быть верной и полной: урок её не исправит.
Каждая карточка приходит с цитатой `ev` (6-14 слов подряд из книги). Программа:
  1. находит цитату в книге точно (без учёта регистра, пробелов, знаков и переносов) и запоминает место;
  2. проверяет, что ответ карточки (слова и числа) действительно стоит рядом с этим местом;
  3. если цитата не нашлась или ответ расходится с ней, помечает карточку «подозрительной» — её проверяет отдельный запрос
     по найденному куску книги (исправить или отбросить);
  4. по местам карточек видит участки книги, на которые не пришлось ни одной карточки (их добирает отдельный запрос).
"""
import bisect
import re
from array import array
from collections import Counter

from .coverage import SourceIndex, join_wrapped, stems

FACT_SHARE_MIN = 0.7          # доля значимых слов факта конспекта в самом похожем окне книги (числа обязаны там быть)
FACT_BLOCK_MARGIN = 300       # запас вокруг блока, где ещё ищем опору факта
FACT_CARD_SHARE = 0.75        # факт, слова которого почти целиком уже в вопросе и ответе карточки узла, повторяет карточку
FACT_DUP_JACCARD = 0.7
EV_MARGIN = 150               # запас вокруг цитаты, где ищем слова ответа
EXACT_ANSWER_MIN = 0.6        # доля значимых слов ответа рядом с точно найденной цитатой
WINDOW_ANSWER_MIN = 0.75      # то же по всему окну (там проверка мягче, поэтому порог выше)
LEXICAL_TOP_WINDOWS = 3        # без цитаты ответ ищем в нескольких самых похожих окнах, а не в одном
FUZZY_EVIDENCE_MIN = 0.8      # доля слов цитаты в одном окне, чтобы считать цитату «почти найденной»
MIN_EVIDENCE_STEMS = 4
MIN_QUOTE_ALNUM = 14          # цитата короче — слишком общая, точное совпадение ничего не доказывает
PASSAGE_CHARS = 1500          # кусок книги, который видит проверяющий запрос
FOCUS_CHARS = 450             # выдержка к уроку для карточки без найденной цитаты: лучшее предложение с соседями
EXCERPT_MARGINS = (350, 200, 100, 0)
MIN_THIN_SPAN_CHARS = 4500    # участок без карточек короче одного окна — не считаем дырой
FILL_SPAN_CHARS = 12000
COVER_RADIUS = 150            # абзацы дальше этого от цитаты любой карточки считаются «нетронутыми»
MIN_HOLE_CHARS = 700          # нетронутый кусок короче — не абзац, а обрывок
HOLE_PASSAGE_CHARS = 3500     # на один запрос добора идёт кусок не длиннее этого
_FOOTNOTE_LINE = re.compile(r"^\s*\d{1,3}\s+[А-ЯЁA-Z][^\n]{0,160}(?:\bС\.\s?\d|//|Режим доступа|\bМ\.,|\bСПб\.|\bМинск,)[^\n]*$", re.M)

_NUM = re.compile(r"\d+")
_SENT_END = re.compile(r"(?<=[.!?…])\s+")


def _fold(text: str) -> str:
    """Буквы и цифры любого алфавита в нижнем регистре, «ё» = «е»; всё остальное выбрасывается."""
    return "".join(c.lower().replace("ё", "е") for c in (text or "") if c.isalnum())


class SourceLocator:
    """Точный поиск цитаты в книге с возвратом позиции в исходном тексте."""

    def __init__(self, text: str):
        self.text = text
        chars: list[str] = []
        pos = array("I")
        for i, ch in enumerate(text):
            if ch.isalnum():
                for c in ch.lower().replace("ё", "е"):
                    chars.append(c)
                    pos.append(i)
        self._s = "".join(chars)
        self._pos = pos

    def find(self, quote: str) -> tuple[int, int] | None:
        q = _fold(quote)
        if len(q) < MIN_QUOTE_ALNUM:
            return None
        i = self._s.find(q)
        if i < 0:
            return None
        return self._pos[i], self._pos[i + len(q) - 1] + 1


def _answer_share(answer: str, region: str) -> float:
    """Какая доля значимых слов ответа стоит в куске книги. Числа обязательны: расхождение в цифре — сразу 0."""
    nums = set(_NUM.findall(answer or ""))
    if nums and not nums <= set(_NUM.findall(region)):
        return 0.0
    words = set(stems(answer))
    if not words:
        return 1.0
    have = set(stems(region))
    return sum(1 for w in words if w in have) / len(words)


class CardVerifier:
    """Сверяет карточки с книгой. Строится один раз на книгу (индекс и поиск цитат — единственная заметная работа)."""

    def __init__(self, text: str, index: SourceIndex | None = None):
        self.text = text
        self.index = index or SourceIndex(text)
        self.locator = SourceLocator(text)
        self._starts = [a for a, _ in self.index.spans]
        self._win_stems: list[set[str]] | None = None
        self._substantive: dict[int, bool] = {}
        self._block_cache: dict[tuple[int, int], tuple[set[str], set[str]]] = {}

    # --- окна -----------------------------------------------------------

    def _stems_of_windows(self) -> list[set[str]]:
        if self._win_stems is None:
            self._win_stems = [set(v) for v in self.index.vecs]
        return self._win_stems

    def window_of(self, pos: int) -> int:
        return max(0, bisect.bisect_right(self._starts, pos) - 1)

    def _top_windows(self, query_text: str, k: int = 1) -> list[tuple[int, float]]:
        """k самых похожих на запрос окон книги: [(индекс окна, сходство)], лучшее первым; ничего похожего — пусто."""
        q = self.index.query(query_text)
        scored = []
        for i, wv in enumerate(self.index.vecs):
            if not self.substantive(i):          # оглавление похоже на названия тем, но фактов в нём нет
                continue
            s = sum(x * wv.get(w, 0.0) for w, x in q.items())
            if s > 0.0:
                scored.append((s, i))
        scored.sort(reverse=True)
        return [(i, s) for s, i in scored[:k]]

    def _best_window(self, query_text: str) -> tuple[int, float]:
        top = self._top_windows(query_text, 1)
        return top[0] if top else (-1, 0.0)

    def _subwindow(self, win: int, query_text: str, width: int = PASSAGE_CHARS, step: int = 400) -> tuple[int, int]:
        """Самый «похожий» на запрос отрезок окна шириной ~width знаков."""
        a, b = self.index.spans[win]
        want = {w: self.index.idf.get(w, 1.0) for w in stems(query_text)}
        best, best_pos, pos = -1.0, a, a
        while True:
            end = min(b, pos + width)
            have = set(stems(self.text[pos:end]))
            score = sum(v for w, v in want.items() if w in have)
            if score > best:
                best, best_pos = score, pos
            if end >= b:
                break
            pos += step
        return best_pos, min(b, best_pos + width)

    # --- проверка карточки -------------------------------------------------

    def check(self, card: dict) -> dict:
        """Статус карточки: grounded (цитата найдена точно, ответ рядом) / near (цитата почти найдена) /
        lexical (цитаты нет, но слова и числа ответа есть в самом похожем окне) / unchecked (книгу не проверить) /
        flagged (расхождение — нужна проверка по куску книги). Возвращает {"status", "span"|"window", "reason"?}."""
        answer = card.get("translation") or ""
        evidence = card.get("evidence") or ""
        span = self.locator.find(evidence) if evidence else None
        if span:
            a, b = span
            region = self.text[max(0, a - EV_MARGIN):b + EV_MARGIN]
            if _answer_share(answer, region) >= EXACT_ANSWER_MIN:
                return {"status": "grounded", "span": span}
            return {"status": "flagged", "span": span, "reason": "ответ не стоит рядом с цитатой"}
        if not self.index.usable:
            return {"status": "unchecked"}

        ev_stems = set(stems(evidence))
        if len(ev_stems) >= MIN_EVIDENCE_STEMS:
            win_stems = self._stems_of_windows()
            share, win = max(((sum(1 for w in ev_stems if w in ws) / len(ev_stems), i) for i, ws in enumerate(win_stems)))
            if share >= FUZZY_EVIDENCE_MIN:
                a, b = self.index.spans[win]
                if _answer_share(answer, self.text[a:b]) >= WINDOW_ANSWER_MIN:
                    return {"status": "near", "window": win}
                return {"status": "flagged", "window": win, "reason": "ответ не найден рядом с цитатой"}

        both = self._top_windows(f"{card.get('text', '')} {answer}", LEXICAL_TOP_WINDOWS)
        only_answer = self._top_windows(answer, 2) if answer.strip() else []
        candidates = list(dict.fromkeys(i for i, _ in both + only_answer))
        for win in candidates:
            a, b = self.index.spans[win]
            if _answer_share(answer, self.text[a:b]) >= WINDOW_ANSWER_MIN:
                return {"status": "lexical", "window": win}
        if candidates:
            return {"status": "flagged", "window": candidates[0],
                    "reason": "слов или чисел ответа нет в книге рядом с темой вопроса"}
        return {"status": "flagged", "reason": "в книге нет места, похожего на вопрос"}

    def verify(self, cards: list[dict]) -> dict:
        """Помечает карточки: card["support"] (статус), card["src_span"] / card["src_window"] (где в книге).
        Возвращает сводку и список подозрительных карточек."""
        counts: Counter = Counter()
        flagged: list[dict] = []
        for c in cards:
            res = self.check(c)
            c["support"] = res["status"]
            c.pop("src_span", None)
            c.pop("src_window", None)
            if "span" in res:
                c["src_span"] = [res["span"][0], res["span"][1]]
                c["src_window"] = self.window_of(res["span"][0])
            elif "window" in res:
                c["src_window"] = res["window"]
            if res["status"] == "flagged":
                c["support_reason"] = res.get("reason")
                flagged.append(c)
            counts[res["status"]] += 1
        return {"cards": len(cards), **{k: counts.get(k, 0) for k in ("grounded", "near", "lexical", "unchecked", "flagged")}}, flagged

    # --- факты конспекта темы ---------------------------------------------

    def check_fact(self, fact: str) -> int | None:
        """Окно книги, в котором стоят слова и числа факта (без вызова ИИ); None — опоры в книге нет.
        Короткий текст (окон слишком мало для сравнения): проверка по тексту целиком, ответ 0."""
        words = set(stems(fact))
        if not words:
            return None
        nums = set(_NUM.findall(fact))
        if not self.index.usable:
            if nums and not nums <= set(_NUM.findall(self.text)):
                return None
            have = set(stems(self.text))
            return 0 if sum(1 for w in words if w in have) / len(words) >= FACT_SHARE_MIN else None
        win_stems = self._stems_of_windows()
        for win, _ in self._top_windows(fact, LEXICAL_TOP_WINDOWS):
            a, b = self.index.spans[win]
            if nums and not nums <= set(_NUM.findall(self.text[a:b])):
                continue
            if sum(1 for w in words if w in win_stems[win]) / len(words) >= FACT_SHARE_MIN:
                return win
        return None

    def fact_supported_in(self, fact: str, a: int, b: int, margin: int = FACT_BLOCK_MARGIN) -> bool:
        """Слова и числа факта стоят в блоке книги [a, b) (с запасом по краям: факт может опираться на соседнюю фразу)."""
        words = set(stems(fact))
        if not words:
            return False
        key = (a, b)
        if key not in self._block_cache:
            region = self.text[max(0, a - margin):min(len(self.text), b + margin)]
            self._block_cache[key] = (set(stems(region)), set(_NUM.findall(region)))
        region_stems, region_nums = self._block_cache[key]
        nums = set(_NUM.findall(fact))
        if nums and not nums <= region_nums:
            return False
        return sum(1 for w in words if w in region_stems) / len(words) >= FACT_SHARE_MIN

    # --- куски книги -----------------------------------------------------

    def passage_range(self, card: dict, max_chars: int = PASSAGE_CHARS) -> tuple[int, int] | None:
        """Где в книге должен стоять ответ карточки: диапазон знаков (для проверки и для выдержки к уроку)."""
        span = card.get("src_span")
        query = f"{card.get('text', '')} {card.get('translation', '')}"
        if span:
            half = max(200, (max_chars - (span[1] - span[0])) // 2)
            return max(0, span[0] - half), min(len(self.text), span[1] + half)
        if not self.index.usable:
            return None
        win = card.get("src_window")
        if win is None:
            win, _ = self._best_window(query)
            if win < 0:
                return None
        return self._subwindow(win, query, max_chars)

    def focus_range(self, card: dict, max_chars: int = FOCUS_CHARS) -> tuple[int, int] | None:
        """Самое подходящее к карточке предложение книги вместе с соседними (до max_chars знаков)."""
        win = card.get("src_window")
        query = f"{card.get('text', '')} {card.get('translation', '')}"
        if not self.index.usable:
            return None
        if win is None:
            win, _ = self._best_window(query)
            if win < 0:
                return None
        a, b = self.index.spans[win]
        text = self.text[a:b]
        cuts = [0] + [m.end() for m in _SENT_END.finditer(text)] + [len(text)]
        sents = [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1) if cuts[i + 1] > cuts[i]]
        if not sents:
            return None
        want = {w: self.index.idf.get(w, 1.0) for w in stems(query)}
        scores = [sum(want[w] for w in set(stems(text[x:y])) if w in want) for x, y in sents]
        best = max(range(len(sents)), key=lambda i: scores[i])
        lo = hi = best
        while True:                                   # добавляем соседа с большим весом, пока помещаемся
            opts = []
            if lo > 0:
                opts.append((scores[lo - 1], lo - 1, hi))
            if hi < len(sents) - 1:
                opts.append((scores[hi + 1], lo, hi + 1))
            opts = [o for o in opts if sents[o[2]][1] - sents[o[1]][0] <= max_chars]
            if not opts:
                break
            _, lo, hi = max(opts)
        return a + sents[lo][0], a + min(sents[hi][1], sents[lo][0] + max_chars)

    def passage(self, card: dict, max_chars: int = PASSAGE_CHARS) -> str:
        rng = self.passage_range(card, max_chars)
        return _clean_cut(self.text, rng[0], rng[1]) if rng else ""

    def excerpt(self, cards: list[dict], max_chars: int) -> str:
        """Выдержка для урока узла: куски книги вокруг мест, откуда взяты его карточки (слитые и ужатые до max_chars)."""
        ranges: list[tuple[int, int]] = []
        for c in cards:
            sp = c.get("src_span")
            if sp:
                ranges.append((sp[0], sp[1]))
            else:
                rng = self.focus_range(c)
                if rng:
                    ranges.append(rng)
        if not ranges:
            return ""
        merged: list[tuple[int, int]] = []
        for margin in EXCERPT_MARGINS:
            padded = sorted((max(0, a - margin), min(len(self.text), b + margin)) for a, b in ranges)
            merged = []
            for a, b in padded:
                if merged and a <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], b))
                else:
                    merged.append((a, b))
            if sum(b - a for a, b in merged) <= max_chars:
                break
        total = sum(b - a for a, b in merged)
        if total > max_chars:                                  # слишком много разных мест: делим бюджет поровну
            each = max(200, max_chars // len(merged))
            merged = [(a, min(b, a + each)) for a, b in merged]
        return "\n…\n".join(_clean_cut(self.text, a, b) for a, b in merged)

    # --- непокрытые участки ------------------------------------------------

    def substantive(self, win: int) -> bool:
        """Окно похоже на учебный текст, а не на оглавление, список литературы или таблицу."""
        if win in self._substantive:
            return self._substantive[win]
        a, b = self.index.spans[win]
        t = self.text[a:b]
        n = max(1, len(t))
        letters = sum(1 for c in t if c.isalpha())
        digits = sum(1 for c in t if c.isdigit())
        ok = (letters / n >= 0.6 and digits / n <= 0.06 and t.count("....") <= 5
              and len(stems(t)) >= min(200, int(0.04 * len(t))))
        self._substantive[win] = ok
        return ok

    def card_windows(self, cards: list[dict]) -> Counter:
        counts: Counter = Counter()
        for c in cards:
            if c.get("src_window") is not None:
                counts[c["src_window"]] += 1
        return counts

    def _ignored_ranges(self) -> list[tuple[int, int]]:
        """Сноски и библиографические строки: карточки по ним не нужны."""
        return [(m.start(), m.end()) for m in _FOOTNOTE_LINE.finditer(self.text)]

    def uncovered_passages(self, cards: list[dict], radius: int = COVER_RADIUS, min_chars: int = MIN_HOLE_CHARS,
                           max_chars: int = HOLE_PASSAGE_CHARS) -> list[dict]:
        """Абзацы книги, которых не коснулась ни одна карточка. «Коснулась» — цитата карточки лежит не дальше radius знаков
        (для карточек без найденной цитаты берём лучшее предложение книги к ней). Сноски и оглавление не в счёт.
        Нетронутый кусок режется на части не длиннее max_chars по границам предложений. Возвращает по порядку в книге."""
        n = len(self.text)
        covered: list[tuple[int, int]] = list(self._ignored_ranges())
        for c in cards:
            sp = c.get("src_span")
            if sp:
                covered.append((max(0, sp[0] - radius), min(n, sp[1] + radius)))
            else:
                rng = self.focus_range(c) if self.index.usable else None
                if rng:
                    covered.append((max(0, rng[0] - radius), min(n, rng[1] + radius)))
        covered.sort()
        holes: list[tuple[int, int]] = []
        pos = 0
        for a, b in covered:
            if a > pos:
                holes.append((pos, a))
            pos = max(pos, b)
        if pos < n:
            holes.append((pos, n))
        out: list[dict] = []
        for a, b in holes:
            if b - a < min_chars:
                continue
            for x, y in self._split_by_sentences(a, b, max_chars):
                chunk = self.text[x:y]
                letters = sum(1 for ch in chunk if ch.isalpha())
                digits = sum(1 for ch in chunk if ch.isdigit())
                size = max(1, len(chunk))
                if (y - x >= min_chars and letters / size >= 0.6 and digits / size <= 0.06 and chunk.count("....") <= 3
                        and len(stems(chunk)) >= min(150, int(0.04 * size))):
                    out.append({"start": x, "end": y, "chars": y - x})
        return out

    def _split_by_sentences(self, a: int, b: int, max_chars: int) -> list[tuple[int, int]]:
        """Режет [a, b) на части не длиннее max_chars по концам предложений (без обрыва слов)."""
        parts, start = [], a
        if a > 0 and self.text[a - 1] not in ".!?\n":                 # начало с середины предложения: сдвигаем к началу следующего
            m = re.search(r"[.!?…][\"»)]?\s+(?=[А-ЯЁA-Z«\"(\d])", self.text[a:min(b, a + 400)])
            if m:
                start = a + m.end()
        while start < b:
            end = min(b, start + max_chars)
            if end < b:
                cut = max(self.text.rfind(". ", start + max_chars // 2, end), self.text.rfind(".\n", start + max_chars // 2, end))
                if cut != -1:
                    end = cut + 1
            parts.append((start, end))
            start = end
        return parts

    def thin_spans(self, cards: list[dict], max_span_chars: int = FILL_SPAN_CHARS) -> list[dict]:
        """Подряд идущие окна книги без единой карточки (учебный текст, не короче MIN_THIN_SPAN_CHARS), от больших к малым."""
        if not self.index.usable:
            return []
        counts = self.card_windows(cards)
        runs: list[list[int]] = []
        run: list[int] = []
        for i in range(len(self.index.spans)):
            if counts.get(i, 0) == 0 and self.substantive(i):
                run.append(i)
            elif run:
                runs.append(run)
                run = []
        if run:
            runs.append(run)
        out: list[dict] = []
        for r in runs:
            chunk: list[int] = []
            size = 0
            for i in r + [None]:
                width = (self.index.spans[i][1] - self.index.spans[i][0]) if i is not None else 0
                if i is None or (chunk and size + width > max_span_chars):
                    if chunk and size >= MIN_THIN_SPAN_CHARS:
                        out.append({"start": self.index.spans[chunk[0]][0], "end": self.index.spans[chunk[-1]][1],
                                    "chars": size, "windows": chunk})
                    chunk, size = [], 0
                if i is not None:
                    chunk.append(i)
                    size += width
        out.sort(key=lambda s: -s["chars"])
        return out

    def suggest_node(self, nodes: list[dict], span_text: str) -> str | None:
        """Узел карты, чьи название и описание больше всего похожи на участок книги (для карточек, добранных на нём)."""
        carriers = [n for n in nodes if n["tier"] < 3]
        if not carriers or not self.index.usable:
            return carriers[0]["key"] if carriers else None
        sv = self.index.query(span_text)
        best, best_key = 0.0, carriers[0]["key"]
        for n in carriers:
            q = self.index.query(f"{n['name']} {n['name']} {n['name']} {n.get('summary') or ''}")
            s = sum(x * sv.get(w, 0.0) for w, x in q.items()) * (1 + 0.25 * n["tier"])
            if s > best:
                best, best_key = s, n["key"]
        return best_key


def _clean_cut(text: str, a: int, b: int) -> str:
    """Вырезка без обрыва слов по краям, пробелы сжаты."""
    a, b = max(0, a), min(len(text), b)
    if a > 0 and text[a - 1].isalnum():
        nxt = text.find(" ", a, a + 40)
        if nxt != -1:
            a = nxt + 1
    if b < len(text) and text[b].isalnum():
        prv = text.rfind(" ", max(a, b - 40), b)
        if prv != -1:
            b = prv
    return re.sub(r"\s+", " ", join_wrapped(text[a:b])).strip()


def dedupe_cards(cards_by_node: dict[str, list[dict]], order: list[str] | None = None) -> int:
    """Убирает повторы между узлами: одинаковый вопрос или почти одинаковые вопрос и ответ. Остаётся первая карточка
    (узлы обходятся в порядке `order`). Возвращает число удалённых."""
    seen_fronts: set[str] = set()
    kept: list[tuple[set[str], set[str]]] = []
    removed = 0
    for key in (order or list(cards_by_node)):
        survivors = []
        for c in cards_by_node.get(key) or []:
            front = _fold(c["text"])
            q, a = set(stems(c["text"])), set(stems(c["translation"]))
            dup = front in seen_fronts
            if not dup and q:
                for kq, ka in kept:
                    if _jaccard(q, kq) >= 0.7 and _jaccard(a, ka) >= 0.7:
                        dup = True
                        break
            if dup:
                removed += 1
                continue
            seen_fronts.add(front)
            kept.append((q, a))
            survivors.append(c)
        if key in cards_by_node:
            cards_by_node[key] = survivors
    return removed


def process_facts(verifier: "CardVerifier", raw_facts: list[dict], blocks: list[tuple[int, int]], matcher,
                  cards_by_node: dict[str, list[dict]], order: list[str]) -> tuple[dict[str, list[str]], dict]:
    """Факты «Конспекта темы» перед сохранением. raw_facts: [{"t": факт, "blk": номер блока книги, "seq": номер в ответе}].
    Отбрасываем: факты без опоры в книге (слов и чисел нет в своём блоке и вообще нигде похожем), повторяющие карточку того же
    узла или соседнего места, повторяющие соседний факт. Оставшиеся относим к узлу (matcher) и ставим в порядке книги.
    Возвращает ({узел: [факты]}, отчёт)."""
    import bisect
    report = {"received": len(raw_facts), "unsupported": 0, "repeat_card": 0, "repeat_fact": 0, "kept": 0}
    starts = [a for a, _ in blocks]
    cards_by_key: dict[str, list[set[str]]] = {}
    cards_by_blk: dict[int, list[set[str]]] = {}
    for key in order:
        for c in cards_by_node.get(key) or []:
            words = set(stems(f"{c['text']} {c['translation']}"))
            cards_by_key.setdefault(key, []).append(words)
            sp = c.get("src_span")
            if sp:
                cards_by_blk.setdefault(max(0, bisect.bisect_right(starts, sp[0]) - 1), []).append(words)
    seen: dict[int, list[set[str]]] = {}
    placed: dict[str, list[tuple[int, int, str]]] = {}
    for f in sorted(raw_facts, key=lambda f: (f["blk"], f["seq"])):
        text, blk = f["t"], f["blk"]
        words = set(stems(text))
        a, b = blocks[blk]
        win = verifier.window_of((a + b) // 2)
        key = matcher.best(text, win) if matcher else None
        near_cards = [w for k in (blk - 1, blk, blk + 1) for w in cards_by_blk.get(k, [])] + cards_by_key.get(key, [])
        if words and any(len(words & cw) / len(words) >= FACT_CARD_SHARE for cw in near_cards):
            report["repeat_card"] += 1
            continue
        if words and any(_jaccard(words, w) >= FACT_DUP_JACCARD for k in range(blk - 2, blk + 3) for w in seen.get(k, [])):
            report["repeat_fact"] += 1
            continue
        if not verifier.fact_supported_in(text, a, b) and verifier.check_fact(text) is None:
            report["unsupported"] += 1
            continue
        seen.setdefault(blk, []).append(words)
        placed.setdefault(key or (order[0] if order else ""), []).append((blk, f["seq"], text))
    out: dict[str, list[str]] = {}
    for key, items in placed.items():
        out[key] = [t for _, _, t in sorted(items)]
        report["kept"] += len(items)
    return out, report


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


__all__ = ["CardVerifier", "process_facts", "COVER_RADIUS", "SourceLocator", "dedupe_cards", "FILL_SPAN_CHARS", "PASSAGE_CHARS"]
