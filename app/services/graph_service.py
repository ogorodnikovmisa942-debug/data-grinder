"""Нормализация названий предметов и их синонимов (алиасов)."""


def resolve_subject_alias(subject_slug: str) -> str:
    """Нормализует предмет без принудительной подмены на другие имена."""
    return (subject_slug or "").strip().lower()


def get_all_subject_aliases(subject_slug: str) -> list[str]:
    """Возвращает все известные синонимы и сокращения предмета для полноты выборки."""
    s = (subject_slug or "").strip().lower()
    if not s:
        return []
    if s in ("sudoustr", "sudoustroystvo", "sudoust", "court_system", "судоустройство", "sud", "суд"):
        aliases = ["sudoustr", "sudoustroystvo", "sudoust", "court_system", "судоустройство", "sud", "суд"]
        if s in aliases:
            aliases.remove(s)
            aliases.insert(0, s)
        return aliases
    if s in ("civil_law", "гражданское", "гк_рф", "гражданское_право", "law_civil", "law_civil_rb"):
        aliases = ["civil_law", "гражданское", "гк_рф", "гражданское_право", "law_civil", "law_civil_rb"]
        if s in aliases:
            aliases.remove(s)
            aliases.insert(0, s)
        return aliases
    if s in ("onshteorpravo", "teoriya_prava", "общая_теория_права", "тгп", "tgp"):
        aliases = ["onshteorpravo", "teoriya_prava", "общая_теория_права", "тгп", "tgp"]
        if s in aliases:
            aliases.remove(s)
            aliases.insert(0, s)
        return aliases
    if s in ("chinese", "chinese_hsk3", "hsk3", "китайский", "китайский_язык"):
        aliases = ["chinese", "chinese_hsk3", "hsk3", "китайский", "китайский_язык"]
        if s in aliases:
            aliases.remove(s)
            aliases.insert(0, s)
        return aliases
    if s in ("python", "python_pro", "python_advanced"):
        aliases = ["python", "python_pro", "python_advanced"]
        if s in aliases:
            aliases.remove(s)
            aliases.insert(0, s)
        return aliases
    return [s]
