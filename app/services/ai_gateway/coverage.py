"""
Охват источника: сколько книги приходится на каждый узел карты и насколько плотно куски книги покрыты карточками.

Всё считается программой (TF-IDF по стеммам), без вызовов ИИ и без затрат. Модуль чистый и не зависит от языка
в смысле разметки: учебнику не нужны заголовки, текст режется на окна по ~5 тыс. знаков.
Для текстов без букв (иероглифы и т. п.) расчёт не применяется — вызывающий код откатывается на фиксированные квоты.
"""
import math
import re
from collections import Counter

WINDOW_CHARS = 5000
STEM_LEN = 6
MIN_WORD_LEN = 4
MIN_MATCH_SCORE = 0.04          # окно, не похожее ни на один узел, не засчитываем никому
MIN_TOKENS_FOR_INDEX = 400      # меньше — считать нечего
MAX_CARDS_PER_NODE = 12         # потолок квоты на один узел

_WORD_RE = re.compile(r"[^\W\d_]{%d,}" % MIN_WORD_LEN)
_STOP = set(
    "это как что для при или так его ее их был была были быть есть может могут также если когда только который которые "
    "которая которого которой которых этом этой этих тех той тот такой такие вправе должен должны более менее между "
    "после перед через также данный данная данные того тому свой своей своих осуществляет осуществляется "
    "the and that this with from have been which their there are was were not but can will".split()
)


# Перенос слова на конце строки в тексте из PDF: «обще-\nственные». Склеиваем, иначе слово распадается на два обрывка
_WRAP_HYPHEN = re.compile(r"(?<=[^\W\d_])-[ \t]*\n[ \t]*(?=[^\W\d_])")


def join_wrapped(text: str) -> str:
    return _WRAP_HYPHEN.sub("", text or "")


def stems(text: str) -> list[str]:
    return [w[:STEM_LEN] for w in _WORD_RE.findall(join_wrapped(text).lower().replace("ё", "е")) if w not in _STOP]


def split_windows(text: str, size: int = WINDOW_CHARS) -> list[tuple[int, int]]:
    """Окна [start, end) по ~size знаков, границы — по концам строк, чтобы не рвать слова."""
    spans, start, n = [], 0, len(text)
    while start < n:
        end = min(n, start + size)
        if end < n:
            cut = text.rfind("\n", start + size // 2, end + size // 4)
            end = cut + 1 if cut != -1 else end
        spans.append((start, end))
        start = end
    return spans


class SourceIndex:
    """Окна источника с TF-IDF-векторами."""

    def __init__(self, text: str, size: int = WINDOW_CHARS):
        self.text = text
        self.spans = split_windows(text, size)
        tf = [Counter(stems(text[a:b])) for a, b in self.spans]
        self.tokens = sum(sum(c.values()) for c in tf)
        df = Counter(w for c in tf for w in c)
        n = max(1, len(tf))
        self.idf = {w: math.log(n / (1 + k)) + 1.0 for w, k in df.items()}
        self.vecs = [self._vec(c) for c in tf]

    @property
    def usable(self) -> bool:
        return self.tokens >= MIN_TOKENS_FOR_INDEX and len(self.spans) >= 3

    def _vec(self, counter: Counter) -> dict[str, float]:
        v = {w: (1 + math.log(k)) * self.idf.get(w, 1.0) for w, k in counter.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    def query(self, text: str) -> dict[str, float]:
        return self._vec(Counter(stems(text)))

    def best_matches(self, queries: list[dict[str, float]]) -> list[tuple[int, float]]:
        """Для каждого окна — индекс самого похожего запроса и сходство с ним ((-1, 0) если ничто не подошло)."""
        out = []
        for wv in self.vecs:
            best_i, best_s = -1, 0.0
            for i, q in enumerate(queries):
                s = sum(x * wv.get(w, 0.0) for w, x in q.items())
                if s > best_s:
                    best_i, best_s = i, s
            out.append((best_i, best_s) if best_s >= MIN_MATCH_SCORE else (-1, 0.0))
        return out


SOFT_TOP_K = 3            # окно делится между лучшими узлами...
SOFT_MIN_SHARE = 0.7      # ...если их сходство не ниже 70% от лучшего
TIER_BONUS = 0.25         # конкретные узлы (подтемы) выигрывают у общих понятий, которые «похожи на всё»


def node_source_sizes(index: SourceIndex, nodes: list[dict], weights: list[float] | None = None) -> dict[str, int]:
    """Сколько знаков источника «принадлежит» каждому узлу. Окно делится поровну между узлами, чьи название и описание
    на него похожи (победитель-забирает-всё отдавал общим понятиям вроде «Правоотношение» по 50–60 тыс. знаков).
    Кейсы на различение (ярус 3) не участвуют: это виньетки поверх подтем, а не отдельные куски книги.
    weights — вес каждого окна (насыщенность × новизна): «знаки» считаются взвешенными, поэтому карточки достаются насыщенным
    и новым кускам, а не воде и уже покрытому."""
    carriers = [n for n in nodes if n["tier"] < 3]
    queries = [index.query(f"{n['name']} {n['name']} {n['name']} {n.get('summary') or ''}") for n in carriers]
    sizes = {n["key"]: 0.0 for n in nodes}
    for pos, (wv, (a, b)) in enumerate(zip(index.vecs, index.spans)):
        scored = []
        for i, q in enumerate(queries):
            s = sum(x * wv.get(w, 0.0) for w, x in q.items())
            scored.append((s * (1 + TIER_BONUS * carriers[i]["tier"]), i))
        scored.sort(reverse=True)
        best = scored[0][0] if scored else 0.0
        if best < MIN_MATCH_SCORE:
            continue
        top = [i for s, i in scored[:SOFT_TOP_K] if s >= SOFT_MIN_SHARE * best]
        share = (b - a) * (weights[pos] if weights else 1.0)
        for i in top:
            sizes[carriers[i]["key"]] += share / len(top)
    return {k: int(v) for k, v in sizes.items()}


def card_quotas(nodes: list[dict], sizes: dict[str, int], chars_per_card: int = 2300,
                min_cards: int = 3, max_cards: int | None = MAX_CARDS_PER_NODE, case_cards: int = 3, total_cap: int | None = None) -> dict[str, int]:
    """Сколько карточек просить на узел: пропорционально куску источника, не меньше min_cards и (если задано) не больше max_cards.
    Кейсы на различение (ярус 3) — практические виньетки, их число от объёма не зависит."""
    quotas: dict[str, int] = {}
    for n in nodes:
        if n["tier"] == 3:
            quotas[n["key"]] = case_cards
        else:
            want = round(sizes.get(n["key"], 0) / chars_per_card)
            quotas[n["key"]] = max(min_cards, min(max_cards, want) if max_cards else want)
    if total_cap and sum(quotas.values()) > total_cap:
        # Сжимаем пропорционально, не опускаясь ниже минимума
        k = total_cap / sum(quotas.values())
        for key in quotas:
            quotas[key] = max(min_cards if quotas[key] >= min_cards else quotas[key], int(quotas[key] * k))
    return quotas


_DEF_RE = re.compile(r"(—\s+это\b|называется|называют|понимается|понимают|представляет собой|определяется как|является\b|означает|заключается в)", re.I)
_ENUM_RE = re.compile(r"(;|во-первых|во-вторых|в-третьих|\b[а-е]\)|\b\d\)|следующ\w+:|включа\w+:)", re.I)
_DASH_RE = re.compile(r"\s[—–]\s")
_REF_RE = re.compile(r"(\bС\.\s?\d+|\[\d+\]|\bсм\.\s|\bURL\b|[А-ЯЁ][а-яё]+,\s*[А-ЯЁ]\.\s*[А-ЯЁ]\.|\bМ\.:|\bМинск[,:]|\bISBN\b|©)")
WEIGHT_MIN, WEIGHT_MAX = 0.35, 2.2
ANCHOR_MIN_CONTAINMENT = 0.75                          # какая доля (по весу) значимых слов карточки есть в окне, чтобы считать карточку «из этого окна»
ANCHOR_MIN_MARGIN = 0.10                              # и насколько лучше она ложится в это окно, чем в следующее по порядку (иначе — общая лексика)
_TOC_RE = re.compile(r"\.{4,}\s*\d{1,4}")
# Сигналы важности из самой книги: итоги главы концентрируют главное, вопросы для самоконтроля и задания — не учебный текст
_SUMMARY_HEAD = re.compile(r"^\s*(выводы?|резюме|заключение|итоги|краткие выводы|основные (?:положения|понятия|выводы))\s*[:.]?\s*$", re.I | re.M)
_QUESTIONS_HEAD = re.compile(r"^\s*(вопросы для (?:самоконтроля|повторения|обсуждения|самопроверки)|контрольные вопросы|вопросы и задания|тестовые задания|задания для самостоятельной)", re.I | re.M)
SUMMARY_BONUS = 0.5
QUESTIONS_FACTOR = 0.5
COVERED_FLOOR = 0.15                                  # даже покрытый кусок оставляем с небольшим весом: новый автор может сказать иначе


def window_saturation(index: "SourceIndex") -> list[float]:
    """«Насыщенность» каждого окна источника: определения, числа, перечисления и «термин — пояснение» повышают вес, ссылки и
    библиография понижают. Вес ~1 — обычное окно; страницы воды получают меньше, страницы с пятью понятиями больше.
    Сумма весов, нормированная на длину, равна 1: общее число карточек от них не меняется, меняется только кому они достаются."""
    raw: list[float] = []
    for a, b in index.spans:
        chunk = index.text[a:b]
        k = max(1.0, (b - a) / 1000.0)
        defs = len(_DEF_RE.findall(chunk)) / k
        nums = len(re.findall(r"\d+", chunk)) / k
        enum = len(_ENUM_RE.findall(chunk)) / k
        dash = len(_DASH_RE.findall(chunk)) / k
        refs = len(_REF_RE.findall(chunk)) / k
        if len(_TOC_RE.findall(chunk)) >= 3:                      # оглавление: точки и номера страниц — не содержание
            raw.append(0.1)
            continue
        score = max(0.1, 0.6 + 0.9 * defs + 0.12 * nums + 0.25 * enum + 0.2 * dash - 0.35 * refs)
        if _SUMMARY_HEAD.search(chunk):
            score += SUMMARY_BONUS
        if _QUESTIONS_HEAD.search(chunk):
            score = max(0.1, score * QUESTIONS_FACTOR)
        raw.append(score)
    total_len = sum(b - a for a, b in index.spans) or 1
    mean = sum(r * (b - a) for r, (a, b) in zip(raw, index.spans)) / total_len or 1.0
    return [min(WEIGHT_MAX, max(WEIGHT_MIN, r / mean)) for r in raw]


def window_novelty(index: "SourceIndex", card_texts: list[str]) -> list[float]:
    """Какие окна нового материала УЖЕ покрыты карточками курса: 1 — новое, 0 — покрыто. Карточка «принадлежит» окну, если почти все её
    значимые слова (по весу IDF) есть в этом окне и заметно меньше в остальных (калибровка на реальных книгах: одна и та же книга
    даёт такую привязку у ~46% окон, другая книга той же отрасли — у ~16%). Окно между двумя покрытыми тоже покрыто: колода
    выбирает главное, а не каждый абзац. Сам текст прежних материалов не нужен и не хранится: сравнение идёт по тексту карточек."""
    n = len(index.spans)
    if not card_texts or n == 0:
        return [1.0] * n
    win_sets = [set(v) for v in index.vecs]
    max_idf = max(index.idf.values()) if index.idf else 1.0
    anchored = [False] * n
    for text in card_texts:
        terms = set(stems(text or ""))
        if not terms:
            continue
        weights = {t: index.idf.get(t, max_idf) for t in terms}
        total = sum(weights.values()) or 1.0
        scores = sorted(((sum(w for t, w in weights.items() if t in ws) / total, i) for i, ws in enumerate(win_sets)), reverse=True)
        if len(scores) > 1 and scores[0][0] >= ANCHOR_MIN_CONTAINMENT and scores[0][0] - scores[1][0] >= ANCHOR_MIN_MARGIN:
            anchored[scores[0][1]] = True
    covered = [anchored[i] or (0 < i < n - 1 and anchored[i - 1] and anchored[i + 1]) for i in range(n)]
    return [0.0 if c else 1.0 for c in covered]


def effective_chars(index: "SourceIndex", novelty: list[float]) -> int:
    """Сколько знаков нового материала реально нового: покрытое остаётся с небольшим весом (COVERED_FLOOR)."""
    return int(sum((b - a) * (COVERED_FLOOR + (1 - COVERED_FLOOR) * n) for (a, b), n in zip(index.spans, novelty)))


TIER_FLOOR = {0: 3, 1: 2, 2: 1}   # основы и темы держат общие идеи ветки: им минимум побольше, чем подтеме
SIZE_EXPONENT = 0.85              # в длинном куске больше примеров и пересказов, а не пропорционально больше обязательных фактов
CASE_SHARE = 0.08                 # кейсы (ярус 3) — виньетки поверх уже заданных правил: не больше 8% колоды
def target_cards(source_chars: int, chars_per_card: int) -> int:
    """Цель по числу карточек для источника: одна на chars_per_card знаков (~страница учебника). Это цель по смыслу, а не по деньгам:
    колода должна быть КОНСПЕКТОМ книги, а не её пересказом (тысячи карточек на учебник — это уже перечитывание текста)."""
    return max(1, round(source_chars / max(1, chars_per_card)))


def allocate_cards(nodes: list[dict], sizes: dict[str, int], total: int, case_cards: int = 3,
                   max_cards: int | None = None, floor_override: dict[str, int] | None = None) -> dict[str, int]:
    """Раскладывает ЦЕЛЬ по числу карточек (total) по узлам: каждому узлу — минимум по ярусу (TIER_FLOOR), остальное — пропорционально размеру куска книги
    в степени SIZE_EXPONENT (методом наибольших остатков, сумма точно равна total). Кейсы получают немного (≤ CASE_SHARE).
    Если даже минимумов не хватает — по одной карточке на узел (сумма тогда чуть больше total)."""
    total = max(1, int(total))
    cases = [n for n in nodes if n["tier"] == 3]
    carriers = [n for n in nodes if n["tier"] < 3]
    quotas: dict[str, int] = {}
    per_case = max(1, min(case_cards, int(CASE_SHARE * total / len(cases)))) if cases else 0
    for n in cases:
        quotas[n["key"]] = per_case
    left = total - per_case * len(cases)
    floors = {n["key"]: (floor_override or {}).get(n["key"], TIER_FLOOR.get(n["tier"], 1)) for n in carriers}
    if sum(floors.values()) > left:
        floors = {k: 1 for k in floors}
    spare = max(0, left - sum(floors.values()))
    weights = {n["key"]: max(sizes.get(n["key"], 0), 1000) ** SIZE_EXPONENT for n in carriers}
    wsum = sum(weights.values()) or 1.0
    shares = {k: spare * w / wsum for k, w in weights.items()}
    extra = {k: int(v) for k, v in shares.items()}
    for k in sorted(shares, key=lambda k: shares[k] - extra[k], reverse=True)[:spare - sum(extra.values())]:
        extra[k] += 1
    for k, f in floors.items():
        quotas[k] = f + extra.get(k, 0)
        if max_cards:
            quotas[k] = min(quotas[k], max_cards)
    return quotas


FACT_BLOCK_CHARS = 3200      # блок книги для конспекта: достаточно короткий, чтобы модель прошла его целиком, а не пересказала в общих чертах


def fact_blocks(text: str, size: int = FACT_BLOCK_CHARS) -> list[tuple[int, int]]:
    """Блоки книги по порядку: [a, b) по ~size знаков, границы по концам строк (как окна, но мельче)."""
    return split_windows(text, size)


def block_fact_targets(blocks: list[tuple[int, int]], weights: list[float], usable: list[bool], total: int) -> list[int]:
    """Сколько фактов конспекта просить на каждый блок: total раскладывается по блокам пропорционально длине и весу блока
    (насыщенность × новизна), у неучебных блоков (оглавление, литература) фактов нет. Наибольший остаток, сумма равна total;
    учебный блок с заметной долей получает хотя бы один."""
    raw = [(b - a) * w if ok else 0.0 for (a, b), w, ok in zip(blocks, weights, usable)]
    wsum = sum(raw)
    if total <= 0 or wsum <= 0:
        return [0] * len(blocks)
    shares = [total * r / wsum for r in raw]
    out = [int(x) for x in shares]
    for i in sorted(range(len(shares)), key=lambda i: shares[i] - out[i], reverse=True)[:max(0, total - sum(out))]:
        out[i] += 1
    return [max(1, k) if (ok and sh >= 0.4) else k for k, ok, sh in zip(out, usable, shares)]


class NodeMatcher:
    """К какому узлу карты отнести факт: среди лучших узлов его блока книги берётся тот, чьи название, описание и карточки больше
    похожи на сам факт (программа, без вызовов ИИ). Кейсы (ярус 3) фактов не получают."""

    def __init__(self, index: "SourceIndex", nodes: list[dict], cards_by_node: dict[str, list[dict]] | None = None):
        self.index = index
        self.carriers = [n for n in nodes if n["tier"] < 3]
        cards_by_node = cards_by_node or {}
        self.queries = []
        for n in self.carriers:
            cards = " ".join(f"{c['text']} {c['translation']}" for c in cards_by_node.get(n["key"], []))
            self.queries.append(index.query(f"{n['name']} {n['name']} {n['name']} {n.get('summary') or ''} {cards}"))
        self._win_top: dict[int, list[int]] = {}

    def _window_top(self, win: int) -> list[int]:
        if win not in self._win_top:
            wv = self.index.vecs[win]
            scored = sorted(((sum(x * wv.get(w, 0.0) for w, x in q.items()) * (1 + TIER_BONUS * self.carriers[i]["tier"]), i)
                             for i, q in enumerate(self.queries)), reverse=True)
            best = scored[0][0] if scored else 0.0
            self._win_top[win] = [i for s, i in scored[:SOFT_TOP_K] if best >= MIN_MATCH_SCORE and s >= SOFT_MIN_SHARE * best]
        return self._win_top[win]

    def best(self, fact: str, win: int | None = None) -> str | None:
        if not self.carriers:
            return None
        cands = (self._window_top(win) if win is not None and 0 <= win < len(self.index.vecs) else []) or list(range(len(self.carriers)))
        fv = self.index.query(fact)
        best_i, best_s = cands[0], -1.0
        for i in cands:
            s = sum(x * fv.get(w, 0.0) for w, x in self.queries[i].items()) * (1 + TIER_BONUS * self.carriers[i]["tier"])
            if s > best_s:
                best_i, best_s = i, s
        return self.carriers[best_i]["key"]


def card_density(index: SourceIndex, cards: list[dict]) -> list[int]:
    """Сколько карточек приходится на каждое окно источника (карточка → самое похожее окно)."""
    counts = [0] * len(index.spans)
    for c in cards:
        q = index.query(f"{c.get('text', '')} {c.get('translation', '')} {c.get('example', '')}")
        best_i, best_s = -1, 0.0
        for i, wv in enumerate(index.vecs):
            s = sum(x * wv.get(w, 0.0) for w, x in q.items())
            if s > best_s:
                best_i, best_s = i, s
        if best_i >= 0 and best_s >= MIN_MATCH_SCORE:
            counts[best_i] += 1
    return counts


__all__ = ["join_wrapped", "lesson_alignment", "lesson_text", "card_in_lesson", "SourceIndex", "WINDOW_CHARS", "card_density", "card_quotas",
           "allocate_cards", "target_cards", "fact_blocks", "block_fact_targets", "NodeMatcher", "window_saturation", "window_novelty", "effective_chars", "extract_sections", "node_source_sizes", "parse_src_refs", "section_card_report", "split_windows", "stems", "uncovered_sections", "weak_sections"]


# ---------------------------------------------------------------------------
# Охват по оглавлению: какие разделы книги ни один узел карты не упоминает в своём src
# ---------------------------------------------------------------------------

MIN_SECTION_CHARS = 8000
MIN_SECTIONS_FOR_AUDIT = 6
TITLE_KEY_CHARS = 22        # заголовок в оглавлении и в тексте переносится по-разному: сравниваем только начало
MIN_BODY_CHARS = 400        # «раздел» короче — это строка оглавления/колонтитул, а не раздел

_DECIMAL_HEAD = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.?[ \t]+(\S.{2,139})$")
_PARA_HEAD = re.compile(r"^\s*§\s*(\d{1,2})\.?[ \t]+(\S.{2,139})$")
_TOC_LINE = re.compile(r"(\.{4,}|…{2,})|\s\d{1,4}\s*$")        # строка оглавления: отточие или номер страницы в конце


def _norm_title(title: str) -> str:
    return re.sub(r"[^\w]+", "", title.lower())[:TITLE_KEY_CHARS]


def extract_sections(text: str) -> list[dict]:
    """Нумерованные разделы книги: «1.2. Название» или «§ 3. Название».
    Оглавление и колонтитулы дублируют заголовки: из дублей оставляем вхождение с самым длинным телом, и только после
    этого главы для «§» определяются по сбросу нумерации. Пустой список, если книга так не размечена
    (проверка тогда просто не применяется)."""
    raw: list[dict] = []
    offset = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if not _TOC_LINE.search(stripped):
            m = _DECIMAL_HEAD.match(line)
            if m:
                raw.append({"pos": offset, "chapter": int(m.group(1)), "section": int(m.group(2)),
                            "title": re.sub(r"\s+", " ", m.group(3)), "para": False})
            else:
                p = _PARA_HEAD.match(line)
                if p:
                    raw.append({"pos": offset, "chapter": 0, "section": int(p.group(1)),
                                "title": re.sub(r"\s+", " ", p.group(2)), "para": True})
        offset += len(line) + 1
    if len(raw) < MIN_SECTIONS_FOR_AUDIT:
        return []

    def body_len(i: int) -> int:
        return (raw[i + 1]["pos"] if i + 1 < len(raw) else len(text)) - raw[i]["pos"]

    best: dict[str, int] = {}
    for i, h in enumerate(raw):
        k = f"{h['chapter']}:{h['section']}:{_norm_title(h['title'])}"   # номер + начало названия: «История становления…» есть в нескольких главах
        if k not in best or body_len(i) > body_len(best[k]):
            best[k] = i
    kept = [raw[i] for i in sorted(best.values()) if body_len(i) >= MIN_BODY_CHARS]

    chapter, last_para = 0, 99
    for h in kept:
        if h["para"]:
            if h["section"] <= last_para:
                chapter += 1
            last_para = h["section"]
            h["chapter"] = chapter
    out = []
    for i, h in enumerate(kept):
        end = kept[i + 1]["pos"] if i + 1 < len(kept) else len(text)
        out.append({"chapter": h["chapter"], "section": h["section"], "title": h["title"],
                    "start": h["pos"], "end": end, "chars": end - h["pos"]})
    seen, unique = set(), []
    for s_ in out:                                    # на случай совпадения номеров в разных местах
        key = (s_["chapter"], s_["section"])
        if key not in seen:
            seen.add(key)
            unique.append(s_)
    return unique if len(unique) >= MIN_SECTIONS_FOR_AUDIT else []


_CHAPTER_REF = re.compile(r"(?:гл(?:ава|ав)?\.?|chapter|ch\.?)\s*(\d{1,2})", re.IGNORECASE)
_DECIMAL_REF = re.compile(r"(\d{1,2})\.(\d{1,2})")
_PARA_REF = re.compile(r"§\s*(\d{1,2})(?:\s*[–\-]\s*(\d{1,2}))?")


def parse_src_refs(src: str) -> set[tuple[int, int]]:
    """Ссылки узла на разделы: «Гл. 14, § 3», «Гл. 4, §4.3», «§11.2–11.3» → {(14, 3)}, {(4, 3)}, {(11, 2), (11, 3)}.
    Ссылка только на главу («Гл. 5») раздел не закрывает."""
    refs: set[tuple[int, int]] = set()
    src = src or ""
    for a, b in _DECIMAL_REF.findall(src):
        refs.add((int(a), int(b)))
    ch = _CHAPTER_REF.search(src)
    if ch:
        c = int(ch.group(1))
        for a, b in _PARA_REF.findall(re.sub(r"\d{1,2}\.\d{1,2}", " ", src)):
            lo = int(a)
            hi = int(b) if b else lo
            for s in range(lo, min(hi, lo + 20) + 1):
                refs.add((c, s))
    return refs


def uncovered_sections(sections: list[dict], nodes: list[dict], min_chars: int = MIN_SECTION_CHARS) -> list[dict]:
    """Крупные разделы книги, которых нет в src ни одного узла."""
    covered: set[tuple[int, int]] = set()
    for n in nodes:
        covered |= parse_src_refs(n.get("src") or "")
    return [s for s in sections if s["chars"] >= min_chars and (s["chapter"], s["section"]) not in covered]


# ---------------------------------------------------------------------------
# Отчёт о колоде: насколько карточки покрывают разделы книги
# ---------------------------------------------------------------------------

def section_card_report(text: str, sections: list[dict], cards: list[dict]) -> list[dict]:
    """Для каждого раздела книги — сколько карточек на него пришлось (карточка → самый похожий раздел по TF-IDF)
    и плотность на 10 тыс. знаков. Нужен для отчёта; на генерацию не влияет."""
    if not sections or not cards:
        return []
    counters = [Counter(stems(text[s["start"]:s["end"]])) for s in sections]
    df = Counter(w for c in counters for w in c)
    n = len(sections)
    idf = {w: math.log(n / (1 + k)) + 1.0 for w, k in df.items()}

    def vec(c):
        v = {w: (1 + math.log(k)) * idf.get(w, 1.0) for w, k in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    section_vecs = [vec(c) for c in counters]
    counts = [0] * n
    for card in cards:
        q = vec(Counter(stems(f"{card.get('text', '')} {card.get('translation', '')} {card.get('example', '')}")))
        best_i, best_s = -1, 0.0
        for i, sv in enumerate(section_vecs):
            s = sum(x * sv.get(w, 0.0) for w, x in q.items())
            if s > best_s:
                best_i, best_s = i, s
        if best_i >= 0 and best_s >= MIN_MATCH_SCORE:
            counts[best_i] += 1
    return [{"chapter": s["chapter"], "section": s["section"], "title": s["title"], "chars": s["chars"],
             "cards": counts[i], "per_10k": round(10000 * counts[i] / max(1, s["chars"]), 2)} for i, s in enumerate(sections)]


def weak_sections(report: list[dict], rel_threshold: float = 0.4, min_chars: int = MIN_SECTION_CHARS) -> list[dict]:
    """Крупные разделы, где карточек заметно меньше, чем в среднем по книге (меньше 40% средней плотности)."""
    total_chars = sum(r["chars"] for r in report)
    if not total_chars:
        return []
    mean = 10000 * sum(r["cards"] for r in report) / total_chars
    return [r for r in report if r["chars"] >= min_chars and r["per_10k"] < rel_threshold * mean]


# ---------------------------------------------------------------------------
# Согласованность урока и карточек: есть ли ответ карточки в тексте урока того же узла
# ---------------------------------------------------------------------------

def lesson_text(lesson: dict | None) -> str:
    if not lesson:
        return ""
    parts = [str(s.get("say") or "") for s in lesson.get("screens") or []]
    for c in lesson.get("check") or []:
        parts.append(f"{c.get('q', '')} {' '.join(c.get('options') or [])} {c.get('why', '')}")
    return " ".join(parts)


def card_in_lesson(card: dict, lesson_stems: set[str]) -> float:
    """Доля значимых слов ответа карточки, которые есть в уроке (0..1). Нет значимых слов — считаем покрытой."""
    answer = set(stems(card.get("translation", "")))
    if not answer:
        return 1.0
    return sum(1 for w in answer if w in lesson_stems) / len(answer)


def lesson_alignment(packs: dict) -> dict:
    """Сводка по всем узлам: сколько ответов карточек целиком/частично есть в уроке и какие узлы хуже всего."""
    full = part = none = 0
    worst = []
    for key, pack in (packs or {}).items():
        cards = pack.get("cards") or []
        if not cards:
            continue
        ls = set(stems(lesson_text(pack.get("lesson"))))
        scores = [card_in_lesson(c, ls) for c in cards]
        f = sum(1 for x in scores if x >= 0.99)
        full += f
        part += sum(1 for x in scores if 0.5 <= x < 0.99)
        none += sum(1 for x in scores if x < 0.5)
        worst.append((f / len(cards), key))
    total = full + part + none
    worst.sort()
    return {"cards": total, "full_share": round(full / total, 3) if total else 0.0,
            "partial_share": round(part / total, 3) if total else 0.0, "none_share": round(none / total, 3) if total else 0.0,
            "worst_nodes": [k for _, k in worst[:5]]}
