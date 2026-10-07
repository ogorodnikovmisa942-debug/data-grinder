"""Подставной DeepSeek для тестов конвейера «Путь знаний»: отвечает по типу задачи (MAP, CARDS, LESSON, ...), записывает запросы."""
import re
from unittest.mock import patch

TYPE_RE = re.compile(r"TYPE: (\w+)")
LESSON_KEY_RE = re.compile(r"^### (\S+) \|", re.M)


def keys_of(prompt: str) -> list[str]:
    """Ключи узлов из запроса CARDS («NODES TO PRODUCE: a, b») или LESSON («### a | Имя | tier 1»)."""
    if "NODES TO PRODUCE: " in prompt:
        return prompt.split("NODES TO PRODUCE: ")[1].split("\n")[0].split(", ")
    return LESSON_KEY_RE.findall(prompt)


def word(key: str) -> str:
    """Уникальное «слово» из ключа узла (кириллица, длиннее трёх букв): карточки разных узлов не считаются повторами."""
    return "тема" + "".join(chr(0x430 + (ord(c) % 32)) for c in key * 2)


def default_cards(key: str, n: int = 2, ev: str = "") -> list[dict]:
    w = word(key)
    return [{"t": f"Какой орган отвечает за раздел {w} под номером {i}?", "s": "Право | Тема", "d": f"Орган номер {i} раздела {w}.",
             "e": "", "l": "easy", "y": 1, "at": "organ", "ev": ev,
             "x": [f"Другой орган {i}.", f"Иной орган {i}.", f"Третий орган {i}."]} for i in range(1, n + 1)]


ANSWER_RE = re.compile(r"^\d+\. Q: .*? \| A: (.*)$", re.M)


def node_block(prompt: str, key: str) -> str:
    """Кусок запроса LESSON, относящийся к узлу."""
    start = prompt.index(f"### {key} |")
    end = prompt.find("### ", start + 5)
    return prompt[start:end if end != -1 else len(prompt)]


def default_lesson(key: str, answers: list[str] | None = None) -> dict:
    """Урок по умолчанию: общие экраны и по экрану на каждый ответ карточки (как делает правильный урок)."""
    screens = [f"Экран {i} узла {key}" for i in range(1, 4)] + [f"Запомни: {a}" for a in (answers or [])]
    return {"screens": [{"say": t, "emo": "talk", "focus": []} for t in screens], "check": []}


class FakeLLM:
    """Вызывается как call_deepseek. Поведение по типам задач переопределяется функциями вида f(prompt, fake) -> raw."""

    def __init__(self, raw_map=None, cards=None, lessons=None, audit=None, fill=None, gaps=None, cost: float = 0.0, align=None, facts=None):
        self.raw_map, self.cost = raw_map, cost
        self.handlers = {"CARDS": cards, "LESSON": lessons, "AUDIT": audit, "FILL": fill, "GAPS": gaps, "ALIGN": align, "FACTS": facts}
        self.log: list[tuple[str, str]] = []          # (тип, запрос)

    def of(self, kind: str) -> list[str]:
        return [p for k, p in self.log if k == kind]

    async def __call__(self, user_prompt, **kwargs):
        kind = TYPE_RE.search(user_prompt).group(1)
        self.log.append((kind, user_prompt))
        meta = {"cost_usd": self.cost, "cache_hit_tokens": 0, "completion_tokens": 10, "prompt_tokens": 1000}
        handler = self.handlers.get(kind)
        if handler is not None:
            return handler(user_prompt, self), meta
        if kind == "MAP":
            return self.raw_map, meta
        if kind == "CARDS":
            return {"nodes": [{"key": k, "cards": default_cards(k)} for k in keys_of(user_prompt)]}, meta
        if kind == "LESSON":
            return {"nodes": [{"key": k, "lesson": default_lesson(k, ANSWER_RE.findall(node_block(user_prompt, k)))}
                              for k in keys_of(user_prompt)]}, meta
        if kind == "AUDIT":
            return {"audit": []}, meta
        if kind == "FILL":
            return {"fill": []}, meta
        if kind == "GAPS":
            return {"gaps": []}, meta
        if kind == "LINKS":
            return {"edges": []}, meta
        if kind == "ALIGN":
            return {"align": []}, meta
        if kind == "FACTS":                              # блоки без фактов: конспект тест не проверяет, пока не подставлен свой ответ
            return {"blocks": [{"id": i, "facts": []} for i in re.findall(r"^(B\d+) \|", user_prompt, re.M)]}, meta
        return {"lesson": None}, meta                  # INTRO


def patched(fake: FakeLLM):
    async def call(user_prompt, **kwargs):          # patch ждёт обычную async-функцию, а не объект с async __call__
        return await fake(user_prompt, **kwargs)
    return patch("app.services.ai_gateway.client.call_deepseek", side_effect=call)
