"""Конвейер «карта → карточки → проверка по книге → добор → урок из карточек»: порядок, проверка, бюджет (подставной DeepSeek)."""
import asyncio
import re

from app.services.ai_gateway import budget as budget_mod
from app.services.ai_gateway import path_builder as pb
from app.services.ai_gateway import path_prompts as pp
from llm_fake import FakeLLM, default_cards, default_lesson, keys_of, node_block, patched, ANSWER_RE

FILL_TEXT = "Институт подробно рассматривается в учебнике, с примерами из практики и ссылками на нормативные акты. "

FACTS = {
    "term": ("Какой срок полномочий установлен для судьи Конституционного суда?", "Шесть лет.",
             "Срок полномочий судьи Конституционного суда составляет шесть лет"),
    "venue": ("Куда подаётся иск по месту жительства ответчика?", "В районный суд.",
              "Иск подаётся в районный суд по месту жительства ответчика"),
    "prosecutor": ("Кто назначает Генерального прокурора республики?", "Президент.",
                   "Генеральный прокурор назначается Президентом с согласия Совета Республики"),
    "notary": ("Что выдаёт нотариус наследникам умершего?", "Свидетельство о праве на наследство.",
               "Нотариус выдаёт свидетельство о праве на наследство"),
    "age": ("Каков минимальный возраст кандидата в судьи?", "Тридцать лет.",
            "Кандидату в судьи должно быть не менее тридцати лет"),
    "jury": ("Сколько присяжных заседателей участвует в процессе?", "Двенадцать.",
             "В процессе участвуют двенадцать присяжных заседателей"),
}
BUDGET_FACT = "Совет Республики ежегодно утверждает бюджет республики на очередной финансовый год"


def _chunk(subject: str, fact: str, n: int = 14) -> str:
    return (f"Об институте {subject}. " + FILL_TEXT * n) + fact + ". " + (FILL_TEXT * n) + "\n"


def _book(with_gap: bool = False) -> str:
    body = "".join(_chunk(s, FACTS[k][2]) for s, k in (("суда", "term"), ("подсудности", "venue"), ("прокуратуры", "prosecutor"), ("нотариата", "notary"),
                                                               ("кандидатов", "age"), ("присяжных", "jury")))
    if with_gap:
        body += _chunk("бюджета", BUDGET_FACT, n=100)            # длинный участок, на который карточек не будет
    return body


def _card(fact: str, ev: str | None = None, answer: str | None = None) -> dict:
    q, a, e = FACTS[fact]
    return {"t": q, "s": "Право | Тема", "d": answer or a, "e": "", "l": "medium", "y": 1, "at": "term",
            "ev": e if ev is None else ev, "x": ["Три года.", "Пять лет.", "Десять лет."]}


def _map(extra_nodes=()):
    nodes = [
        {"key": "base", "name": "Основа", "tier": 0, "order": 1, "summary": "Базовое понятие", "src": "Гл. 1"},
        {"key": "topic", "name": "Тема", "tier": 1, "order": 2, "summary": "Тема", "src": "Гл. 2"},
        {"key": "sub_a", "name": "Судья", "tier": 2, "parent": "topic", "order": 3, "summary": "Срок полномочий судьи", "src": "Гл. 2"},
        {"key": "sub_b", "name": "Иск", "tier": 2, "parent": "topic", "order": 4, "summary": "Куда подаётся иск", "src": "Гл. 2"},
        {"key": "sub_c", "name": "Кандидат", "tier": 2, "parent": "topic", "order": 5, "summary": "Возраст кандидата в судьи", "src": "Гл. 2"},
        {"key": "sub_d", "name": "Присяжные", "tier": 2, "parent": "topic", "order": 6, "summary": "Число присяжных", "src": "Гл. 2"},
    ] + list(extra_nodes)
    return {"title": "T", "domain": "law", "nodes": nodes,
            "edges": [{"from": "sub_a", "to": "sub_b", "relation": "demarcated_from", "label": "разграничивается с"}]}


def _cards_handler(per_node: dict):
    def handler(prompt, fake):
        return {"nodes": [{"key": k, "cards": per_node.get(k) or default_cards(k)} for k in keys_of(prompt)]}
    return handler


GOOD = {
    "base": [_card("notary")],
    "topic": [_card("prosecutor")],
    "sub_a": [_card("term")],
    "sub_b": [_card("venue")],
    "sub_c": [_card("age")],
    "sub_d": [_card("jury")],
}


def _run(fake, text=None, subject="s"):
    with patched(fake):
        return asyncio.run(pb.build_learning_path(text or _book(), subject))


# --- порядок этапов и состав запросов ---------------------------------------------

def test_stages_run_in_order_and_confirmed_cards_skip_the_audit():
    fake = FakeLLM(raw_map=_map(), cards=_cards_handler(GOOD))
    res = _run(fake)
    kinds = [k for k, _ in fake.log]
    assert kinds.index("MAP") < kinds.index("CARDS") < kinds.index("LESSON")
    assert "AUDIT" not in kinds                                   # все цитаты найдены в книге: проверять нечего
    assert res["stats"]["support"]["grounded"] == 6
    assert set(res["packs"]) == {"base", "topic", "sub_a", "sub_b", "sub_c", "sub_d"} and all(p["lesson"] for p in res["packs"].values())
    assert res["stats"]["audit"] == {"checked": 0}


def test_lesson_request_has_cards_excerpt_and_no_book():
    fake = FakeLLM(raw_map=_map(), cards=_cards_handler(GOOD))
    book = _book()
    _run(fake, book)
    prompt = "\n".join(fake.of("LESSON"))
    block = node_block(prompt, "sub_a")
    assert "1. Q: Какой срок полномочий установлен для судьи Конституционного суда? | A: Шесть лет." in block
    assert "PREREQS: Тема" in block
    assert "RELATED: Судья разграничивается с Иск" in block         # связь из карты, а не из книги
    assert "KEYS: sub_a=Судья" in block and "topic=Тема" in block
    assert "EXCERPT: «" in block and "составляет шесть лет" in block   # выдержка вокруг цитаты карточки
    assert len(prompt) < len(book) / 4 and book[:2000] not in prompt  # в запросе урока нет книги целиком


# --- проверка по книге --------------------------------------------------------------

def test_suspicious_cards_are_audited_with_a_passage_and_verdicts_are_applied():
    wrong_number = _card("term", answer="Пять лет.")                      # цитата настоящая, ответ расходится с ней
    wrong_number["t"] = "Сколько лет длится срок полномочий судьи Конституционного суда?"
    invented = {"t": "Какой орган утверждает бюджет санатория?", "s": "Право | Тема", "d": "Попечительский совет.",
                "e": "", "l": "easy", "y": 1, "at": "organ", "ev": "Попечительский совет утверждает бюджет санатория",
                "x": ["Совет директоров.", "Наблюдательный совет.", "Правление."]}
    per_node = dict(GOOD, sub_a=[_card("term"), wrong_number, invented])

    def audit(prompt, fake):
        items = prompt.split("\n\n")
        out = []
        for chunk in items:
            m = re.search(r"^(C\d+) \| node", chunk, re.M)
            if not m:
                continue
            if "Попечительский" in chunk:
                out.append({"id": m.group(1), "v": "drop"})
            elif "A: Пять лет." in chunk:
                assert "составляет шесть лет" in chunk                       # проверяющий видит кусок книги
                out.append({"id": m.group(1), "v": "fix", "d": "Шесть лет.", "x": ["Три года.", "Пять лет.", "Семь лет."],
                            "ev": "Срок полномочий судьи Конституционного суда составляет шесть лет"})
        return {"audit": out}

    fake = FakeLLM(raw_map=_map(), cards=_cards_handler(per_node), audit=audit)
    res = _run(fake)
    assert len(fake.of("AUDIT")) == 1
    cards = res["packs"]["sub_a"]["cards"]
    assert [c["translation"] for c in cards] == ["Шесть лет.", "Шесть лет."]      # выдуманная удалена, неверная исправлена
    assert {c["support"] for c in cards} == {"grounded", "fixed"}
    assert res["stats"]["audit"]["dropped"] == 1 and res["stats"]["audit"]["fixed"] == 1
    # Урок строится уже из окончательных карточек
    block = node_block("\n".join(fake.of("LESSON")), "sub_a")
    assert "Попечительский" not in block and "A: Пять лет." not in block


def test_audit_failure_keeps_the_cards_and_the_lessons_are_still_written():
    wrong = _card("term", answer="Пять лет.")
    fake = FakeLLM(raw_map=_map(), cards=_cards_handler(dict(GOOD, sub_a=[wrong])),
                   audit=lambda p, f: (_ for _ in ()).throw(RuntimeError("API")))
    with patched_hang_free():
        res = _run(fake)
    assert [c["translation"] for c in res["packs"]["sub_a"]["cards"]] == ["Пять лет."]
    assert res["packs"]["sub_a"]["lesson"]


def patched_hang_free():
    from unittest.mock import patch
    return patch.object(pb, "HANG_RETRIES", 0)


# --- добор участков книги без карточек -----------------------------------------------

def test_stretch_without_cards_is_filled_and_unverified_cards_are_rejected():
    book = _book(with_gap=True)

    def fill(prompt, fake):
        assert len(prompt) < len(book) and book[:3000] not in prompt                         # в запросе участки, а не вся книга
        good = {"t": "Какой орган ежегодно утверждает бюджет республики?", "s": "Право | Бюджет", "d": "Совет Республики.",
                "e": "", "l": "medium", "y": 1, "at": "organ", "ev": BUDGET_FACT, "x": ["Правительство.", "Президент.", "Суд."]}
        fake_one = {"t": "Какой орган утверждает бюджет санатория?", "s": "Право | Бюджет", "d": "Попечительский совет.",
                    "e": "", "l": "medium", "y": 1, "at": "organ", "ev": "Попечительский совет утверждает бюджет санатория",
                    "x": ["Совет директоров.", "Наблюдательный совет.", "Правление."]}
        out = []
        for block in re.split(r"(?m)^(?=P\d+ \| suggested node)", prompt):
            m = re.match(r"(P\d+) \| suggested node: (\S+)", block)
            if m:                                                                              # карточки даём только там, где стоит факт
                out.append({"id": m.group(1), "node": m.group(2), "cards": [good, fake_one] if BUDGET_FACT in block else []})
        return {"fill": out}

    fake = FakeLLM(raw_map=_map(), cards=_cards_handler(GOOD), fill=fill)
    res = _run(fake, book)
    assert res["stats"]["fill"]["added"] == 1 and res["stats"]["fill"]["rejected"] == 1
    added = [c for p in res["packs"].values() for c in p["cards"] if "бюджет республики" in c["text"]]
    assert len(added) == 1 and added[0]["support"] == "grounded"
    assert any("Совет Республики." in b for b in fake.of("LESSON"))                  # новая карточка попала в урок
    kinds = [k for k, _ in fake.log]
    assert kinds.index("FILL") < kinds.index("LESSON")                                # добор идёт до уроков


def test_no_fill_when_every_paragraph_is_touched_by_a_card():
    book = "\n".join(FACTS[k][2] + "." for k in ("term", "venue", "prosecutor", "notary", "age", "jury")) * 1
    fake = FakeLLM(raw_map=_map(), cards=_cards_handler(GOOD))
    res = _run(fake, book)
    assert not fake.of("FILL") and res["stats"]["fill"] == {"spans": 0, "added": 0}


def test_untouched_paragraphs_inside_a_covered_section_are_found_by_their_exact_place():
    from app.services.ai_gateway import card_quality as cq
    book = _book()
    v = cq.CardVerifier(book)
    card = {"text": FACTS["term"][0], "translation": FACTS["term"][1], "evidence": FACTS["term"][2]}
    v.verify([card])
    passages = v.uncovered_passages([card])
    assert passages and all(p["chars"] >= cq.MIN_HOLE_CHARS and p["chars"] <= cq.HOLE_PASSAGE_CHARS + 50 for p in passages)
    fact = book.index(FACTS["term"][2])
    assert not any(p["start"] <= fact < p["end"] for p in passages)                     # место карточки — не дыра
    assert any(p["start"] > fact for p in passages) and sum(p["chars"] for p in passages) > len(book) * 0.5
    # Сноски в счёт не идут
    foot = "Основной текст. " * 120 + "\n1 Кашанина, Т.В. Происхождение государства и права. М., 1999. С. 317.\n" * 12 + "Основной текст. " * 5
    assert all("Кашанина" not in foot[p["start"]:p["end"]] for p in cq.CardVerifier(foot + foot).uncovered_passages([]))


# --- бюджет -----------------------------------------------------------------------------

def test_tiny_budget_skips_optional_stages_but_lessons_are_still_written(monkeypatch):
    monkeypatch.setattr(budget_mod, "BUDGET_USD", 0.0005)
    wrong = _card("term", answer="Пять лет.")
    fake = FakeLLM(raw_map=_map(), cards=_cards_handler(dict(GOOD, sub_a=[wrong])))
    res = _run(fake, _book(with_gap=True))
    assert not fake.of("AUDIT") and not fake.of("FILL")
    assert res["stats"]["budget"]["skipped"]                                         # в отчёте видно, что урезано
    assert all(p["lesson"] for p in res["packs"].values())                           # уроки обязательны


def test_card_quotas_follow_the_budget_when_caps_are_on(monkeypatch):
    monkeypatch.setattr(pb, "CARD_CAPS", True)
    path_map = pb.normalize_map(_map())
    hints = {"sub_a": 40_000, "sub_b": 30_000}                                       # крупные куски книги
    free = pb.plan_card_quotas(_book(), path_map, hints)
    capped = pb.plan_card_quotas(_book(), path_map, hints, total_cap=20)
    broke = pb.plan_card_quotas(_book(), path_map, hints, total_cap=0)
    assert free["sub_a"] > 3 and sum(capped.values()) < sum(free.values())
    assert set(broke.values()) == {3}                                                # бюджета нет: минимум на узел, а не «без лимита»

    rich = FakeLLM(raw_map=_map(), cards=_cards_handler(GOOD))
    res_rich = _run(rich, _book())
    monkeypatch.setattr(budget_mod, "BUDGET_USD", 0.0005)
    res_poor = _run(FakeLLM(raw_map=_map(), cards=_cards_handler(GOOD)), _book())
    assert sum(res_poor["quotas"].values()) <= sum(res_rich["quotas"].values())


def test_without_caps_quotas_follow_the_book_only():
    path_map = pb.normalize_map(_map())
    hints = {"sub_a": 90_000, "sub_b": 30_000}
    assert not pb.CARD_CAPS                                                          # по умолчанию потолки выключены
    free = pb.plan_card_quotas(_book(), path_map, hints)                             # константных потолков нет, денег хватает
    assert free["sub_a"] > 12 and free["sub_a"] > free["sub_b"] > 3                  # 90 тыс. знаков -> 39 карточек, без потолка на узел
    broke = pb.plan_card_quotas(_book(), path_map, hints, total_cap=0)              # деньги ограничивают ВСЕГДА, даже без потолков
    assert set(broke.values()) == {3}


def test_without_caps_audit_and_fill_are_not_limited_by_count(monkeypatch):
    monkeypatch.setattr(pb, "AUDIT_MAX_CARDS", 1)
    monkeypatch.setattr(pb, "MAX_FILL_SPANS", 1)
    wrong = []
    for q in ("Сколько лет длится срок полномочий судьи Конституционного суда?",          # три расходящихся карточки -> все три на проверку
              "На какой период назначается судья Конституционного суда?",
              "Чему равна продолжительность работы члена Конституционного суда в должности?"):
        c = _card("term", answer="Пять лет.")
        c["t"] = q
        wrong.append(c)
    seen = []

    def audit(prompt, fake):
        seen.append(prompt.count("PASSAGE: «"))
        return {"audit": []}

    fake = FakeLLM(raw_map=_map(), cards=_cards_handler(dict(GOOD, sub_a=wrong)), audit=audit)
    _run(fake)
    assert sum(seen) == 3


# --- урок: правка пропущенных ответов -------------------------------------------------

def test_lesson_that_skips_answers_is_repaired_once_and_the_better_one_is_kept():
    def lessons(prompt, fake):
        repair = "THE PREVIOUS LESSON OMITTED" in prompt
        out = []
        for k in keys_of(prompt):
            answers = ANSWER_RE.findall(node_block(prompt, k)) if repair else []     # первый ответ «забывает» все ответы
            out.append({"key": k, "lesson": default_lesson(k, answers)})
        return {"nodes": out}

    per_node = {k: [_card(f) for f in fs] for k, fs in {"base": ["notary", "term"], "topic": ["prosecutor", "venue"]}.items()}
    fake = FakeLLM(raw_map=_map(), cards=_cards_handler(per_node), lessons=lessons)
    res = _run(fake)
    first = [p for p in fake.of("LESSON") if "THE PREVIOUS LESSON OMITTED" not in p]
    repairs = [p for p in fake.of("LESSON") if "THE PREVIOUS LESSON OMITTED" in p]
    assert first and repairs
    assert res["stats"]["lesson_repair"]["improved"] >= 1
    text = " ".join(s["say"] for s in res["packs"]["base"]["lesson"]["screens"])
    assert "Шесть лет" in text or "Свидетельство о праве на наследство" in text      # ответы теперь названы в уроке
    assert res["stats"]["lesson_alignment"]["full_share"] > 0.5


def test_lessons_needing_repair_uses_thresholds():
    cards = {"n": [{"text": f"В{i}?", "translation": a} for i, a in enumerate(["Шесть лет.", "Президент республики.", "Районный суд."])]}
    full = {"n": default_lesson("n", ["Шесть лет.", "Президент республики.", "Районный суд."])}
    assert pb.lessons_needing_repair(cards, full) == {}
    one_missing = {"n": default_lesson("n", ["Шесть лет.", "Президент республики."])}
    assert pb.lessons_needing_repair(cards, one_missing) == {"n": ["Районный суд."]}        # 1 из 3 = 33% >= 30%
    many = {"n": {"screens": [{"say": "а"}, {"say": "б"}, {"say": "в"}], "check": []}}
    assert len(pb.lessons_needing_repair(cards, many)["n"]) == 3


# --- карта и промпты -----------------------------------------------------------------

def test_map_task_states_size_and_subtopic_target_without_touching_the_static_prompt():
    task = pp.build_map_task("s", 1_100_000)
    assert "SOURCE SIZE: about 1100 thousand characters." in task and "about 80 tier-2 subtopics" in task and "one per roughly 13 thousand" in task
    assert pp.subtopic_target(1_100_000) == 80 and pp.subtopic_target(60_000) == 15 and pp.subtopic_target(9_000_000) == 120
    assert pp.subtopic_target(1_100_000, chars_per_card=1000) == 120 and pp.subtopic_target(300_000, chars_per_card=1000) == 50   # плотнее карточки — мельче подтемы
    assert "SOURCE SIZE" not in pp.build_map_task("s")                                # без размера — как раньше
    assert "{" not in pp.PATH_BUILDER_SYSTEM_PROMPT.split("PART A")[0]


def test_map_score_prefers_the_subtopic_count_that_fits_the_size_of_the_book():
    def raw(n_sub):
        nodes = [{"key": f"b{i}", "name": f"Основа {i}", "tier": 0, "order": i, "summary": "s", "src": "1"} for i in range(1, 7)]
        nodes += [{"key": f"t{i}", "name": f"Тема {i}", "tier": 1, "order": 10 + i, "summary": "s", "src": "2"} for i in range(1, 8)]
        nodes += [{"key": f"s{i}", "name": f"Подтема {i}", "tier": 2, "parent": "t1", "order": 30 + i, "summary": "s", "src": "3"} for i in range(1, n_sub + 1)]
        return pb.normalize_map({"title": "T", "domain": "law", "nodes": nodes,
                                 "edges": [{"from": f"t{i}", "to": f"b{i}", "relation": "depends_on"} for i in range(1, 7)] * 3})
    few, many = raw(20), raw(50)
    assert pb.score_map(many, 1_100_000) > pb.score_map(few, 1_100_000)             # большая книга: 20 подтем мало
    assert pb.score_map(few, 150_000) > pb.score_map(many, 150_000)                 # небольшой текст: 50 подтем много


def test_cards_prompt_demands_quotes_completeness_and_teaching_order_and_lesson_prompt_forbids_new_facts():
    sp = pp.PATH_BUILDER_SYSTEM_PROMPT
    for marker in ("B1. COMPLETENESS", "TEACHING ORDER", "B2a. EVIDENCE", "copy 5-10 consecutive words", "letter for letter",
                   "what your cards do not ask is never taught", 'TASK "AUDIT"', 'TASK "FILL"', 'TASK "LESSON"',
                   "H1. WHERE FACTS COME FROM", "Do not add rules, numbers, dates, names or conditions from your own knowledge",
                   "THE PREVIOUS LESSON OMITTED THESE ANSWERS", "never use your own knowledge of the subject where it differs from the source"):
        assert marker in sp, marker
    assert "TYPE: CARDS" in pp.build_cards_task("{}", ["a"]) and "TYPE: LESSON" in pp.build_lessons_task("x")


def test_run_report_names_fat_nodes_and_nodes_with_too_few_cards():
    m = pb.normalize_map(_map())
    quotas = {"base": 3, "topic": 3, "sub_a": 10, "sub_b": 8, "sub_c": 3, "sub_d": 3}
    sizes = {"base": 5000, "topic": 9000, "sub_a": 40_000, "sub_b": 20_000, "sub_c": 4000, "sub_d": 4000}
    got = {"sub_a": [{"text": "a"}] * 3, "sub_b": [{"text": "b"}] * 8, "base": [{"text": "c"}] * 3}
    rep = pb.quota_report(m, quotas, sizes, got)
    assert rep["fat_nodes"] == ["Судья (40 тыс. знаков)"]                 # кусок книги больше, чем покрывает потолок в 10 карточек
    assert rep["short_nodes"] == ["Судья"]                                # дали 3 из 10
    assert rep["asked"] == 30 and rep["got"] == 14
    assert pb.quota_report(m, None, None, got) == {}


def test_cards_prompt_asks_for_inventory_a_floor_not_a_ceiling_understanding_and_no_references_to_the_text():
    sp = pp.PATH_BUILDER_SYSTEM_PROMPT
    for marker in ("take inventory as you go", "EVERY member of an enumeration", "The TARGET is a floor, not a ceiling",
                   "AT LEAST N cards", "UNDERSTANDING SHARE", "at least one card in four", "по учению естественного права"):
        assert marker in sp, marker
    assert "about N cards" not in sp and "(N-1 to N+1)" not in sp                 # старая верхняя граница «N±1» снята


def test_cards_about_methodology_history_and_famous_authors_are_not_thrown_away():
    """Прежний чёрный список выбрасывал «методологию», даты 1917–1939 и Монтескьё/Локка во всех предметах."""
    m = pb.normalize_map(_map())
    rej = []
    raw = {"nodes": [{"key": "sub_a", "cards": [
        {"t": "Как называется наука о самоорганизации, совместном действии взаимосвязанных подсистем?", "d": "Синергетика.", "x": ["Герменевтика.", "Диалектика.", "Метафизика."]},
        {"t": "Что такое методология юридической науки?", "d": "Совокупность принципов, методов и способов исследования.", "x": []},
        {"t": "В каком году был принят Декрет о суде?", "d": "В 1917 году.", "x": []},
        {"t": "Кто автор теории разделения властей?", "d": "Шарль Монтескьё.", "x": []},
    ]}]}
    cards = pb.normalize_cards(raw, m, ["sub_a"], rejected=rej)["sub_a"]
    assert len(cards) == 4 and rej == []


def test_references_to_the_text_are_cut_out_of_the_question_and_hopeless_ones_are_rejected():
    assert pb.strip_text_reference("Что, согласно тексту, предписывает человеку закон природы?") == ("Что предписывает человеку закон природы?", False)
    assert pb.strip_text_reference("Что, по определению из главы, отвечает на вопрос «как»?") == ("Что отвечает на вопрос «как»?", False)
    assert pb.strip_text_reference("Какой недостаток теории отмечает источник?")[1] is True       # вырезать нечего — ссылка остаётся
    assert pb.strip_text_reference("Что, по мнению Г. Кельзена, представляет собой естественное право?") == (
        "Что, по мнению Г. Кельзена, представляет собой естественное право?", False)                # ссылка на автора из книги — это не ссылка на текст
    m = pb.normalize_map(_map())
    rej = []
    raw = {"nodes": [{"key": "sub_a", "cards": [
        {"t": "Что, согласно тексту, предписывает человеку закон природы, Бога, разума?", "d": "Мир и безопасность.", "x": []},
        {"t": "Какой недостаток регулятивной теории отмечает источник?", "d": "Максимализм.", "x": []},
    ]}]}
    cards = pb.normalize_cards(raw, m, ["sub_a"], rejected=rej)["sub_a"]
    assert [c["text"] for c in cards] == ["Что предписывает человеку закон природы, Бога, разума?"] and cards[0]["text_reference_removed"]
    assert rej == [("ссылка на текст в вопросе", "Какой недостаток регулятивной теории отмечает источник?")]


def test_run_report_counts_rejected_cards_and_removed_references():
    bad = {"t": "Какой недостаток теории отмечает источник?", "s": "Право | Тема", "d": "Максимализм.", "e": "", "l": "easy", "y": 1,
           "at": "term", "ev": "", "x": ["Минимализм.", "Утопизм.", "Фатализм."]}
    fake = FakeLLM(raw_map=_map(), cards=_cards_handler(dict(GOOD, sub_a=[_card("term"), bad])))
    res = _run(fake)
    assert res["stats"]["rejected"]["by_reason"] == {"ссылка на текст в вопросе": 1}
    assert len(res["packs"]["sub_a"]["cards"]) == 1


def test_yo_and_ye_are_the_same_letter_when_matching_words():
    from app.services.ai_gateway import coverage
    assert coverage.stems("сравнивал с фонарём") == coverage.stems("сравнивал с фонарем")


def test_numeric_answers_are_not_tautologies_but_real_giveaways_still_are():
    from app.services.ai_gateway.blacklist import is_structurally_invalid_card as bad
    assert bad({"text": "Сколько функций имеет юридический метод?", "translation": "Две функции."}) == (False, "")
    assert bad({"text": "Сколько направлений выделяют в развитии теории естественного права?", "translation": "Три направления."}) == (False, "")
    assert bad({"text": "Какой принцип позволяет изучать явления с учётом их исторического развития?", "translation": "Принцип историзма."})[0]
    assert bad({"text": "Что такое судебная власть?", "translation": "Судебная власть."})[0]


def test_fill_request_lists_every_node_so_the_model_can_pick_the_right_one():
    seen = []

    def fill(prompt, fake):
        seen.append(prompt)
        return {"fill": []}

    fake = FakeLLM(raw_map=_map(), cards=_cards_handler(GOOD), fill=fill)
    _run(fake, _book(with_gap=True))
    prompt = seen[0]
    for key in ("base", "topic", "sub_a", "sub_b", "sub_c", "sub_d"):
        assert f"{key} | " in prompt                                                    # весь список, а не узел-«подсказка»
    assert "suggested node:" in prompt and "whose SUBJECT the card is about" in pp.PATH_BUILDER_SYSTEM_PROMPT
