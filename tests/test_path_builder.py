import asyncio
from unittest.mock import patch

import pytest

from app.services.ai_gateway import path_builder as pb
from app.services.ai_gateway.path_prompts import PATH_BUILDER_SYSTEM_PROMPT, build_source_block


RAW_MAP = {
    "title": "Судоустройство",
    "domain": "law",
    "nodes": [
        {"key": "vlast", "name": "Судебная власть", "tier": 0, "order": 1},
        {"key": "instanciya", "name": "Инстанция", "tier": 0, "order": 2, "prereqs": ["apellyaciya"]},
        {"key": "peresmotr", "name": "Пересмотр решений", "tier": 1, "order": 3},
        {"key": "apellyaciya", "name": "Апелляция", "tier": 2, "parent": "peresmotr", "order": 4},
        {"key": "nadzor", "name": "Надзор", "tier": 2, "parent": "peresmotr", "order": 5, "prereqs": ["nadzor"]},
        {"key": "keys_peresmotr", "name": "Кейсы пересмотра", "tier": 3, "parent": "peresmotr", "order": 6},
        {"key": "vlast", "name": "Дубликат", "tier": 1},
        {"key": "", "name": "Без ключа", "tier": 1},
        {"key": "bad_parent", "name": "Плохой родитель", "tier": 1, "parent": "apellyaciya", "order": 7},
    ],
    "edges": [
        {"from": "apellyaciya", "to": "nadzor", "relation": "demarcated_from", "label": "разграничивается с"},
        {"from": "apellyaciya", "to": "nadzor", "relation": "demarcated_from", "label": "дубль"},
        {"from": "apellyaciya", "to": "ghost", "relation": "leads_to"},
        {"from": "vlast", "to": "vlast", "relation": "part_of"},
        {"from": "vlast", "to": "instanciya", "relation": "weird"},
    ],
}


def test_normalize_map_invariants():
    m = pb.normalize_map(RAW_MAP)
    by_key = {n["key"]: n for n in m["nodes"]}
    assert set(by_key) == {"vlast", "instanciya", "peresmotr", "apellyaciya", "nadzor", "keys_peresmotr", "bad_parent"}
    assert by_key["vlast"]["name"] == "Судебная власть"
    # Ярус 0 без пререквизитов, даже если LLM их указал
    assert by_key["instanciya"]["prereqs"] == []
    # Ярус 1 без пререквизитов получает все основы
    assert set(by_key["peresmotr"]["prereqs"]) == {"vlast", "instanciya"}
    # Самоссылка удалена, родитель добавлен
    assert by_key["nadzor"]["prereqs"] == ["peresmotr"]
    # Кейсы зависят от подтем своей ветки
    assert set(by_key["keys_peresmotr"]["prereqs"]) == {"apellyaciya", "nadzor"}
    # Родитель с ярусом не ниже — отброшен
    assert by_key["bad_parent"]["parent"] is None
    # Все пререквизиты раньше по порядку => граф ациклический
    order = {n["key"]: n["order"] for n in m["nodes"]}
    for n in m["nodes"]:
        assert all(order[p] < order[n["key"]] for p in n["prereqs"])
    assert [(e["from"], e["to"], e["relation"]) for e in m["edges"]] == [
        ("apellyaciya", "nadzor", "demarcated_from"),
        ("vlast", "instanciya", "depends_on"),
    ]


def test_normalize_map_requires_foundation():
    with pytest.raises(pb.PathBuildError):
        pb.normalize_map({"nodes": [{"key": "a", "name": "A", "tier": 1}]})


def test_plan_pack_batches_groups_by_branch():
    m = pb.normalize_map(RAW_MAP)
    batches = pb.plan_pack_batches(m, max_nodes=6)
    assert batches[0] == ["vlast", "instanciya"]
    assert batches[1] == ["peresmotr", "apellyaciya", "nadzor", "keys_peresmotr"]
    assert sorted(k for b in batches for k in b) == sorted(n["key"] for n in m["nodes"])


def test_normalize_pack_filters_distractors_and_cards():
    m = pb.normalize_map(RAW_MAP)
    raw = {"nodes": [
        {
            "key": "apellyaciya",
            "lesson": {
                "screens": [
                    {"say": "Раз", "emo": "surprised", "focus": ["nadzor", "ghost"]},
                    {"say": "Два", "emo": "dancing"},
                    {"say": "Три"},
                ],
                "check": [
                    {"q": "Q?", "options": ["a", "b", "c"], "answer": 1, "why": "w"},
                    {"q": "Bad", "options": ["a", "b"], "answer": 5},
                ],
            },
            "cards": [
                {"t": "Какая инстанция пересматривает не вступившие в силу решения суда первой инстанции?",
                 "s": "ГПК | Пересмотр", "d": "Апелляционная инстанция.", "l": "medium", "y": 1, "at": "organ",
                 "x": ["Кассационная инстанция.", "Апелляционная инстанция", "Все перечисленные.", "Надзорная инстанция."]},
                {"t": "Вправе ли суд отменить решение по жалобе стороны?", "d": "Да.", "x": []},
                {"t": "Какой срок подачи апелляционной жалобы установлен законом?", "d": "Десять дней.",
                 "at": "duration", "x": ["Месяц.", "Десять дней"]},
            ],
        },
        {"key": "vlast", "lesson": None, "cards": []},
    ]}
    res = pb.normalize_pack(raw, m, ["apellyaciya"])
    assert list(res) == ["apellyaciya"]
    lesson = res["apellyaciya"]["lesson"]
    assert [s["emo"] for s in lesson["screens"]] == ["surprised", "talk", "talk"]
    assert lesson["screens"][0]["focus"] == ["nadzor"]
    assert len(lesson["check"]) == 1
    cards = res["apellyaciya"]["cards"]
    assert len(cards) == 2  # «Да.» отфильтрован блэклистом
    assert cards[0]["distractors"] == ["Кассационная инстанция.", "Надзорная инстанция."]
    assert cards[0]["node_key"] == "apellyaciya" and cards[0]["answer_type"] == "organ"
    # Меньше двух годных дистракторов => карточка не идёт в MCQ
    assert cards[1]["distractors"] is None


def test_lesson_with_too_few_screens_is_rejected():
    m = pb.normalize_map(RAW_MAP)
    raw = {"nodes": [{"key": "vlast", "lesson": {"screens": [{"say": "one"}, {"say": "two"}]}, "cards": []}]}
    assert pb.normalize_pack(raw, m, ["vlast"])["vlast"]["lesson"] is None


def test_prompt_prefix_is_stable_for_cache():
    text = "Глава 1. Судебная власть..."
    assert build_source_block(text) == build_source_block(text)
    assert "{" not in PATH_BUILDER_SYSTEM_PROMPT.split("PART A")[0]  # статичный заголовок без подстановок
    # Правила карточек из старого промпта сохранены
    for rule in ("Minimum Information Principle", "Absolute Prohibition of Lists & Enumerations", "Zero-Spoiler Law", "Yes/No"):
        assert rule in PATH_BUILDER_SYSTEM_PROMPT


def test_build_learning_path_uses_shared_prefix_and_retries_missing():
    m_raw = {"title": "T", "domain": "law", "nodes": [
        {"key": "a", "name": "A", "tier": 0, "order": 1},
        {"key": "b", "name": "B", "tier": 1, "order": 2},
    ], "edges": []}
    lesson = {"screens": [{"say": "1"}, {"say": "2"}, {"say": "3"}], "check": []}
    prompts = []

    async def fake_call(user_prompt, **kwargs):
        prompts.append(user_prompt)
        meta = {"cost_usd": 0.01, "cache_hit_tokens": 0, "completion_tokens": 10}
        if "TYPE: MAP" in user_prompt:
            return m_raw, meta
        # Первый пакет для «b» теряем, чтобы проверить дозапрос
        if "NODES TO PRODUCE: b" in user_prompt and sum("NODES TO PRODUCE: b" in p for p in prompts) == 1:
            return {"nodes": []}, meta
        keys = user_prompt.split("NODES TO PRODUCE: ")[1].split("\n")[0].split(", ")
        return {"nodes": [{"key": k, "lesson": lesson, "cards": []} for k in keys]}, meta

    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call):
        res = asyncio.run(pb.build_learning_path("КНИГА", "subj"))

    assert set(res["packs"]) == {"a", "b"} and res["missing_nodes"] == []
    prefix = build_source_block("КНИГА")
    assert all(p.startswith(prefix) for p in prompts)
    assert res["cost_usd"] == pytest.approx(0.01 * len(prompts))


def test_truncated_map_is_not_retried_and_cost_is_logged():
    from app.services.ai_gateway.client import LLMOutputTruncated
    calls = []

    async def fake_call(user_prompt, **kwargs):
        raise LLMOutputTruncated("обрезано", {"cost_usd": 0.05, "finish_reason": "length"})

    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call) as mocked:
        with pytest.raises(pb.PathBuildError):
            asyncio.run(pb.build_knowledge_map("КНИГА", "s", calls))
    assert mocked.call_count == 1
    assert calls[0]["cost_usd"] == 0.05 and calls[0]["finish_reason"] == "length"


def test_truncated_pack_is_split_in_halves():
    from app.services.ai_gateway.client import LLMOutputTruncated
    m = pb.normalize_map({"nodes": [{"key": k, "name": k.upper(), "tier": 0, "order": i} for i, k in enumerate("abcd", 1)]})
    lesson = {"screens": [{"say": "1"}, {"say": "2"}, {"say": "3"}]}
    requested = []

    async def fake_call(user_prompt, **kwargs):
        keys = user_prompt.split("NODES TO PRODUCE: ")[1].split("\n")[0].split(", ")
        requested.append(keys)
        if len(keys) > 2:
            raise LLMOutputTruncated("обрезано", {"cost_usd": 0.01})
        return {"nodes": [{"key": k, "lesson": lesson, "cards": []} for k in keys]}, {"cost_usd": 0.01}

    calls = []
    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call):
        res = asyncio.run(pb.build_node_pack("КНИГА", m, ["a", "b", "c", "d"], calls))
    assert set(res) == {"a", "b", "c", "d"}
    assert requested == [["a", "b", "c", "d"], ["a", "b"], ["c", "d"]]
    assert len(calls) == 3


def test_checks_are_shuffled_and_card_duplicates_removed():
    checks = [{"q": f"Вопрос {i}?", "options": ["верный", "неверный 1", "неверный 2"], "answer": 0, "why": ""} for i in range(40)]
    checks.append({"q": "Какая инстанция пересматривает решения?", "options": ["a", "b", "c"], "answer": 0, "why": ""})
    fronts = {pb._norm_text("Какая инстанция пересматривает решения?")}
    final = []
    for i in range(0, 40, 2):
        final += pb._finalize_checks(checks[i:i + 2], fronts, f"node_{i}")
    final += pb._finalize_checks(checks[40:], fronts, "dup")
    assert len(final) == 40  # дубль карточки удалён
    assert all(c["options"][c["answer"]] == "верный" for c in final)
    assert len({c["answer"] for c in final}) == 3  # верный ответ больше не всегда первый


def test_subtopics_of_one_branch_are_not_chained():
    m = pb.normalize_map({"nodes": [
        {"key": "base", "name": "Основа", "tier": 0, "order": 1},
        {"key": "other", "name": "Другая тема", "tier": 1, "order": 2},
        {"key": "branch", "name": "Тема", "tier": 1, "order": 3},
        {"key": "s1", "name": "Подтема 1", "tier": 2, "parent": "branch", "order": 4},
        {"key": "s2", "name": "Подтема 2", "tier": 2, "parent": "branch", "order": 5, "prereqs": ["branch", "s1", "other"]},
        {"key": "case", "name": "Кейс", "tier": 3, "parent": "branch", "order": 6, "prereqs": ["s1", "s2"]},
    ]})
    by_key = {n["key"]: n for n in m["nodes"]}
    assert by_key["s2"]["prereqs"] == ["branch", "other"]  # соседка s1 убрана, межветочная связь сохранена
    assert by_key["case"]["prereqs"] == ["s1", "s2"]


def test_degenerate_map_without_topics_is_retried_with_higher_temperature():
    good = _map_raw(5)
    bad = {"nodes": [{"key": "a", "name": "A", "tier": 0, "order": 1}]}
    temps = []

    async def fake_call(user_prompt, **kwargs):
        temps.append(kwargs.get("temperature"))
        return (bad if len(temps) == 1 else good), {"cost_usd": 0.01}

    calls = []
    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call):
        m = asyncio.run(pb.build_knowledge_map("x" * pb.MIN_SOURCE_CHARS_FOR_TOPICS, "s", calls))
    assert len(m["nodes"]) == 10
    assert temps == [0.1, pb.RETRY_TEMPERATURE]
    assert len(calls) == 2
    assert pb.RETRY_TEMPERATURE >= 0.7


def test_cross_links_keep_only_core_nodes_and_skip_duplicates():
    m = pb.normalize_map(RAW_MAP)
    raw = {"edges": [
        {"from": "vlast", "to": "peresmotr", "relation": "leads_to", "label": "ведёт к"},
        {"from": "peresmotr", "to": "vlast", "relation": "leads_to", "label": "дубль в обратную сторону"},
        {"from": "vlast", "to": "apellyaciya", "relation": "leads_to", "label": "подтема — не ядро"},
        {"from": "vlast", "to": "instanciya", "relation": "depends_on", "label": "уже есть в карте"},
        {"from": "peresmotr", "to": "bad_parent", "relation": "kind_of", "label": "тема–тема"},
    ]}

    async def fake_call(user_prompt, **kwargs):
        assert "TYPE: LINKS" in user_prompt
        return raw, {"cost_usd": 0.004}

    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call):
        links = asyncio.run(pb.build_cross_links("КНИГА", m, []))
    assert [(e["from"], e["to"]) for e in links] == [("vlast", "peresmotr"), ("peresmotr", "bad_parent")]


# --- Карта дважды, зависания, ядро в режиме обдумывания ---

def _map_raw(n_topics):
    nodes = [{"key": f"b{i}", "name": f"Основа {i}", "tier": 0, "order": i, "summary": "s", "src": "гл.1"} for i in range(1, 6)]
    nodes += [{"key": f"t{i}", "name": f"Тема {i}", "tier": 1, "order": 10 + i, "summary": "s", "src": "гл.2"} for i in range(1, n_topics + 1)]
    return {"title": "T", "domain": "law", "nodes": nodes, "edges": []}


def test_second_map_is_kept_only_if_it_scores_higher():
    weak, strong = _map_raw(2), _map_raw(6)
    answers = iter([weak, strong])

    async def fake_call(user_prompt, **kwargs):
        return next(answers), {"cost_usd": 0.01}

    calls = []
    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call):
        m = asyncio.run(pb.build_knowledge_map("x" * pb.MIN_SOURCE_CHARS_FOR_TOPICS, "s", calls, candidates=2))
    assert sum(1 for n in m["nodes"] if n["tier"] == 1) == 6          # альтернатива лучше (темы в диапазоне 5–10)
    assert [c["label"] for c in calls] == ["map#1", "map#alt2"]
    assert pb.score_map(pb.normalize_map(strong)) > pb.score_map(pb.normalize_map(weak))

    # Сбой второй карты не ломает первую
    state = {"n": 0}

    async def flaky(user_prompt, **kwargs):
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("API")
        return strong, {"cost_usd": 0.01}

    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=flaky):
        m = asyncio.run(pb.build_knowledge_map("x" * pb.MIN_SOURCE_CHARS_FOR_TOPICS, "s", [], candidates=2))
    assert len(m["nodes"]) == 11


def test_hang_is_retried_with_same_model_then_fallback_model():
    seen = []

    async def fake_call(user_prompt, **kwargs):
        seen.append(kwargs.get("model"))
        if len(seen) < 3:
            raise asyncio.TimeoutError()
        return {"ok": True}, {"cost_usd": 0.0}

    calls = []
    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call), \
            patch.object(pb, "HANG_BACKOFF_S", 0):
        res = asyncio.run(pb._call("p", 100, "t", calls))
    assert res == {"ok": True}
    assert seen == [None, None, "deepseek-v4-pro"]       # две попытки основной моделью, затем страховочная
    assert sum(1 for c in calls if c["error"]) == 2

    async def always_hang(user_prompt, **kwargs):
        raise asyncio.TimeoutError()

    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=always_hang), \
            patch.object(pb, "HANG_BACKOFF_S", 0):
        with pytest.raises(RuntimeError):
            asyncio.run(pb._call("p", 100, "t", []))


def test_core_batches_are_separate_and_use_thinking():
    m = pb.normalize_map(RAW_MAP)
    batches = pb.plan_pack_batches(m, max_nodes=6, split_core=True)
    assert batches[0] == ["vlast", "instanciya"] and batches[1] == ["peresmotr", "bad_parent"]
    assert batches[2] == ["apellyaciya", "nadzor", "keys_peresmotr"]
    assert sorted(k for b in batches for k in b) == sorted(n["key"] for n in m["nodes"])

    flags = {}

    async def fake_call(user_prompt, **kwargs):
        if "TYPE: MAP" in user_prompt:
            return _map_raw(5), {"cost_usd": 0.0}
        if "TYPE: NODE_PACK" in user_prompt:
            keys = user_prompt.split("NODES TO PRODUCE: ")[1].split("\n")[0].split(", ")
            flags[tuple(keys)] = kwargs.get("thinking", False)
            lesson = {"screens": [{"say": "1"}, {"say": "2"}, {"say": "3"}], "check": []}
            return {"nodes": [{"key": k, "lesson": lesson, "cards": []} for k in keys]}, {"cost_usd": 0.0}
        return {"edges": [], "lesson": None}, {"cost_usd": 0.0}

    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call), patch.object(pb, "THINK_CORE", True):
        asyncio.run(pb.build_learning_path("КНИГА", "s"))
    assert flags and all(flags.values())                 # в тестовой карте только основы и темы — всё в режиме обдумывания


def test_degenerate_map_retry_adds_nudge_only_to_the_tail():
    bad = {"nodes": [{"key": "a", "name": "A", "tier": 0, "order": 1}]}
    prompts = []

    async def fake_call(user_prompt, **kwargs):
        prompts.append((user_prompt, kwargs.get("temperature")))
        return (_map_raw(5) if len(prompts) == 3 else bad), {"cost_usd": 0.0}

    text = "x" * pb.MIN_SOURCE_CHARS_FOR_TOPICS
    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call):
        asyncio.run(pb.build_knowledge_map(text, "s", []))
    assert [t for _, t in prompts] == [0.1, pb.RETRY_TEMPERATURE, pb.LAST_RETRY_TEMPERATURE]
    assert pb.DEGENERATE_MAP_NUDGE not in prompts[0][0] and all(pb.DEGENERATE_MAP_NUDGE in p for p, _ in prompts[1:])
    prefix = build_source_block(text)
    assert all(p.startswith(prefix) for p, _ in prompts)          # кэшируемый префикс не меняется


def test_thin_map_for_a_big_book_is_rejected_and_retried():
    big = "x" * 300_000
    thin = _map_raw(5)                       # 10 узлов: основы и темы, подтем нет — для книги мало
    full = _map_raw(5)
    full["nodes"] += [{"key": f"s{i}", "name": f"Подтема {i}", "tier": 2, "parent": "t1", "order": 30 + i,
                       "summary": "s", "src": "гл.3"} for i in range(1, 11)]
    answers = iter([thin, full, full])

    async def fake_call(user_prompt, **kwargs):
        return next(answers), {"cost_usd": 0.0}

    assert pb.map_problem(pb.normalize_map(thin), len(big)) and pb.map_problem(pb.normalize_map(full), len(big)) is None
    assert pb.map_problem(pb.normalize_map(thin), 20_000) is None      # короткий текст — достаточно тем
    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call):
        m = asyncio.run(pb.build_knowledge_map(big, "s", []))
    assert sum(1 for n in m["nodes"] if n["tier"] == 2) == 10


def test_prompt_asks_for_specifics_and_varied_questions_without_dynamic_parts():
    from app.services.ai_gateway.path_prompts import PATH_BUILDER_SYSTEM_PROMPT as sp
    for marker in ("B2b. WHAT AN EXAM REALLY ASKS", "QUESTION VARIETY", "terms of office", "doses and routes",
                   "dates; persons", "history|science|language|generic"):
        assert marker in sp
    assert "{" not in sp.split("PART A")[0]               # статичная шапка для кэша
    for rule in ("Minimum Information Principle", "Absolute Prohibition of Lists & Enumerations", "Zero-Spoiler Law"):
        assert rule in sp                                  # прежние законы карточек на месте


def test_question_opener_stats():
    cards = [{"text": "Какой орган назначает судей?", "translation": "Президент."},
             {"text": "Какой орган избирает председателя?", "translation": "Съезд судей."},
             {"text": "Сколько судей в коллегии?", "translation": "Три судьи, 3."},
             {"text": "На какой срок выдаётся лицензия?", "translation": "На 5 лет."}]
    st = pb.question_opener_stats(cards)
    assert st["top_opener"] == "какой орган" and st["top_opener_share"] == 0.5 and st["numeric_answers_share"] == 0.5
    assert pb.question_opener_stats([])["cards"] == 0
