"""Нормализация названий предметов."""


def resolve_subject_alias(subject_slug: str) -> str:
    """Предмет — это ровно то название (slug), которое ввёл пользователь, без регистра и пробелов по краям."""
    return (subject_slug or "").strip().lower()


def get_all_subject_aliases(subject_slug: str) -> list[str]:
    """Названия, под которыми карточки предмета лежат в базе. Таблиц «синонимов» нет: предмет один, как назван."""
    s = resolve_subject_alias(subject_slug)
    return [s] if s else []
