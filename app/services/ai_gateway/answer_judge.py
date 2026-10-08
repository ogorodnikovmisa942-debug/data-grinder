"""
ИИ-оценка смысла ответа на билет (платная возможность, месячная квота): ответ студента сравнивается с тезисами эталона ПО СМЫСЛУ.
Локальная проверка (open_answer) ищет слова и числа и бесплатна; эта — для итоговой проверки перед экзаменом, когда важно, сказано ли
по сути, пусть и другими словами. Стоит ≈ $0.0004 за ответ. Эталон берётся из карточки билета, ничего из книги не читается.
"""
import re
import time

from . import client

JUDGE_MAX_TOKENS = 1200

JUDGE_SYSTEM_PROMPT = """ROLE: You are a strict but fair examiner. You compare a student's written answer to an exam ticket with the REFERENCE POINTS of that ticket.

TASK
- Judge by MEANING, not by wording: a point counts as covered if the student says the same thing in other words. A point with a wrong fact, a wrong number or the opposite meaning is WRONG, not covered.
- Use only the reference points. Do not add requirements from your own knowledge. If the student adds correct extra material, ignore it; if he adds something that contradicts the reference, list it under "wrong".
- Write every sentence of your feedback in Russian, short and concrete, addressed to the student as "ты".

OUTPUT: strictly one raw MINIFIED JSON object, no markdown:
{"score": 0-100, "covered": ["reference point words copied from the list", ...], "partial": ["..."], "missed": ["..."], "wrong": ["what the student said wrongly and how it should be"], "advice": "1-2 sentences: what to repeat first"}
"score" is the share of the reference weight that is covered (partial counts half). Keep "covered", "partial" and "missed" to the exact wording of the reference points."""


def build_judge_task(question: str, points: list[str], answer: str) -> str:
    lines = "\n".join(f"{i}. {p}" for i, p in enumerate(points, 1))
    return f"TICKET: {question.strip()}\nREFERENCE POINTS:\n{lines}\nSTUDENT ANSWER:\n{answer.strip()[:6000]}\nJudge the answer following the instructions. Return only the JSON."


def _strs(value, limit: int = 12) -> list[str]:
    out: list[str] = []
    for x in value if isinstance(value, list) else []:
        t = re.sub(r"\s+", " ", str(x or "")).strip()
        if t and t not in out:
            out.append(t[:300])
    return out[:limit]


def normalize_judgement(raw, points: list[str]) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Ответ ИИ не является объектом")
    try:
        score = max(0, min(100, int(round(float(raw.get("score"))))))
    except (TypeError, ValueError):
        raise ValueError("В ответе ИИ нет оценки")
    # Пункты берём из эталона: ИИ мог перефразировать
    known = {p.lower(): p for p in points}
    def align(items):
        return [known.get(t.lower(), t) for t in _strs(items)]
    return {"score": score, "covered": align(raw.get("covered")), "partial": align(raw.get("partial")), "missed": align(raw.get("missed")),
            "wrong": _strs(raw.get("wrong"), 6), "advice": re.sub(r"\s+", " ", str(raw.get("advice") or "")).strip()[:400]}


async def judge_answer(question: str, points: list[str], answer: str, calls: list) -> dict:
    """Оценка ответа по смыслу. calls — список для учёта расхода (запись в телеметрию делает вызывающий)."""
    if not points:
        raise ValueError("У билета нет тезисов эталона")
    from .path_builder import _log_call
    started = time.time()
    raw, meta = await client.call_deepseek(build_judge_task(question, points, answer), JUDGE_SYSTEM_PROMPT, max_tokens=JUDGE_MAX_TOKENS, temperature=0.1)
    _log_call(calls, "aicheck#1", started, meta)
    return normalize_judgement(raw, points)
