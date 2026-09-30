"""
Проверка ответов на вопросы с открытым ответом БЕЗ ИИ и без сети.

Идея: ответ сверяется не с эталонной строкой целиком, а со списком ключевых тезисов.
Тезис засчитан, если в ответе есть его слова (по основам, с допуском опечаток) или слова любого из его вариантов.
Числа и даты сравниваются точно. Тезис с отрицанием перед ним («не является …») не засчитывается автоматически.

Результат — рекомендация оценки FSRS и разбор по тезисам. Последнее слово остаётся за пользователем:
лексика не понимает парафраз без общих слов, поэтому вердикт только предлагается.

Ключевые тезисы берутся из карточки (`Card.key_points`); если их нет, они выводятся из эталонного ответа:
он режется на смысловые куски (предложения и части через «;», «:», запятые и союзы).
"""
import re
from difflib import SequenceMatcher
from typing import Any

try:  # лёгкая зависимость без словарей; без неё работает грубая обрезка окончаний
    import snowballstemmer
    _STEMMER = snowballstemmer.stemmer("russian")
    _STEMMER_EN = snowballstemmer.stemmer("english")
except Exception:  # pragma: no cover - зависит от окружения
    _STEMMER = _STEMMER_EN = None

MAX_ANSWER_CHARS = 3000
MAX_POINTS = 12

_WORD_RE = re.compile(r"[a-zа-я0-9]+(?:-[a-zа-я0-9]+)*", re.IGNORECASE)
_CYRILLIC_RE = re.compile(r"[а-я]")

STOPWORDS = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня еще
нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам сказал ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе
под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были
куда зачем всех можно при два другой хоть после над больше тот через эти нас про них какая много разве три эту моя
впрочем свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между это также либо которые
который которая которое которых которым которой является являются
год года году г гг лет
the a an of to in on and or is are be by for with as at from that this
""".split())

NEGATIONS = frozenset({"не", "нет", "без", "ни", "нельзя", "никогда", "отсутствует", "отсутствуют", "not", "no", "never"})

# Пороги сопоставления
FUZZY_MIN_LEN = 5          # короткие слова сравниваем только точно: «вид» и «вина» — разные вещи
FUZZY_RATIO = 0.84         # допуск опечаток и мелких отличий окончаний
FULL_CREDIT_FROM = 0.85    # покрыто почти всё — тезис засчитан полностью
PARTIAL_CREDIT_FROM = 0.3  # меньше 30% слов — тезис не засчитан; между порогами — частичный балл
MATCHED_FROM = 0.6         # с какого покрытия тезис показываем как «есть»
NEGATION_WINDOW = 3        # сколько слов перед совпадением ищем отрицание

RATING_GOOD_FROM = 0.8
RATING_HARD_FROM = 0.5


def normalize(text: str) -> str:
    return (text or "").lower().replace("ё", "е").replace("\xa0", " ")


def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(normalize(text))


def stem(token: str) -> str:
    if token.isdigit():
        return token
    if _CYRILLIC_RE.search(token):
        if _STEMMER:
            return _STEMMER.stemWord(token)
        return token[:6] if len(token) > 6 else token
    if _STEMMER_EN:
        return _STEMMER_EN.stemWord(token)
    return token


def significant_stems(text: str) -> list[str]:
    """Основы значимых слов (без стоп-слов и отрицаний; числа всегда значимы)."""
    result = []
    for t in tokenize(text):
        if t in STOPWORDS or t in NEGATIONS:
            continue
        if len(t) < 2 and not t.isdigit():
            continue
        result.append(stem(t))
    return result


def _stem_matches(needle: str, hay: str) -> bool:
    if needle == hay:
        return True
    if needle.isdigit() or hay.isdigit():
        return False  # числа и даты — только точно
    if len(needle) < FUZZY_MIN_LEN or len(hay) < FUZZY_MIN_LEN:
        return False
    if needle[0] != hay[0]:
        return False
    return SequenceMatcher(None, needle, hay).ratio() >= FUZZY_RATIO


def _coverage(needle_stems: list[str], answer_stems: list[str]) -> tuple[float, list[int]]:
    """Доля основ тезиса, найденных в ответе, и позиции найденных слов. Число/дата не найдены — покрытие 0."""
    if not needle_stems:
        return 0.0, []
    found: list[int] = []
    missing_number = False
    for ns in needle_stems:
        pos = next((i for i, hs in enumerate(answer_stems) if _stem_matches(ns, hs)), None)
        if pos is None:
            missing_number = missing_number or ns.isdigit()
        else:
            found.append(pos)
    if missing_number:
        return 0.0, []
    return len(found) / len(needle_stems), found


def _point_credit(share: float) -> float:
    """Кредит тезиса по покрытию: почти полное считаем полным, малое — нулём, между — пропорционально."""
    if share >= FULL_CREDIT_FROM:
        return 1.0
    return share if share >= PARTIAL_CREDIT_FROM else 0.0


def _is_negated(positions: list[int], answer_tokens: list[str], variant_tokens: list[str]) -> bool:
    """Перед найденным тезисом стоит «не/без/нет», которого нет в самом тезисе."""
    if not positions or any(t in NEGATIONS for t in variant_tokens):
        return False
    start, end = min(positions), max(positions)
    # отрицание перед тезисом («не является …») или внутри него («власть не делится …»)
    window = answer_tokens[max(0, start - NEGATION_WINDOW):end + 1]
    return any(t in NEGATIONS for t in window)


def _clean_point(raw: Any) -> dict | None:
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").strip()
    if not text:
        return None
    variants = [str(v).strip() for v in (raw.get("variants") or []) if str(v).strip()][:8]
    try:
        weight = float(raw.get("weight", 1.0))
    except (TypeError, ValueError):
        weight = 1.0
    return {"text": text[:300], "variants": [v[:300] for v in variants], "weight": max(0.1, min(5.0, weight))}


def normalize_key_points(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    points = [p for p in (_clean_point(x) for x in raw) if p]
    return points[:MAX_POINTS]


_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|[;\n•]+|\s[—–]\s")


def derive_key_points(reference: str) -> list[dict]:
    """Ключевые тезисы из эталонного ответа: смысловые куски с достаточным числом значимых слов.
    Заголовок до двоеточия («Принцип разделения властей: …») весит вдвое меньше: его слова в ответе не обязаны повторяться."""
    points: list[dict] = []
    for sentence in _SPLIT_RE.split(reference or ""):
        sentence = sentence.strip(" \t.,;:-—")
        if not sentence:
            continue
        head = None
        if ": " in sentence:
            head, sentence = (x.strip() for x in sentence.split(": ", 1))
        # длинное предложение с перечислением режем по запятым, короткое оставляем целиком
        parts = [p.strip() for p in re.split(r",\s+", sentence)] if len(significant_stems(sentence)) > 8 else [sentence]
        if head and significant_stems(head):
            points.append({"text": head[:300], "variants": [], "weight": 0.5})
        points.extend({"text": p[:300], "variants": [], "weight": 1.0} for p in parts if significant_stems(p))
    # склеиваем совсем короткие куски (одно слово) с соседним, чтобы союзы и перечисления не давали ложных тезисов
    merged: list[dict] = []
    for p in points:
        if merged and p["weight"] == 1.0 and merged[-1]["weight"] == 1.0 \
                and len(significant_stems(p["text"])) == 1 and len(significant_stems(merged[-1]["text"])) <= 2:
            merged[-1]["text"] = f'{merged[-1]["text"]}, {p["text"]}'[:300]
        else:
            merged.append(p)
    return merged[:MAX_POINTS]


_BULLET_RE = re.compile(r"^\s*(?:[-•*–]|\d+[.)])\s+")


def points_from_bullets(answer: str) -> list[dict]:
    """Пункты списка «- тезис / вариант / ещё вариант» становятся ключевыми тезисами: человек сам задаёт гранулярность."""
    points = []
    for line in (answer or "").split("\n"):
        if not _BULLET_RE.match(line):
            continue
        alts = [a.strip() for a in _BULLET_RE.sub("", line, count=1).split(" / ") if a.strip()]
        if alts:
            points.append({"text": alts[0][:300], "variants": [a[:300] for a in alts[1:8]], "weight": 1.0})
    return points[:MAX_POINTS]


def suggest_rating(score: float, fast: bool = False) -> int:
    if score >= 1.0 - 1e-9:
        return 4 if fast else 3
    if score >= RATING_GOOD_FROM:
        return 3
    if score >= RATING_HARD_FROM:
        return 2
    return 1


def grade_answer(answer: str, key_points: Any = None, reference: str | None = None, fast: bool = False) -> dict:
    """Оценка ответа. key_points — список {text, variants, weight}; иначе выводятся из reference."""
    answer = (answer or "")[:MAX_ANSWER_CHARS]
    points = normalize_key_points(key_points) or derive_key_points(reference or "")
    answer_tokens = tokenize(answer)
    answer_stems = [stem(t) for t in answer_tokens]

    results = []
    total_w = credit_w = 0.0
    for p in points:
        total_w += p["weight"]
        best_share, negated = 0.0, False
        for candidate in [p["text"], *p["variants"]]:
            share, positions = _coverage(significant_stems(candidate), answer_stems)
            if _point_credit(share) == 0.0:
                continue
            if _is_negated(positions, answer_tokens, tokenize(candidate)):
                negated = True
                continue
            best_share = max(best_share, share)
        credit = _point_credit(best_share)
        credit_w += p["weight"] * credit
        results.append({
            "text": p["text"],
            "matched": best_share >= MATCHED_FROM,
            "partial": 0.0 < credit < 1.0,
            "negated": negated and credit == 0.0,
        })

    score = (credit_w / total_w) if total_w else 0.0
    empty = len(answer_tokens) == 0
    if empty:
        score = 0.0
    return {
        "score": round(score, 3),
        "percent": round(score * 100),
        "suggested_rating": 1 if empty else suggest_rating(score, fast),
        "points": results,
        "matched": sum(1 for r in results if r["matched"]),
        "total": len(results),
        "graded": bool(points),   # False: тезисов нет вообще — только самооценка
    }


# ---------------------------------------------------------------------------
# Разбор списка вопросов, вставленного пользователем
# ---------------------------------------------------------------------------

_Q_MARK = re.compile(r"^\s*(?:вопрос|в|q|question)\s*[:.)]\s*", re.IGNORECASE)
_A_MARK = re.compile(r"^\s*(?:ответ|о|a|answer)\s*[:.)]\s*", re.IGNORECASE)
_NUM_PREFIX = re.compile(r"^\s*(?:билет\s*)?\d+\s*[.)]\s*", re.IGNORECASE)

MAX_QUESTIONS_PER_IMPORT = 200
MAX_QUESTION_CHARS = 500
MAX_REFERENCE_CHARS = 3000


def parse_questions(blob: str) -> list[dict]:
    """
    Блоки, разделённые пустой строкой. В блоке либо явные метки «Вопрос:/Ответ:» (Q:/A:),
    либо первая строка — вопрос, остальное — эталонный ответ.
    Блок без ответа возвращается с пустым answer (пользователю покажем, что проверять не с чем).
    """
    items: list[dict] = []
    for block in re.split(r"\n\s*\n", (blob or "").replace("\r\n", "\n").strip()):
        lines = [l.rstrip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        q_lines: list[str] = []
        a_lines: list[str] = []
        target = None
        explicit = any(_Q_MARK.match(l) or _A_MARK.match(l) for l in lines)
        if explicit:
            for l in lines:
                if _Q_MARK.match(l):
                    target = q_lines
                    l = _Q_MARK.sub("", l, count=1)
                elif _A_MARK.match(l):
                    target = a_lines
                    l = _A_MARK.sub("", l, count=1)
                if target is not None and l.strip():
                    target.append(l.strip())
        else:
            q_lines = [_NUM_PREFIX.sub("", lines[0], count=1).strip()]
            a_lines = [l.strip() for l in lines[1:]]
        question = " ".join(q_lines).strip()
        answer = "\n".join(a_lines).strip()
        if question:
            items.append({"question": question[:MAX_QUESTION_CHARS], "answer": answer[:MAX_REFERENCE_CHARS]})
    return items[:MAX_QUESTIONS_PER_IMPORT]
