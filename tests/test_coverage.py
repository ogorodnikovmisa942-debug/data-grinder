"""Охват источника: квоты карточек по размеру куска книги (без ИИ)."""
import asyncio
from unittest.mock import patch

from app.services.ai_gateway import coverage as cv
from app.services.ai_gateway import path_builder as pb
from app.services.ai_gateway.path_prompts import build_cards_task


def _book():
    """Две темы разного объёма: про суды (длинно) и про нотариат (коротко), по ~5 тыс. знаков на абзац-окно."""
    courts = ("Районный суд рассматривает дела по первой инстанции. Судья районного суда назначается Президентом. " * 8 + "\n") * 18
    notary = ("Нотариус удостоверяет сделки и выдаёт свидетельства о праве на наследство. Нотариальная палата объединяет нотариусов. " * 8 + "\n") * 5
    return courts + notary


def _nodes():
    return [
        {"key": "courts", "name": "Районный суд", "tier": 2, "summary": "Суд первой инстанции, назначение судей"},
        {"key": "notary", "name": "Нотариат", "tier": 2, "summary": "Нотариус удостоверяет сделки, нотариальная палата"},
        {"key": "case", "name": "Разграничение судов", "tier": 3, "summary": "Кейс"},
    ]


def test_windows_cover_text_without_gaps_and_break_on_lines():
    text = _book()
    spans = cv.split_windows(text)
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    assert all(a2 == b1 for (_, b1), (a2, _) in zip(spans, spans[1:]))
    assert all(text[b - 1] == "\n" for _, b in spans[:-1])


def test_bigger_part_of_the_book_gets_more_cards_and_cases_stay_fixed():
    text = _book()
    index = cv.SourceIndex(text)
    assert index.usable
    sizes = cv.node_source_sizes(index, _nodes())
    assert sizes["courts"] > 2 * sizes["notary"] > 0
    assert sizes["case"] == 0                               # кейсы не «владеют» кусками книги
    quotas = cv.card_quotas(_nodes(), sizes, chars_per_card=2300)
    assert quotas["courts"] > quotas["notary"] >= 3
    assert quotas["case"] == 3
    assert max(quotas.values()) <= cv.MAX_CARDS_PER_NODE


def test_total_cap_shrinks_quotas_but_keeps_minimum():
    nodes = [{"key": f"n{i}", "name": f"Тема {i}", "tier": 2} for i in range(20)]
    quotas = cv.card_quotas(nodes, {n["key"]: 40_000 for n in nodes}, total_cap=100)
    assert sum(quotas.values()) <= 100 and min(quotas.values()) >= 3


def test_short_or_wordless_text_is_not_indexed():
    assert not cv.SourceIndex("КНИГА").usable
    assert not cv.SourceIndex("字" * 20000).usable          # иероглифы: считать нечего, будут обычные диапазоны
    short = pb.plan_card_quotas("КНИГА", {"nodes": _nodes()})
    assert short and min(short.values()) >= 1                    # индекса нет, но квоты раскладываются поровну (цель по типу материала действует)


def test_card_density_finds_uncovered_parts():
    text = _book()
    index = cv.SourceIndex(text)
    cards = [{"text": "Кто назначает судью районного суда?", "translation": "Президент.", "example": ""}] * 3
    dens = cv.card_density(index, cards)
    assert sum(dens) == 3 and dens[-1] == 0               # последнее окно — про нотариат, карточек нет


def test_pack_task_carries_target_cards_and_pipeline_passes_them():
    task = build_cards_task("{}", ["courts", "notary"], {"courts": 8, "notary": 3, "other": 5})
    assert "TARGET CARDS: courts=8, notary=3\n" in task
    assert "TARGET CARDS" not in build_cards_task("{}", ["courts"])

    text = _book() * 3
    raw_map = {"title": "T", "domain": "law", "nodes": [
        {"key": "b", "name": "Основа", "tier": 0, "order": 1, "summary": "s", "src": "1"},
        {"key": "t", "name": "Районный суд", "tier": 1, "order": 2, "summary": "Суд первой инстанции", "src": "2"},
        {"key": "n", "name": "Нотариат", "tier": 1, "order": 3, "summary": "Нотариус удостоверяет сделки", "src": "3"},
    ] + [{"key": f"s{i}", "name": f"Подтема {i}", "tier": 2, "parent": "t", "order": 3 + i, "summary": "s", "src": "4"} for i in range(1, 9)],
        "edges": []}
    from llm_fake import FakeLLM, patched
    fake = FakeLLM(raw_map=raw_map)
    with patched(fake):
        res = asyncio.run(pb.build_learning_path(text, "s"))
    seen = fake.of("CARDS")
    assert res["quotas"] and {"b", "t", "n"} <= set(res["quotas"])
    assert seen and all("TARGET CARDS:" in p for p in seen)


def test_lesson_alignment_counts_answers_present_in_lesson():
    lesson = {"screens": [{"say": "Президент назначает председателя Конституционного Суда."},
                          {"say": "Срок полномочий составляет пять лет."}], "check": []}
    packs = {"a": {"lesson": lesson, "cards": [
        {"text": "Кто назначает председателя?", "translation": "Президент."},                    # есть в уроке
        {"text": "Какой срок полномочий?", "translation": "Пять лет."},                          # есть в уроке
        {"text": "Сколько судей в коллегии?", "translation": "Трое профессиональных судей."},    # нет
    ]}, "b": {"lesson": None, "cards": []}}
    st = cv.lesson_alignment(packs)
    assert st["cards"] == 3 and st["full_share"] == 0.667 and st["none_share"] == 0.333
    assert st["worst_nodes"] == ["a"]
    assert cv.lesson_alignment({})["cards"] == 0


def test_lesson_prompt_scales_with_target_cards_and_demands_alignment():
    from app.services.ai_gateway.path_prompts import PATH_BUILDER_SYSTEM_PROMPT as sp
    assert "ALIGNMENT LAW" in sp and "ceil(N/3)+2 screens" in sp and "at most 30 words" in sp
    assert pb._normalize_lesson({"screens": [{"say": f"Экран {i}"} for i in range(12)]}, set())["screens"].__len__() == 10
