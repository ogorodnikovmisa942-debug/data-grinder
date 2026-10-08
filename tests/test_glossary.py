"""Режим «словарь» (без ИИ) и тип материала «пособие»: разбор статей, пропуск того, что курс уже спрашивает, привязка к темам курса,
карточки «определение → термин», маршрутизация в конвейере, сохранение вместе с учебником."""
import asyncio
import random
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai_gateway import glossary as g
from app.services.ai_gateway import path_builder as pb
from app.services.ai_gateway import source_profile as sp
from llm_fake import FakeLLM, patched

ADJ = ["Судебный", "Гражданский", "Уголовный", "Налоговый", "Трудовой", "Земельный", "Семейный", "Таможенный", "Бюджетный", "Арбитражный"]
NOUN = ["процесс", "кодекс", "иск", "приговор", "договор", "спор", "штраф", "залог", "арест", "запрет", "надзор", "мандат"]
ADJ_LOW = [a.lower() for a in ADJ]


def _terms(n):
    return [f"{a} {b}" for a in ADJ for b in NOUN][:n]


def _definition(i):
    r = random.Random(i)
    return (f"порядок {r.choice(ADJ_LOW)}ого типа, при котором {r.choice(NOUN)} определяет {r.choice(ADJ_LOW)}ое значение "
            f"для {r.choice(NOUN)} и {r.choice(NOUN)} в системе государства номер {i}.")


def glossary_text(n=100, sep=" — "):
    return "\n".join(f"{t}{sep}{_definition(i)}" for i, t in enumerate(_terms(n)))


# --- разбор ------------------------------------------------------------------------------------------------------------------------

def test_entries_are_found_in_the_usual_dictionary_formats():
    text = """--- словарь.pdf: Стр. 1 ---
А
Апелляция (лат. appellatio) — обжалование судебного решения в вышестоящий суд, не вступившего
в законную силу, с целью его проверки.
Арбитраж – разновидность третейского разбирательства споров между хозяйствующими субъектами.
12
БЮДЖЕТ. Свод доходов и расходов государства на определённый период, утверждаемый законом.
Вина: психическое отношение лица к совершённому деянию и его последствиям для общества.

Закон - нормативный правовой акт высшей юридической силы, принятый в особом порядке.
1917 — Революция в России, изменившая государственный строй страны надолго и всерьёз.
"""
    got = {e["answer"]: e["definition"] for e in g.parse_entries(text)}
    assert set(got) == {"Апелляция", "Арбитраж", "Бюджет", "Вина", "Закон"}                  # дата и номер страницы статьями не стали
    assert got["Апелляция"].endswith("с целью его проверки.") and "не вступившего в законную силу" in got["Апелляция"]   # перенос строки склеен


def test_repeated_terms_keep_the_fuller_definition_and_non_terms_are_ignored():
    text = ("Иск — требование.\n\nИск — требование лица к суду защитить нарушенное или оспариваемое право и интерес.\n"
            "Например: это не статья словаря, а пояснение к тексту выше.\nМы рассмотрели вопрос — и пошли дальше обсуждать.\n")
    entries = g.parse_entries(text)
    assert [e["answer"] for e in entries] == ["Иск"] and "защитить нарушенное" in entries[0]["definition"]


def test_only_an_obvious_dictionary_is_detected_automatically():
    assert g.looks_like_glossary(glossary_text(100)) and g.looks_like_glossary(glossary_text(100, sep=" – "))
    prose = ("Суд обязан рассмотреть дело в разумный срок и вынести законное решение. " * 400)
    assert not g.looks_like_glossary(prose) and not g.looks_like_glossary(glossary_text(20))


def test_the_term_is_hidden_in_the_question():
    assert g.mask_term("Деятельность суда по осуществлению правосудия", "Правосудие") == "Деятельность суда по осуществлению …"
    assert "апелляци" not in g.mask_term("Обжалование решения в апелляционном порядке; апелляция подаётся в суд", "Апелляция (лат. appellatio)").lower()


# --- словарь без курса ------------------------------------------------------------------------------------------------------------

def test_a_dictionary_alone_becomes_alphabetical_groups_without_any_ai_call():
    with patch("app.services.ai_gateway.client.call_deepseek", new=AsyncMock(side_effect=AssertionError("ИИ вызван"))):
        res = g.build_glossary_path(glossary_text(100), "s")
    nodes = {n["key"]: n for n in res["map"]["nodes"]}
    cards = [c for p in res["packs"].values() for c in p["cards"]]
    assert res["source_type"] == "glossary" and res["cost_usd"] == 0.0 and res["calls"] == [] and res["merge"] == {}
    assert nodes["gl_root"]["tier"] == 0 and all(n["tier"] == 1 and n["prereqs"] == ["gl_root"] for k, n in nodes.items() if k != "gl_root")
    assert len(cards) == 100 and len(nodes) == 1 + -(-100 // g.GROUP_SIZE)
    assert all(p["lesson"] and len(p["lesson"]["screens"]) >= 3 for p in res["packs"].values())
    c = cards[0]
    assert c["text"].startswith("Какое понятие определяется так: «") and c["answer_type"] == "term" and c["layer"] == 0
    assert c["translation"].endswith(".") and c["distractors"] and len(c["distractors"]) >= 2
    assert c["translation"] not in c["distractors"] and c["translation"].rstrip(".").lower() not in c["text"].lower()
    assert len({x["translation"] for x in cards}) == 100                                     # термин одной статьи — один ответ


def test_a_small_dictionary_is_one_foundation_without_a_root():
    res = g.build_glossary_path(glossary_text(8), "s")
    assert [n["tier"] for n in res["map"]["nodes"]] == [0] and sum(len(p["cards"]) for p in res["packs"].values()) == 8


def test_not_a_dictionary_gives_a_clear_error():
    with pytest.raises(g.GlossaryError, match="не словарь"):
        g.build_glossary_path("Обычный текст учебника без статей. " * 50, "s")


# --- словарь к курсу ---------------------------------------------------------------------------------------------------------------

def _course():
    others = [{"key": f"n_{i}", "name": name, "tier": 2, "summary": summ, "prereqs": [], "parent": None}
              for i, (name, summ) in enumerate([("Конституционный суд", "конституционный контроль полномочия судьи республика"),
                                                ("Присяжные заседатели", "присяжные вердикт коллегия отбор состав"),
                                                ("Нотариат", "нотариус удостоверение завещание наследство свидетельство")])]
    return {"nodes": [{"key": "n_tax", "name": "Налоговый кодекс", "tier": 2, "summary": "налоговый кодекс штраф арест надзор", "prereqs": [], "parent": None}] + others,
            "cards": {"n_tax": [{"q": "Как называется требование лица к суду?", "a": "Судебный иск."},
                                {"q": "Что устанавливает налоговый кодекс?", "a": "Штраф и арест."}]}}


def test_terms_the_course_already_asks_are_skipped_and_matching_ones_join_the_topic():
    text = "\n".join([
        "Судебный иск — требование лица к суду защитить нарушенное или оспариваемое право.",             # термин курс уже спрашивает
        "Налоговый штраф — денежное взыскание по кодексу за нарушение правил, применяемое вместе с арестом имущества и надзором.",       # по теме узла
        "Налоговый запрет — ограничение по кодексу на отдельные виды деятельности, сопровождаемое надзором и арестом имущества.",
        "Налоговый залог — обеспечение уплаты по кодексу имуществом должника; при неуплате возможны арест и усиленный надзор.",
        "Земельный залог — обеспечение обязательства участком земли, переходящим к кредитору при неисполнении.",
        "Семейный договор — соглашение супругов об имущественных правах и обязанностях в браке и после него.",
        "Трудовой спор — разногласие работника и работодателя по условиям труда, рассматриваемое комиссией.",
    ])
    res = g.build_glossary_path(text, "s", _course())
    s = res["stats"]["source"]
    assert s["skipped_already_asked"] == 1 and s["entries"] == 7
    assert res["merge"] == {"gl__n_tax": "n_tax"} and len(res["packs"]["gl__n_tax"]["cards"]) == 3
    assert res["packs"]["gl__n_tax"]["lesson"]                                             # три и больше — получают разбор («дополнение» темы)
    asked = {c["translation"] for p in res["packs"].values() for c in p["cards"]}
    assert "Судебный иск." not in asked and "Земельный залог." in asked
    assert res["packs"]["gl__n_tax"]["cards"][0]["secondary_text"] == "Словарь | Налоговый кодекс"


def test_when_there_are_too_many_terms_those_met_in_the_course_come_first_then_the_rest_evenly():
    entries = glossary_text(60).split("\n")
    entries[41] = "Таможенный залог — предмет залога на таможне, используемый в налоговых платежах и штрафах государства."
    course = {"nodes": [], "cards": {"x": [{"q": "Что вносят в таможенный залог?", "a": "Деньги."}]}}
    res = g.build_glossary_path("\n".join(entries), "s", course, goal_override=10)
    got = [c["translation"] for p in res["packs"].values() for c in p["cards"]]
    assert len(got) == 10 and entries[41].split(" — ")[0] + "." in got                       # термин из карточек курса взят первым
    letters = sorted({a.split()[0] for a in got})
    assert len(letters) >= 5                                                               # остальные — не только начало алфавита


def test_the_deck_limit_of_the_course_applies_to_a_dictionary_too(monkeypatch):
    monkeypatch.setattr(sp, "COURSE_CARD_CAP", 50)
    res = g.build_glossary_path(glossary_text(100), "s", {"nodes": [], "cards": {"x": [{"q": f"Q{i}?", "a": f"A{i}."} for i in range(40)]}})
    assert res["stats"]["deck_policy"]["limited_by"] == "course"
    assert sum(len(p["cards"]) for p in res["packs"].values()) == sp.MIN_ADD_CARDS          # курс почти полон: лучшее из нового


# --- маршрутизация и сохранение --------------------------------------------------------------------------------------------------

def test_the_pipeline_routes_a_dictionary_without_calling_the_model():
    fake = FakeLLM(raw_map={})
    with patched(fake):
        chosen = asyncio.run(pb.build_learning_path(glossary_text(30), "s", source_kind="glossary"))
        auto = asyncio.run(pb.build_learning_path(glossary_text(100), "s"))
        with pytest.raises(pb.PathBuildError, match="не словарь"):
            asyncio.run(pb.build_learning_path("Обычный текст учебника без статей. " * 60, "s", source_kind="glossary"))
    assert fake.log == [] and chosen["source_type"] == auto["source_type"] == "glossary"


def test_a_dictionary_after_a_textbook_adds_terms_to_its_topics_through_the_worker():
    """Учебник через воркер (подставная модель), затем словарь: ИИ не вызывается, карточки лягут в темы учебника, повторы пропущены."""
    from sqlalchemy import delete, func, select
    from app.database.models import Card, GenerationJob, KnowledgeNode, Source
    from app.database.session import AsyncSessionLocal
    from app.services.generation_worker import process_generation_job
    from app.services.knowledge_path import get_path_state, wipe_subject
    from test_pipeline_quality import GOOD, _book, _cards_handler, _map
    user, subject = "test_glossary_user", "test_glossary_subject"

    async def upload(text, kind, fake):
        async with AsyncSessionLocal() as db:
            job = GenerationJob(user_id=user, subject=subject, theme="t", raw_text=text, status="processing", source_name=kind or "учебник",
                                source_kind=kind)
            db.add(job)
            await db.commit()
            job_id = job.id
        with patched(fake):
            await process_generation_job(job_id, is_offpeak=True)
        async with AsyncSessionLocal() as db:
            return (await db.get(GenerationJob, job_id)).status

    async def scenario():
        async with AsyncSessionLocal() as db:
            await wipe_subject(db, user, subject)
            await db.execute(delete(GenerationJob).where(GenerationJob.user_id == user))
            await db.commit()
        try:
            assert await upload(_book(), None, FakeLLM(raw_map=_map(), cards=_cards_handler(GOOD))) == "completed"
            async with AsyncSessionLocal() as db:
                before = (await db.execute(select(func.count(Card.id)).where(Card.user_id == user, Card.subject == subject))).scalar()
            quiet = FakeLLM(raw_map={})
            text = ("Срок полномочий судьи — срок, на который назначается судья Конституционного суда республики и после которого он уходит.\n"
                    + glossary_text(40))
            assert await upload(text, "glossary", quiet) == "completed"
            assert quiet.log == []                                                          # словарь не вызывал модель
            async with AsyncSessionLocal() as db:
                after = (await db.execute(select(func.count(Card.id)).where(Card.user_id == user, Card.subject == subject))).scalar()
                srcs = (await db.execute(select(Source).where(Source.user_id == user, Source.subject == subject).order_by(Source.id))).scalars().all()
                state = await get_path_state(db, user, subject)
            assert after > before and srcs[1].kind == "glossary" and srcs[1].cost_usd == 0.0
            assert srcs[1].cards_count == after - before
            assert len({n["key"] for n in state["nodes"]}) == len(state["nodes"])
        finally:
            async with AsyncSessionLocal() as db:
                await wipe_subject(db, user, subject)
                await db.execute(delete(GenerationJob).where(GenerationJob.user_id == user))
                await db.commit()

    asyncio.run(scenario())


# --- «пособие»: между шпаргалкой и учебником -------------------------------------------------------------------------------------

def test_a_study_guide_gets_more_cards_than_a_textbook_but_fewer_than_a_short_article():
    text = "Суд обязан рассмотреть дело в разумный срок и вынести законное решение. " * 1500
    assert sp.target_cards("textbook", text) < sp.target_cards("guide", text) < sp.target_cards("article", text)
    assert sp.resolve_kind("guide", text) == "guide" and sp.resolve_kind("glossary", text) == "glossary"
    assert sp.facts_per_card("guide") < sp.facts_per_card("textbook") and "SOURCE TYPE: study guide" in sp.kind_hint("guide")
    assert pb.normalize_map({"nodes": [{"key": "a", "name": "A", "tier": 0}], "source_type": "guide"})["source_type"] == "guide"
    from app.api.endpoints.imports import job_options
    assert job_options("", "guide")[1] == "guide" and job_options("", "glossary")[1] == "glossary"
