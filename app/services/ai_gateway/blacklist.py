"""
Структурные проверки карточки, верные для любого предмета: пустая, слишком короткая, «Да/Нет», тавтология.

Раньше здесь жил «чёрный список» правил под один учебник (методология, даты 1917–1939, Монтескьё, архивные справки и т. п.).
Он молча выбрасывал нормальные карточки других курсов и удалён: что учить, решает книга, а не список запретов по темам.
"""
import re

_FUNCTION_WORDS = {"и", "в", "во", "на", "с", "со", "к", "ко", "по", "от", "до", "из", "за", "не", "ни", "то", "а", "но", "да", "же", "бы",
                   "ли", "о", "об", "у", "для", "при", "что", "как", "это"}
_NUMERAL_WORD = re.compile(
    r"\b(один|одна|одно|одну|одни|два|две|трое|три|четыре|пять|шесть|семь|восемь|девять|десять|одиннадцать|двенадцать|тринадцать|"
    r"четырнадцать|пятнадцать|двадцать|тридцать|сорок|пятьдесят|сто|двух|трех|трёх|четырех|четырёх|пяти|шести|семи|восьми|девяти|десяти)\b")

# --- ПРОГРАММНЫЙ ВАЛИДАТОР КАЧЕСТВА И СТРОГИЙ BLACKLIST (R3, R4) ---
def is_structurally_invalid_card(card: dict) -> tuple[bool, str]:
    """Только то, что верно для карточки по любому предмету: пустая, слишком короткая, «Да/Нет», тавтология.
    Именно эта проверка работает в конвейере «Путь знаний»: правила ниже (методология, даты 1917–1939, Монтескьё и т. п.)
    написаны под один учебник и молча выбрасывали нормальные карточки других курсов.
    """
    front = (card.get("text") or card.get("front") or card.get("question") or card.get("t") or "").strip()
    back = (card.get("translation") or card.get("back") or card.get("answer") or card.get("d") or "").strip()

    if not front or not back:
        return True, "empty_front_or_back"

    if len(front) < 5 or len(back) < 2:
        return True, "too_short"

    # Cloze-карточки намеренно содержат целевой термин в разметке {{c1::термин}}
    if "{{c" in front:
        return False, ""

    back_lower = back.lower()
    front_lower = front.lower()

    # 1. Бинарные Да/Нет
    if re.match(r'^(да|нет)[\.,\s!]', back_lower) or back_lower in ("да", "нет", "да.", "нет."):
        return True, "binary_yes_no"

    # 2. Тавтологии и смысловое эхо (когда ответ почти целиком состоит из слов, уже упомянутых в вопросе)
    stop_words = {
        "орган", "органы", "органов", "органам", "органами", "дело", "дела", "государство", "государства",
        "является", "относятся", "относится", "входит", "входят", "группе", "какой", "какому", "какая", "какие", "каком",
        "это", "для", "при", "том", "что", "как", "чем", "кто", "где", "куда", "откуда", "зачем", "почему",
        "его", "ее", "их", "свой", "своей", "своих", "этом", "этой", "этих", "всех", "все", "всей"
    }
    back_words = [w for w in re.findall(r'[a-zA-Zа-яА-Я0-9]{2,}', back_lower) if w not in stop_words and w not in _FUNCTION_WORDS]
    # Число в ответе («Две функции», «Три направления», «5 лет») — это и есть информация, даже если остальные слова повторяют вопрос
    has_number = bool(re.search(r"\d", back_lower)) or bool(_NUMERAL_WORD.search(back_lower))
    if back_words and not has_number:
        back_stems = [w[:6] for w in back_words]
        front_stems = {w[:6] for w in re.findall(r'[a-zA-Zа-яА-Я0-9]{2,}', front_lower)}
        overlap_count = sum(1 for s in back_stems if s in front_stems)
        # Если ответ короткий и все значащие основы в вопросе, ЛИБО длинный и >= 75% слов в вопросе:
        if (len(back_stems) <= 3 and overlap_count == len(back_stems)) or (len(back_stems) >= 4 and (overlap_count / len(back_stems)) >= 0.75):
            return True, "tautology"

    return False, ""


def semantic_normalize_front(text: str) -> str:
    """Формирует инвариантный смысловой отпечаток вопроса для семантической дедупликации."""
    t = re.sub(r'\{\{c\d+::(.*?)(?:::.*?)?\}\}', r'\1', text)
    t = re.sub(r'\[(.*?)\]', r'\1', t)
    t = t.lower()
    words = re.findall(r'[a-zA-Zа-яА-Я0-9]{3,}', t)
    stop_words = {
        "чем", "как", "какой", "какая", "какие", "каком", "каков", "что", "где", "куда",
        "кто", "когда", "почему", "зачем", "отличается", "отличие", "принципиально",
        "судебном", "процессе", "процесс", "суде", "деле", "случае", "согласно", "соответствии",
        "рамках", "сферы", "точки", "зрения", "какова", "заключается", "ключевое", "ключевой",
        "различие", "различия", "разграничение", "основное", "основной", "между", "суть",
        "понятие", "обозначает", "представляет", "собой", "называют", "называется", "определяется"
    }
    def _stem(w: str) -> str:
        for ending in ("ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ях", "ах", "ом", "ем", "ой", "ей", "ый", "ий", "ая", "яя", "ое", "ее", "ые", "ие", "ов", "ев", "ам", "ям", "а", "я", "о", "е", "у", "ю", "ы", "и"):
            if w.endswith(ending) and len(w) - len(ending) >= 3:
                return w[:-len(ending)][:5]
        return w[:5]

    stems = sorted({_stem(w) for w in words if w not in stop_words})
    # Для коротких или шаблонных вопросов (<3 значащих лемм) не задействуем нечеткую дедупликацию
    if len(stems) < 3:
        return ""
    return " ".join(stems)


def strip_secondary_spoilers(front: str, back: str, secondary: str) -> str:
    """Убирает из 's' (виден на лицевой стороне) части после '|', которые подсказывают ответ 'd'."""
    clean_sec = str(secondary or "").strip()
    clean_back = str(back or "").strip()
    if "|" not in clean_sec or not clean_back:
        return clean_sec

    def extract_stems(text_val: str) -> set[str]:
        stop_stems = {"суд", "дел", "прав", "закон", "орган", "норм", "стат", "кодекс", "област", "виды", "вид", "form", "part", "case", "rule", "type"}
        stems = set()
        for w in re.findall(r'[a-zA-Zа-яА-Я0-9]{4,}', text_val.lower()):
            s = re.sub(r'(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ого|его|ому|ему|ых|их|ым|им|ом|ем|ами|ями|ях|ах|ов|ев|ей|ам|ям|а|я|у|ю|е|о|ы|и|ь|ing|ed|es|s)$', '', w)
            if len(s) >= 3 and s not in stop_stems:
                stems.add(s)
        return stems

    parts = [p.strip() for p in clean_sec.split("|")]
    back_stems = extract_stems(clean_back)
    is_concept_def = bool(re.search(r'\b(?:какое понятие|какой термин|назовите понятие|назовите термин|что обозначает|what concept|what term|which term)\b', str(front or "").lower()))
    safe_parts = [parts[0]]
    for part in parts[1:]:
        part_lower = part.lower()
        part_stems = extract_stems(part)
        is_spoiler = bool(part_stems & back_stems)
        if not is_spoiler and is_concept_def:
            is_spoiler = any(s in clean_back.lower() for s in part_stems if len(s) >= 4)
        if not is_spoiler and re.search(r'\b(?:срок|дней|суток|месяц|кгб|комитет|надзор|отмена|запрещен|противопоказан)\b', part_lower):
            is_spoiler = any(w in clean_back.lower() for w in part_lower.split())
        if not is_spoiler:
            safe_parts.append(part)
    return " | ".join(safe_parts)


__all__ = [
    "is_structurally_invalid_card",
    "semantic_normalize_front",
    "strip_secondary_spoilers",
]
