"""Проверка охвата по оглавлению: разделы книги без узла → запрос GAPS → новые узлы."""
import asyncio
from unittest.mock import patch

from app.services.ai_gateway import coverage as cv
from app.services.ai_gateway import path_builder as pb
from app.services.ai_gateway.path_prompts import build_gaps_task, PATH_BUILDER_SYSTEM_PROMPT


def _word(tag):
    """Слово только из букв (цифры в токены не попадают): «суд» + буквы по номеру главы/раздела."""
    return "суд" + "".join(chr(1072 + int(c)) for c in str(tag) if c.isdigit())


def _body(word, chars=9000):
    word = _word(word)
    return (f"{word} " * (chars // (len(word) + 1))) + "\n"


def _book_paragraphs():
    """Две главы с § -нумерацией, оглавление в конце повторяет заголовки заглавными буквами."""
    parts = ["Предисловие\n" + _body("введение", 1500)]
    for ch in (1, 2):
        for p in range(1, 4):
            parts.append(f"§ {p}. Раздел {ch}-{p} про суды\n" + _body(f"суд{ch}{p}", 9000))
    parts.append("СОДЕРЖАНИЕ\n")
    for ch in (1, 2):
        for p in range(1, 4):
            parts.append(f"§ {p}. РАЗДЕЛ {ch}-{p} ПРО СУДЫ 12\n")
    return "".join(parts)


def _book_decimal():
    parts = []
    for ch in (1, 2, 3):
        for p in (1, 2):
            parts.append(f"{ch}.{p}. Параграф {ch}.{p} о праве\n" + _body(f"право{ch}{p}", 9000))
    return "".join(parts)


def test_sections_are_extracted_from_both_numbering_styles_without_toc_duplicates():
    secs = cv.extract_sections(_book_paragraphs())
    assert [(s["chapter"], s["section"]) for s in secs] == [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)]
    assert all(s["chars"] >= 8000 for s in secs[:-1])
    dec = cv.extract_sections(_book_decimal())
    assert [(s["chapter"], s["section"]) for s in dec] == [(1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2)]
    assert cv.extract_sections("Просто текст без заголовков.\n" * 500) == []


def test_src_references_are_parsed_in_several_formats():
    assert cv.parse_src_refs("Гл. 14, § 3") == {(14, 3)}
    assert cv.parse_src_refs("Гл. 4, §4.3") == {(4, 3)}
    assert cv.parse_src_refs("§11.2–11.3") == {(11, 2), (11, 3)}
    assert cv.parse_src_refs("Гл. 5, §2–4") == {(5, 2), (5, 3), (5, 4)}
    assert cv.parse_src_refs("Гл. 5") == set()                       # ссылка на главу целиком разделов не закрывает
    assert cv.parse_src_refs("") == set()


def test_uncovered_sections_are_the_big_ones_no_node_cites():
    secs = cv.extract_sections(_book_paragraphs())
    nodes = [{"src": "Гл. 1, § 1"}, {"src": "Гл. 1, §2–3"}, {"src": "Гл. 2, § 1"}]
    unc = cv.uncovered_sections(secs, nodes)
    assert [(s["chapter"], s["section"]) for s in unc] == [(2, 2), (2, 3)]   # последний раздел вместе с хвостом оглавления тоже крупный


def _map():
    return pb.normalize_map({"title": "Т", "domain": "law", "nodes": [
        {"key": "b1", "name": "Основа", "tier": 0, "order": 1, "summary": "s", "src": "Гл. 1, § 1"},
        {"key": "topic_a", "name": "Тема А", "tier": 1, "order": 2, "summary": "s", "src": "Гл. 1, § 2"},
        {"key": "topic_b", "name": "Тема Б", "tier": 1, "order": 3, "summary": "s", "src": "Гл. 2, § 1"},
        {"key": "a1", "name": "Подтема А1", "tier": 2, "parent": "topic_a", "order": 4, "summary": "s", "src": "Гл. 1, § 3"},
        {"key": "b2", "name": "Подтема Б1", "tier": 2, "parent": "topic_b", "order": 5, "summary": "s", "src": "Гл. 2, § 3"},
    ], "edges": []})


def test_gap_answer_inserts_nodes_after_siblings_and_fixes_bad_parents():
    path_map = _map()
    secs = [{"chapter": 2, "section": 2, "title": "Раздел 2-2 про суды", "start": 0, "end": 9000, "chars": 9000},
            {"chapter": 1, "section": 4, "title": "Уже покрыто", "start": 9000, "end": 18000, "chars": 9000},
            {"chapter": 1, "section": 5, "title": "Литература", "start": 18000, "end": 27000, "chars": 9000}]
    raw = {"gaps": [
        {"id": "S1", "covered_by": [], "nodes": [{"key": "novyy_uzel", "name": "Новый узел", "parent": "ghost", "prereqs": ["a1", "nope"], "summary": "Кратко."}]},
        {"id": "S2", "covered_by": ["a1"], "nodes": []},
        {"id": "S3", "covered_by": [], "nodes": []},
    ]}
    merged, hints, report = pb.apply_gap_answer(path_map, raw, secs)
    by_key = {n["key"]: n for n in merged["nodes"]}
    new = by_key["novyy_uzel"]
    assert new["tier"] == 2 and new["parent"] == "topic_b"                # «ghost» заменён темой из той же главы (Гл. 2)
    assert new["src"] == "Гл. 2, §2" and "a1" in new["prereqs"] and "nope" not in new["prereqs"]
    order = [n["key"] for n in merged["nodes"]]
    assert order.index("novyy_uzel") > order.index("b2")                  # после сестринской подтемы
    assert hints == {"novyy_uzel": 9000}
    assert report == {"sections": 3, "covered": 1, "added": 1, "skipped": 1}
    same, none_hints, rep2 = pb.apply_gap_answer(path_map, {"gaps": []}, secs)
    assert same is path_map and none_hints == {}


def test_fill_map_gaps_calls_model_once_and_skips_unusable_src():
    text = _book_paragraphs()
    secs = cv.extract_sections(text)
    path_map = _map()
    prompts = []

    async def fake_call(user_prompt, **kwargs):
        prompts.append(user_prompt)
        return {"gaps": [{"id": "S1", "covered_by": [], "nodes": [{"key": "poryadok", "name": "Порядок", "parent": "topic_b", "summary": "."}]}]}, {"cost_usd": 0.0}

    calls = []
    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call):
        merged, hints, report = asyncio.run(pb.fill_map_gaps(text, path_map, calls))
    assert len(prompts) == 1 and "TYPE: GAPS" in prompts[0] and prompts[0].startswith(pb.build_source_block(text))
    assert "S1 | Гл. 2, §2" in prompts[0] and report["added"] == 1 and "poryadok" in hints

    # Узлы ссылаются на формат, который не распознаётся → проверку пропускаем без вызовов
    broken = pb.normalize_map({"nodes": [{"key": "b", "name": "Основа", "tier": 0, "order": 1, "src": "стр. 31"},
                                           {"key": "t", "name": "Тема", "tier": 1, "order": 2, "src": "стр. 40"}], "edges": []})
    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call) as mocked:
        same, hints, report = asyncio.run(pb.fill_map_gaps(text, broken, []))
    assert mocked.call_count == 0 and same is broken and report is None

    # Сбой запроса не ломает нарезку
    async def boom(*a, **k):
        raise RuntimeError("API")

    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=boom), patch.object(pb, "HANG_RETRIES", 0):
        same, hints, report = asyncio.run(pb.fill_map_gaps(text, path_map, []))
    assert same is path_map and hints == {} and "error" in report


def test_gaps_prompt_is_static_and_task_goes_to_the_tail():
    assert 'PART E. TASK "GAPS"' in PATH_BUILDER_SYSTEM_PROMPT
    assert "{" not in PATH_BUILDER_SYSTEM_PROMPT.split("PART A")[0]
    task = build_gaps_task("{}", "S1 | Гл. 1, §1 | «Т» | ~9 тыс. знаков | begins: «...»")
    assert task.endswith("Return only the GAPS JSON.") and "TYPE: GAPS" in task


def test_pipeline_adds_gap_nodes_with_size_hints_into_quotas():
    text = _book_paragraphs() * 1
    raw_map = {"title": "T", "domain": "law", "nodes": [
        {"key": "b1", "name": "Основа", "tier": 0, "order": 1, "summary": "s", "src": "Гл. 1, § 1"},
        {"key": "topic_a", "name": "Тема А", "tier": 1, "order": 2, "summary": "s", "src": "Гл. 1, § 2"},
        {"key": "topic_b", "name": "Тема Б", "tier": 1, "order": 3, "summary": "s", "src": "Гл. 2, § 1"},
    ] + [{"key": f"s{i}", "name": f"Подтема {i}", "tier": 2, "parent": "topic_a", "order": 3 + i, "summary": "s", "src": "Гл. 1, § 3"} for i in range(1, 5)],
        "edges": []}

    async def fake_call(user_prompt, **kwargs):
        if "TYPE: MAP" in user_prompt:
            return raw_map, {"cost_usd": 0.0}
        if "TYPE: GAPS" in user_prompt:
            return {"gaps": [{"id": "S1", "covered_by": [], "nodes": [{"key": "dyra", "name": "Дыра", "parent": "topic_b", "summary": "."}]}]}, {"cost_usd": 0.0}
        if "TYPE: NODE_PACK" in user_prompt:
            keys = user_prompt.split("NODES TO PRODUCE: ")[1].split("\n")[0].split(", ")
            lesson = {"screens": [{"say": "1"}, {"say": "2"}, {"say": "3"}], "check": []}
            return {"nodes": [{"key": k, "lesson": lesson, "cards": []} for k in keys]}, {"cost_usd": 0.0}
        return {"edges": [], "lesson": None}, {"cost_usd": 0.0}

    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call):
        res = asyncio.run(pb.build_learning_path(text, "s"))
    keys = {n["key"] for n in res["map"]["nodes"]}
    assert "dyra" in keys and "dyra" in res["packs"]                      # узел прошёл нарезку как остальные
    assert res["gap_report"]["added"] == 1
    assert res["quotas"]["dyra"] >= 3
