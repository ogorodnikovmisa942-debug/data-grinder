"""Баланс колоды: деньги задают размер, важность — распределение; ключевые мысли несут то, на что карточек не хватило."""
import asyncio
import re

import test_pipeline_quality as tpq
from app.services.ai_gateway import coverage as cv
from app.services.ai_gateway.card_quality import CardVerifier, process_facts
from app.services.ai_gateway import path_builder as pb
from app.services.ai_gateway import path_prompts as pp
from app.services.ai_gateway.budget import Budget
from llm_fake import FakeLLM, keys_of, node_block, patched


def _nodes(n2: int = 10):
    return ([{"key": "b0", "tier": 0}, {"key": "t1", "tier": 1}]
            + [{"key": f"s{i}", "tier": 2} for i in range(n2)]
            + [{"key": "c1", "tier": 3}, {"key": "c2", "tier": 3}])


# --- распределение --------------------------------------------------------------------

def test_allocation_hits_the_total_exactly_and_keeps_tier_floors():
    nodes = _nodes()
    sizes = {n["key"]: 8000 + 3000 * i for i, n in enumerate(nodes)}
    q = cv.allocate_cards(nodes, sizes, 60)
    assert sum(q.values()) == 60
    assert q["b0"] >= cv.TIER_FLOOR[0] and q["t1"] >= cv.TIER_FLOOR[1] and all(q[f"s{i}"] >= 1 for i in range(10))
    assert q["s9"] > q["s0"]                                     # крупный кусок книги получает больше


def test_allocation_is_sublinear_in_size_and_does_not_feed_tiny_nodes_by_floors():
    nodes = [{"key": "big", "tier": 2}, {"key": "small", "tier": 2}]
    q = cv.allocate_cards(nodes, {"big": 40_000, "small": 4_000}, 40)
    assert sum(q.values()) == 40
    assert q["big"] / q["small"] < 40_000 / 4_000               # степень 0,85: длинный кусок — не пропорционально больше фактов
    assert q["big"] > q["small"] >= 1


def test_cases_get_a_small_share_and_never_below_one():
    nodes = _nodes()
    q = cv.allocate_cards(nodes, {n["key"]: 10_000 for n in nodes}, 200)
    assert q["c1"] == q["c2"] == 3                                # денег много: обычная норма кейса
    q = cv.allocate_cards(nodes, {n["key"]: 10_000 for n in nodes}, 20)
    assert q["c1"] == q["c2"] == 1 and sum(q.values()) == 20      # денег мало: кейсы урезаны первыми (≤ 8% колоды), остальное — узлам


def test_not_enough_for_floors_means_one_card_per_node_not_zero():
    nodes = _nodes()
    q = cv.allocate_cards(nodes, {n["key"]: 5000 for n in nodes}, 3)
    assert min(q.values()) >= 1 and sum(q.values()) <= len(nodes) + 3


def test_per_node_max_clips_only_when_asked():
    nodes = [{"key": "a", "tier": 2}, {"key": "b", "tier": 2}]
    assert cv.allocate_cards(nodes, {"a": 90_000, "b": 1000}, 50)["a"] > 12
    assert cv.allocate_cards(nodes, {"a": 90_000, "b": 1000}, 50, max_cards=12)["a"] == 12


def test_target_is_one_card_per_page_and_never_zero():
    assert cv.target_cards(1_135_000, 2300) == 493
    assert cv.target_cards(100, 2300) == 1 and cv.target_cards(0, 2300) == 1


def test_block_fact_targets_follow_length_and_weight_hit_the_total_and_skip_non_teaching_blocks():
    blocks = [(i * 3000, (i + 1) * 3000) for i in range(5)]
    usable = [True, True, False, True, True]
    t = cv.block_fact_targets(blocks, [1.0, 2.0, 1.0, 1.0, 0.5], usable, 20)
    assert sum(t) == 20 and t[2] == 0                              # оглавление, литература: фактов нет
    assert t[1] > t[0] > t[4]                                      # насыщенный блок получает больше, водянистый меньше
    assert cv.block_fact_targets(blocks, [1.0] * 5, usable, 0) == [0] * 5
    few = cv.block_fact_targets(blocks, [1.0] * 5, usable, 5)
    assert all(k >= 1 for k, ok in zip(few, usable) if ok)         # у каждого учебного блока хоть один факт


def test_fact_blocks_cover_the_whole_text_in_order():
    book = tpq._book()
    spans = cv.fact_blocks(book)
    assert spans[0][0] == 0 and spans[-1][1] == len(book) and all(a2 == b1 for (_, b1), (a2, _) in zip(spans, spans[1:]))
    assert all(b - a <= cv.FACT_BLOCK_CHARS * 1.3 for a, b in spans)


def test_node_matcher_picks_the_node_whose_name_summary_and_cards_fit_the_fact():
    book = tpq._book()
    index = cv.SourceIndex(book)
    nodes = pb.normalize_map(tpq._map())["nodes"]
    cards = {k: [{"text": c["t"], "translation": c["d"]} for c in v] for k, v in tpq.GOOD.items()}
    m = cv.NodeMatcher(index, nodes, cards)
    assert m.best("Срок полномочий судьи Конституционного суда составляет шесть лет.") == "sub_a"
    assert m.best("Иск подаётся в районный суд по месту жительства ответчика.") == "sub_b"
    assert cv.NodeMatcher(index, [], {}).best("что угодно") is None


# --- деньги -----------------------------------------------------------------------------

def test_book_cost_forecast_for_the_measured_book_fits_the_ceiling_and_grows_with_cards():
    goal = cv.target_cards(1_135_000, 2300)
    price = Budget.estimate_book_cost(1_135_000, goal)
    limit = Budget([], offpeak=True).limit
    assert 0.10 <= price <= limit * 1.25                                                # ~450 страниц с 2 мыслями на карточку: чуть выше 18¢, цену вперёд урежет мысли
    assert Budget.estimate_book_cost(1_135_000, goal, facts=False) <= limit * 1.05      # одни карточки по странице — в потолок
    assert Budget.estimate_book_cost(1_135_000, 2 * goal) > price                       # 1000 карточек стоили бы заметно дороже
    assert Budget.estimate_book_cost(1_135_000, goal, facts=False) < price              # факты конспекта тоже чего-то стоят
    assert price <= limit                                                                 # решение 2026-10-07: «глубокая» версия (≈ 21¢) укладывается в потолок по умолчанию
    assert price - Budget.estimate_book_cost(1_135_000, goal, facts=False) < 0.05       # но недорого: ~42 токена на факт, урок их не пересказывает
    assert Budget.estimate_book_cost(1_135_000, 400, facts=1.8) <= limit * 1.05          # до потолка цена вперёд сжимает и карточки, и факты (≈ 400 и 1,8 на карточку)
    assert Budget.estimate_book_cost(300_000, cv.target_cards(300_000, 2300)) < price


def test_facts_cost_money_and_shrink_the_card_cap():
    b = Budget([{"label": "map#1", "prompt_tokens": 337_000, "cost_usd": 0.066}], offpeak=True)
    assert b.card_cap(1_135_000, 90, 10, facts=True) < b.card_cap(1_135_000, 90, 10, facts=False)
    assert b.card_cap(1_135_000, 90, 10, facts=1.0) > b.card_cap(1_135_000, 90, 10, facts=True)
    assert 0 < b.facts_cost(90) < b.cost(out=185 * 90) / 4                               # факт в разы дешевле карточки


def test_facts_per_card_follow_the_source_type():
    from app.services.ai_gateway import source_profile as sp
    assert sp.facts_per_card("textbook") == 3.2 and sp.facts_per_card("notes") < sp.facts_per_card("article") < sp.facts_per_card("textbook")
    assert sp.facts_per_card(None) == sp.facts_per_card("textbook")


# --- конвейер ---------------------------------------------------------------------------

def _fake(g_by_node=None, **kw):
    def cards(prompt, fake):
        return {"nodes": [{"key": k, "cards": tpq.GOOD.get(k) or [], **({"g": g_by_node[k]} if g_by_node and k in g_by_node else {})}
                          for k in keys_of(prompt)]}
    return FakeLLM(raw_map=tpq._map(), cards=cards, **kw)


def test_fill_only_tops_up_to_the_target_and_is_skipped_when_it_is_reached():
    fake = _fake()
    with patched(fake):
        res = asyncio.run(pb.build_learning_path(tpq._book(with_gap=True), "s", card_total=6))
    assert not fake.of("FILL")                                                          # цель (6) достигнута: сверх неё добор не нужен
    assert res["stats"]["fill"]["skipped"] == "цель достигнута" and res["stats"]["quota"]["target"] == 6

    fake = _fake()
    with patched(fake):
        res = asyncio.run(pb.build_learning_path(tpq._book(with_gap=True), "s", card_total=14))
    assert fake.of("FILL")                                                              # до цели не хватает 8: добор разрешён
    d = res["stats"]["density"]
    assert d["chars_per_card"] > 0 and d["cards_per_node"] > 0


def test_fill_never_adds_more_than_the_missing_cards():
    from test_pipeline_quality import BUDGET_FACT

    def fill(prompt, fake):
        card = {"t": "Какой орган ежегодно утверждает бюджет республики?", "s": "Право | Бюджет", "d": "Совет Республики.",
                "e": "", "l": "medium", "y": 1, "at": "organ", "ev": BUDGET_FACT, "x": ["Правительство.", "Президент.", "Суд."]}
        return {"fill": [{"id": f"P{i}", "node": "sub_a", "cards": [dict(card, t=card["t"] + " " * i + str(i))] * 3} for i in range(1, 12)]}

    fake = FakeLLM(raw_map=tpq._map(), cards=_fake().handlers["CARDS"], fill=fill)
    with patched(fake):
        res = asyncio.run(pb.build_learning_path(tpq._book(with_gap=True), "s", card_total=8))
    total = sum(len(p["cards"]) for p in res["packs"].values())
    assert res["stats"]["fill"].get("added", 0) <= 2 and total <= 8                      # было 6, цель 8: добавлять можно не больше двух


PLEDGE = "Договор залога заключается в письменной форме"
LEASE = "Договор аренды здания подлежит государственной регистрации"
FACT_BOOK = tpq._book() + tpq._chunk("залога", PLEDGE) + tpq._chunk("аренды", LEASE)


def _facts_handler(plan):
    """plan: [(фраза из книги, факт)] — факт приходит в том блоке, где стоит фраза."""
    def handler(prompt, fake):
        book = prompt.split("[SOURCE MATERIAL — FULL TEXT]\n", 1)[1].split("\n[END OF SOURCE MATERIAL]", 1)[0]
        spans = cv.fact_blocks(book)
        blocks = []
        for m in re.finditer(r"^B(\d+) \| K=", prompt, re.M):
            i = int(m.group(1)) - 1
            a, b = spans[i]
            blocks.append({"id": f"B{i + 1}", "facts": [fact for anchor, fact in plan if a <= book.find(anchor) < b]})
        return {"blocks": blocks}
    return handler


def _run_facts(plan, **kw):
    fake = FakeLLM(raw_map=tpq._map(), cards=_fake().handlers["CARDS"], facts=_facts_handler(plan), **kw)
    with patched(fake):
        return fake, asyncio.run(pb.build_learning_path(FACT_BOOK, "s"))


def test_facts_form_the_fact_sheet_by_blocks_and_the_lesson_does_not_retell_them():
    plan = [(PLEDGE, PLEDGE + "."),
            (PLEDGE, "Договор залога заключается в письменной форме в 2099 году."),                      # число, которого нет в книге
            (tpq.FACTS["term"][2], "Срок полномочий судьи Конституционного суда составляет шесть лет."),   # это уже спрашивает карточка
            (PLEDGE, "Президент Республики лично принимает присягу у каждого судьи страны."),             # такого в книге нет
            (PLEDGE, "Договор залога заключается в форме письменной.")]                                  # почти повтор первого факта
    fake, res = _run_facts(plan)
    assert fake.of("FACTS") and "FACTS" in [k for k, _ in fake.log]
    assert list(res["facts"].values()) == [[PLEDGE + "."]] or [f for fs in res["facts"].values() for f in fs] == [PLEDGE + "."]
    st = res["stats"]["facts"]
    assert st["kept"] == 1 and st["repeat_card"] == 1 and st["unsupported"] == 1 and st["repeat_fact"] == 1 and st["received"] == 4
    assert all("K=" in p for p in fake.of("FACTS")) and "starts: «" in fake.of("FACTS")[0] and "ends: «" in fake.of("FACTS")[0]
    lesson_prompt = "\n".join(fake.of("LESSON"))
    assert "ALSO TEACH" not in lesson_prompt and PLEDGE not in lesson_prompt            # урок факты не получает
    assert all("FACT SHEET" not in p and '"g"' not in p for p in fake.of("CARDS"))     # карточки о фактах ничего не знают


def test_facts_are_listed_in_the_order_of_the_book_and_of_the_answer_not_of_the_blocks_returned():
    verifier = CardVerifier(FACT_BOOK)
    spans = cv.fact_blocks(FACT_BOOK)
    blk = lambda s: next(i for i, (a, b) in enumerate(spans) if a <= FACT_BOOK.find(s) < b)

    class OneNode:
        def best(self, fact, win=None):
            return "n"

    raw = [{"t": LEASE + ".", "blk": blk(LEASE), "seq": 0}, {"t": PLEDGE + ".", "blk": blk(PLEDGE), "seq": 0}]
    out, rep = process_facts(verifier, raw, spans, OneNode(), {}, ["n"])
    assert out == {"n": [PLEDGE + ".", LEASE + "."]} and rep["kept"] == 2           # порядок книги, а не порядок ответа


def test_fact_support_is_checked_in_the_block_and_unusable_books_are_not_checked():
    verifier = CardVerifier(FACT_BOOK)
    spans = cv.fact_blocks(FACT_BOOK)
    a, b = next((a, b) for a, b in spans if a <= FACT_BOOK.find(PLEDGE) < b)
    assert verifier.fact_supported_in(PLEDGE + ".", a, b)
    assert not verifier.fact_supported_in("Квантовая запутанность лишена локальных скрытых параметров.", a, b)
    assert not verifier.fact_supported_in("Договор залога заключается на 9 лет.", a, b)            # числа 9 в блоке нет
    assert verifier.check_fact(PLEDGE) is not None
    short = CardVerifier("Договор залога заключается в письменной форме. Срок договора пять лет.")        # окон мало: проверка по тексту целиком
    assert short.check_fact("Договор залога заключается письменно.") == 0 and short.check_fact("Квантовая запутанность лишена параметров.") is None
    assert short.check_fact("Срок договора девять лет, указано в 12 пунктах.") is None


def test_facts_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(pb, "FACTS", False)
    fake, res = _run_facts([(PLEDGE, PLEDGE + ".")])
    assert not fake.of("FACTS") and res["facts"] == {} and res["stats"]["facts"]["asked"] == 0


def test_facts_target_follows_the_card_target_and_the_source_type():
    fake = _fake()
    with patched(fake):
        res = asyncio.run(pb.build_learning_path(FACT_BOOK, "s", card_total=12))
    st = res["stats"]["facts"]
    from app.services.ai_gateway import source_profile as sp_
    assert st["per_card"] == sp_.facts_per_card(res["source_type"]) and st["blocks"] >= 3
    assert st["asked"] >= round(st["per_card"] * 12) - 1                               # ровно столько, сколько ждали от цели
    prompts = fake.of("FACTS")
    assert prompts and all(p.count("\nB") + p.count("BLOCKS (reading order):\nB") >= 1 for p in prompts)


def test_blocks_missing_from_the_answer_are_asked_again():
    calls = {"n": 0}

    def facts(prompt, fake):
        calls["n"] += 1
        ids = re.findall(r"^(B\d+) \|", prompt, re.M)
        return {"blocks": [{"id": i, "facts": []} for i in ids[: max(1, len(ids) // 2)]]} if calls["n"] == 1 else {"blocks": [{"id": i, "facts": []} for i in ids]}

    fake = FakeLLM(raw_map=tpq._map(), cards=_fake().handlers["CARDS"], facts=facts)
    with patched(fake):
        asyncio.run(pb.build_learning_path(FACT_BOOK, "s"))
    assert len(fake.of("FACTS")) >= 2                                                  # недостающие блоки запрошены повторно


def test_second_map_is_built_only_when_the_first_is_weak(monkeypatch):
    monkeypatch.setattr(pb, "MAP_GOOD_SCORE", 0.0)
    fake = _fake()
    with patched(fake):
        asyncio.run(pb.build_learning_path(tpq._book(), "s"))
    assert len(fake.of("MAP")) == 1                                                      # первая уже «достаточна» — вторую не строим
    monkeypatch.setattr(pb, "MAP_GOOD_SCORE", 99.0)
    fake = _fake()
    with patched(fake):
        asyncio.run(pb.build_learning_path(tpq._book(), "s"))
    assert len(fake.of("MAP")) == 2


def test_spread_pick_covers_the_whole_book_not_only_its_start():
    spans = list(range(100))
    picked = pb.spread_pick(spans, 5)
    assert picked[0] == 0 and picked[-1] == 99 and picked == sorted(picked) and len(set(picked)) == 5
    assert max(b - a for a, b in zip(picked, picked[1:])) <= 26
    assert pb.spread_pick(spans, 200) == spans and pb.spread_pick(spans, 0) == [] and pb.spread_pick(spans, 1) == [50]


def test_prompt_lines_for_targets_and_facts():
    task = pp.build_cards_task("{}", ["a", "b"], {"a": 4, "b": 2})
    assert "TARGET CARDS: a=4, b=2\n" in task and "FACT SHEET" not in task and "FACTS" not in task
    facts_task = pp.build_facts_task(["B1 | K=5 | starts: «а б в» | ends: «г д»", "B2 | K=3 | starts: «е» | ends: «ж»"])
    assert "TYPE: FACTS" in facts_task and "B2 | K=3" in facts_task
    sp = pp.PATH_BUILDER_SYSTEM_PROMPT
    assert 'PART J. TASK "FACTS"' in sp and "IN THE ORDER OF THE BLOCK" in sp and "B2c" not in sp
    assert "ALSO TEACH" not in sp                                                           # урок факты не пересказывает
