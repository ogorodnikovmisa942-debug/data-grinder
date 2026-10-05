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


def node_source_sizes(index: SourceIndex, nodes: list[dict]) -> dict[str, int]:
    """Сколько знаков источника «принадлежит» каждому узлу. Окно делится поровну между узлами, чьи название и описание
    на него похожи (победитель-забирает-всё отдавал общим понятиям вроде «Правоотношение» по 50–60 тыс. знаков).
    Кейсы на различение (ярус 3) не участвуют: это виньетки поверх подтем, а не отдельные куски книги."""
    carriers = [n for n in nodes if n["tier"] < 3]
    queries = [index.query(f"{n['name']} {n['name']} {n['name']} {n.get('summary') or ''}") for n in carriers]
    sizes = {n["key"]: 0.0 for n in nodes}
    for wv, (a, b) in zip(index.vecs, index.spans):
        scored = []
        for i, q in enumerate(queries):
            s = sum(x * wv.get(w, 0.0) for w, x in q.items())
            scored.append((s * (1 + TIER_BONUS * carriers[i]["tier"]), i))
        scored.sort(reverse=True)
        best = scored[0][0] if scored else 0.0
        if best < MIN_MATCH_SCORE:
            continue
        top = [i for s, i in scored[:SOFT_TOP_K] if s >= SOFT_MIN_SHARE * best]
        for i in top:
            sizes[carriers[i]["key"]] += (b - a) / len(top)
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


__all__ = ["join_wrapped", "lesson_alignment", "lesson_text", "card_in_lesson", "SourceIndex", "WINDOW_CHARS", "card_density", "card_quotas", "extract_sections", "node_source_sizes", "parse_src_refs", "section_card_report", "split_windows", "stems", "uncovered_sections", "weak_sections"]


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
