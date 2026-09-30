"""
Политика: КОГДА вместо обычной карточки или теста давать вопрос с письменным (открытым) ответом.

Выводы исследований, на которых построены правила (подробности и ссылки: docs/open_questions_policy.md):
1. Вспоминание с нуля полезнее узнавания: эффект g≈0.8 (свободный ответ) против ≈0.4 (выбор из вариантов). Rowland, 2014.
2. Но преимущество есть, только если ответ обычно удаётся вспомнить. Когда вспомнить трудно, выбор из вариантов
   не хуже. Smith & Karpicke, 2014; Kang et al., 2007. Поэтому новые, шаткие и «пиявочные» карточки открытым вопросом не проверяем.
3. Эффект растёт с интервалом: чем прочнее и старше запись, тем охотнее проверяем письменно.
4. Обратная связь обязательна (эталон и разбор по тезисам): 0.73 против 0.39 без неё.
5. Формат тренировки должен совпадать с форматом проверки: в режиме «подготовка к экзамену» доля выше,
   но формат всё равно смешиваем, чтобы знание не «прилипало» к одному виду вопроса.
6. Оптимум обучения около 85% верных: по точности ответов пользователя на открытых вопросах долю подстраиваем.

Политика детерминирована (хеш карточки и даты), чтобы перезагрузка очереди не меняла формат вопроса.
"""
import hashlib
from typing import Any, Iterable

from app.services.open_answer import significant_stems

OPEN_MODES = ("auto", "exam", "off")
DEFAULT_MODE = "auto"

# Базовая доля подходящих карточек, предъявляемых письменно
BASE_SHARE = {"auto": 0.20, "exam": 0.60, "off": 0.0}
MAX_SHARE = 0.85
# Потолок открытых вопросов за одну выдачу: набор текста на телефоне дорог
SESSION_CAP = {"auto": 5, "exam": 12}

MIN_STABILITY_DAYS = 4.0    # ниже — вспоминание ещё шаткое (признак «успеха извлечения»)
MAX_LAPSES = 3              # 4+ провалов — пиявка: сначала закрепить обычным способом
MAX_REFERENCE_CHARS = 400   # длинный эталон неудобно печатать и плохо проверять
SHORT_ANSWER_WORDS = 5      # до 5 слов — «впишите ответ», дольше — развёрнутый ответ

MIN_RECENT_FOR_ADAPT = 8    # сколько открытых ответов нужно для подстройки
ACC_HIGH = 0.92
ACC_LOW = 0.75
ACC_VERY_LOW = 0.60


def normalize_mode(mode: Any) -> str:
    return mode if mode in OPEN_MODES else DEFAULT_MODE


def reference_word_count(text: str | None) -> int:
    return len((text or "").split())


def answer_kind(text: str | None) -> str:
    """short — вписать термин/дату/число; free — развёрнутый ответ своими словами."""
    return "short" if reference_word_count(text) <= SHORT_ANSWER_WORDS else "free"


def is_open_eligible(card: Any) -> bool:
    """Карточку можно предъявить письменно: изучена, вспоминание устойчиво, эталон проверяем и не слишком длинный."""
    if getattr(card, "state", None) != 2:
        return False
    if getattr(card, "content_type", "text") == "cloze":
        return False  # пропуск в тексте уже требует вспоминания
    if (getattr(card, "lapses", 0) or 0) > MAX_LAPSES:
        return False
    if (getattr(card, "stability", 0.0) or 0.0) < MIN_STABILITY_DAYS:
        return False
    ref = (getattr(card, "translation", "") or "").strip()
    if not ref or len(ref) > MAX_REFERENCE_CHARS:
        return False
    return bool(significant_stems(ref))


def share_for(mode: str, stability: float, recent_accuracy: float | None = None, n_recent: int = 0) -> float:
    """Вероятность открытого формата для подходящей карточки."""
    share = BASE_SHARE.get(normalize_mode(mode), 0.0)
    if share <= 0:
        return 0.0
    if stability >= 30:
        share *= 1.4
    elif stability >= 14:
        share *= 1.2
    if recent_accuracy is not None and n_recent >= MIN_RECENT_FOR_ADAPT:
        if recent_accuracy >= ACC_HIGH:
            share *= 1.25      # отвечаете уверенно — можно сложнее
        elif recent_accuracy < ACC_VERY_LOW:
            share *= 0.25      # слишком трудно: возвращаемся к выбору и самопроверке
        elif recent_accuracy < ACC_LOW:
            share *= 0.6
    return min(share, MAX_SHARE)


def _roll(card_id: Any, day_key: str, salt: str = "") -> float:
    digest = hashlib.sha1(f"{salt}:{day_key}:{card_id}".encode()).hexdigest()[:8]
    return int(digest, 16) / 0xFFFFFFFF


def pick_open_ids(
    cards: Iterable[Any],
    mode: str,
    day_key: str,
    recent_accuracy: float | None = None,
    n_recent: int = 0,
    salt: str = "",
    cap: int | None = None,
) -> set:
    """Какие из карточек показать письменно. Стабильно в течение суток; не больше потолка за выдачу."""
    mode = normalize_mode(mode)
    if mode == "off":
        return set()
    limit = SESSION_CAP[mode] if cap is None else cap
    chosen: list[tuple[float, Any]] = []
    for c in cards:
        if not is_open_eligible(c):
            continue
        roll = _roll(c.id, day_key, salt)
        if roll < share_for(mode, getattr(c, "stability", 0.0) or 0.0, recent_accuracy, n_recent):
            chosen.append((roll, c.id))
    chosen.sort()
    return {cid for _, cid in chosen[:limit]}
