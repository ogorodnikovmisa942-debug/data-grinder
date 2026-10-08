"""Размер колоды задаёт содержание и общий потолок предмета, а не деньги; качество — отбраковка непроверенного и обрезка по важности."""
import asyncio

import test_pipeline_quality as tpq
from app.services.ai_gateway import card_quality as cq
from app.services.ai_gateway import path_builder as pb
from app.services.ai_gateway import source_profile as sp
from llm_fake import FakeLLM, patched


# --- размер колоды ----------------------------------------------------------------------------------------------------

def test_a_textbook_gets_about_five_hundred_cards_and_a_thick_one_is_capped():
    assert sp.deck_goal(493)["goal"] == 493 and sp.deck_goal(493)["limited_by"] is None
    big = sp.deck_goal(780)                                                     # 1,8 млн знаков: больше 600 не нужно
    assert big["goal"] == sp.MAX_CARDS_PER_SOURCE and big["limited_by"] == "source"


def test_depth_scales_the_deck_and_unknown_depth_is_standard():
    assert sp.deck_goal(500, "compact")["goal"] == 300
    assert sp.deck_goal(500, "detailed")["goal"] == 700
    assert sp.deck_goal(500, "что-то")["goal"] == 500 and sp.normalize_depth(None) == "standard"
    assert sp.deck_goal(900, "detailed")["goal"] == round(sp.MAX_CARDS_PER_SOURCE * 1.4)    # потолок материала тоже растёт с глубиной


def test_several_sources_share_one_course_budget():
    assert sp.deck_goal(200, existing_cards=500)["goal"] == 200                  # второй материал: мало нового, потолок не тронут
    full = sp.deck_goal(300, existing_cards=700)                                 # до потолка предмета (750) осталось 50
    assert full["goal"] == 50 and full["limited_by"] == "course" and full["room"] == 50
    assert sp.deck_goal(300, existing_cards=760, new_share=0.5)["goal"] == sp.MIN_ADD_CARDS      # курс полон, но новое есть: лучшие из нового
    assert sp.deck_goal(300, existing_cards=760, new_share=0.01)["goal"] == 1    # нового почти нет: одна карточка, чтобы тема не осталась пустой
    assert sp.deck_goal(15, existing_cards=760, new_share=0.9)["goal"] == 15     # маленький конспект не раздуваем до MIN_ADD_CARDS
    assert sp.deck_goal(200, "detailed", existing_cards=800)["goal"] == 250      # у подробной глубины потолок предмета выше (1050): остаётся 250, а хочется 280


def test_the_pipeline_reports_the_deck_policy_and_does_not_let_money_shrink_the_deck(monkeypatch):
    course = {"nodes": [{"key": "old", "name": "Старая тема", "tier": 1, "summary": "s"}],
              "cards": {"old": [{"q": f"Вопрос {i}?", "a": f"Ответ {i}."} for i in range(740)]}}
    fake = FakeLLM(raw_map={**tpq._map(), "source_type": "textbook"}, cards=tpq._cards_handler(tpq.GOOD))
    with patched(fake):
        res = asyncio.run(pb.build_learning_path(tpq._book(with_gap=True), "s", course=course, depth="compact"))
    pol = res["stats"]["deck_policy"]
    assert pol["depth"] == "compact" and pol["existing_cards"] == 740 and pol["mult"] == 0.6
    assert res["stats"]["source"]["kind"] == "textbook"
    declared = FakeLLM(raw_map={**tpq._map(), "source_type": "textbook"}, cards=tpq._cards_handler(tpq.GOOD))
    with patched(declared):
        res2 = asyncio.run(pb.build_learning_path(tpq._book(), "s", source_kind="lecture"))
    assert res2["stats"]["source"]["kind"] == "lecture"                           # тип, названный пользователем, главнее догадки модели


# --- качество ---------------------------------------------------------------------------------------------------------

def _c(text, answer, support, layer=1):
    return {"text": text, "translation": answer, "support": support, "layer": layer}


def test_card_priority_prefers_confirmed_specific_and_understanding_cards():
    strong = _c("Почему суд независим?", "Потому что подчиняется только закону 1 раз.", "grounded", 0)
    plain = _c("Как называется орган?", "Суд.", "lexical")
    bad = _c("Что это?", "Что-то.", "flagged")
    assert cq.card_priority(strong) > cq.card_priority(plain) > cq.card_priority(bad)
    assert cq.card_priority(plain, 1.8) > cq.card_priority(plain, 1.0) > cq.card_priority(plain, 0.4)      # насыщенный кусок книги важнее воды


def test_unsupported_cards_are_dropped_but_each_node_keeps_one():
    by = {"a": [_c("Q1", "A1", "grounded"), _c("Q2", "A2", "flagged")], "b": [_c("Q3", "A3", "flagged"), _c("Q4", "A4", "flagged")]}
    assert cq.drop_unsupported(by) == 2
    assert [c["text"] for c in by["a"]] == ["Q1"] and [c["text"] for c in by["b"]] == ["Q3"]


def test_trim_removes_the_least_important_cards_over_the_goal_and_keeps_one_per_node():
    by = {"a": [_c(f"Qa{i}", "ответ" if i else "Ответ 1 год", "grounded" if i < 2 else "lexical") for i in range(6)],
          "b": [_c("Qb", "Ответ", "lexical")]}
    assert cq.trim_to_goal(by, 100) == 0                                            # в пределах цели ничего не трогаем
    removed = cq.trim_to_goal(by, 4, tolerance=1.0)
    assert removed == 3 and sum(len(v) for v in by.values()) == 4 and len(by["b"]) == 1
    assert all(c["support"] == "grounded" for c in by["a"][:2]) and any(c["text"] == "Qa0" for c in by["a"])   # осталось подтверждённое и конкретное


def test_quality_report_counts_and_warns_only_for_real_decks():
    cards = [_c(f"Почему {i}?", "Ответ 5.", "grounded") for i in range(12)] + [_c("Как?", "Так.", "lexical") for _ in range(10)]
    rep = cq.quality_report(cards, dropped_unsupported=2, trimmed=1)
    assert rep["cards"] == 22 and rep["dropped_unsupported"] == 2 and rep["trimmed_by_priority"] == 1
    assert 0.5 < rep["supported_share"] < 0.6 and rep["duplicate_fronts"] == 9 and rep["warnings"]
    assert cq.quality_report(cards[:5])["warnings"] == []                           # на пяти карточках предупреждения не нужны


def test_pipeline_with_the_gate_drops_unconfirmed_cards_after_the_audit(monkeypatch):
    monkeypatch.setattr(pb, "QUALITY_GATE", True)
    invented = {"t": "Какой орган утверждает бюджет санатория?", "s": "Право | Тема", "d": "Попечительский совет.",
                "e": "", "l": "easy", "y": 1, "at": "organ", "ev": "Попечительский совет утверждает бюджет санатория",
                "x": ["Совет директоров.", "Наблюдательный совет.", "Правление."]}
    per_node = dict(tpq.GOOD, sub_a=[tpq._card("term"), invented])
    fake = FakeLLM(raw_map=tpq._map(), cards=tpq._cards_handler(per_node),
                   audit=lambda p, f: {"audit": [{"id": f"C{i}", "v": "skip"} for i in range(1, 9)]})
    with patched(fake):
        res = asyncio.run(pb.build_learning_path(tpq._book(), "s"))
    assert [c["translation"] for c in res["packs"]["sub_a"]["cards"]] == ["Шесть лет."]
    q = res["stats"]["quality"]
    assert q["dropped_unsupported"] == 1 and q["supported_share"] == 1.0


# --- параметры загрузки ----------------------------------------------------------------------------------------------

def test_job_options_map_the_old_preset_and_ignore_unknown_values():
    from app.api.endpoints.imports import job_options
    assert job_options("", "", "atomic") == ("standard", None)
    assert job_options("", "", "blitz") == ("compact", None) and job_options("", "", "detailed") == ("detailed", None)
    assert job_options("detailed", "notes", "blitz") == ("detailed", "notes")                  # названное явно главнее пресета
    assert job_options("слишком подробно", "роман", None) == ("standard", None)


def test_the_worker_passes_depth_and_kind_to_the_builder():
    from unittest.mock import patch
    from sqlalchemy import delete
    from app.database.models import GenerationJob
    from app.database.session import AsyncSessionLocal
    from app.services.generation_worker import process_generation_job
    from app.services.knowledge_path import wipe_subject
    from test_knowledge_path import fake_result
    seen = {}

    async def fake_build(text, subject, calls=None, course=None, **options):
        seen.update(options)
        calls.append({"label": "map#1", "cost_usd": 0.01, "prompt_tokens": 10, "cache_hit_tokens": 0, "completion_tokens": 1,
                      "duration_ms": 1, "finish_reason": "stop", "model": "deepseek-flash"})
        return fake_result()

    async def scenario():
        user, subject = "test_deck_policy_user", "test_deck_policy_subject"
        async with AsyncSessionLocal() as db:
            job = GenerationJob(user_id=user, subject=subject, theme="t", raw_text="Текст материала " * 10, status="processing",
                                source_name="Материал", depth="compact", source_kind="lecture")
            db.add(job)
            await db.commit()
            job_id = job.id
        try:
            with patch("app.services.generation_worker.build_learning_path", side_effect=fake_build):
                await process_generation_job(job_id, is_offpeak=True)
        finally:
            async with AsyncSessionLocal() as db:
                await wipe_subject(db, user, subject)
                await db.execute(delete(GenerationJob).where(GenerationJob.user_id == user))
                await db.commit()

    asyncio.run(scenario())
    assert seen == {"depth": "compact", "source_kind": "lecture"}


# --- урок-дополнение для совпавшей темы -------------------------------------------------------------------------------

def _supplement_run(new_cards: int):
    topics = [("присяга", "Судья приносит присягу при вступлении в должность."), ("мантия", "Мантию судья надевает на заседании."),
              ("самоотвод", "Судья заявляет самоотвод при личной заинтересованности."), ("совещание", "Тайну совещания судьи хранят до конца.")]

    def fresh(n):                                   # разные по смыслу карточки: похожие вопросы выбросил бы поиск повторов
        return [{"t": f"Что делает судья: {w}?", "s": "Право | Тема", "d": a, "e": "", "l": "easy", "y": 1, "at": "rule", "ev": "",
                 "x": ["Ничего.", "Молчит.", "Уходит."]} for w, a in topics[:n]]

    def align(prompt, fake):
        return {"align": [{"new": "sub_a", "same": "sub_a"}]}

    course = {"nodes": [{"key": "sub_a", "name": "Судья", "tier": 2, "summary": "Срок полномочий судьи"}],
              "cards": {"sub_a": [{"q": "Совсем другой вопрос?", "a": "Совсем другой ответ."}]}}
    per_node = dict(tpq.GOOD, sub_a=fresh(new_cards))
    fake = FakeLLM(raw_map={**tpq._map(), "source_type": "textbook"}, cards=tpq._cards_handler(per_node), align=align)
    with patched(fake):
        return fake, asyncio.run(pb.build_learning_path(tpq._book(), "s", course=course))


def test_a_merged_topic_with_enough_new_cards_gets_a_short_supplement_lesson():
    fake, res = _supplement_run(sp.SUPPLEMENT_MIN_CARDS)
    block = "\n".join(fake.of("LESSON"))
    assert "### sub_a |" in block and "SUPPLEMENT: the learner already finished" in block[block.index("### sub_a |"):block.index("### sub_a |") + 2500]
    assert res["merge"] == {"sub_a": "sub_a"} and res["supplements"] == ["sub_a"] and res["packs"]["sub_a"]["lesson"]
    assert "### sub_b |" in block and "SUPPLEMENT" not in block[block.index("### sub_b |"):block.index("### sub_b |") + 1500]    # у обычных тем пометки нет


def test_a_merged_topic_with_few_new_cards_stays_without_a_lesson():
    fake, res = _supplement_run(sp.SUPPLEMENT_MIN_CARDS - 1)
    assert res["supplements"] == [] and not res["packs"]["sub_a"]["lesson"] and "### sub_a |" not in "\n".join(fake.of("LESSON"))


def test_saving_a_supplement_creates_a_subtopic_with_its_cards_and_deleting_the_source_removes_it():
    from sqlalchemy import select
    from app.database.models import Card, KnowledgeNode
    from app.database.session import AsyncSessionLocal
    from app.services.knowledge_path import delete_source, save_learning_path, wipe_subject
    from test_knowledge_path import fake_result

    async def scenario():
        user, subject = "test_supplement_user", "test_supplement_subject"
        async with AsyncSessionLocal() as db:
            await wipe_subject(db, user, subject)
            await db.commit()
        try:
            async with AsyncSessionLocal() as db:
                first = await save_learning_path(db, user, subject, fake_result(), source_name="Учебник")
                await db.commit()
            second = fake_result()
            second["merge"] = {"base": "base"}                                        # у «основы» 5 новых карточек и урок
            async with AsyncSessionLocal() as db:
                stats = await save_learning_path(db, user, subject, second, source_name="Конспект")
                await db.commit()
                assert stats["supplements"] == 1 and stats["merged_nodes"] == 1
                nodes = {n.node_key: n for n in (await db.execute(select(KnowledgeNode).where(KnowledgeNode.user_id == user))).scalars().all()}
                sup = next(n for k, n in nodes.items() if k.startswith("base__add"))
                assert sup.name == "Основа: дополнение" and sup.tier == 2 and sup.parent_key == "base" and sup.prereq_keys == ["base"]
                assert sup.lesson_status == "ready" and sup.source_id == stats["source_id"]
                cards = (await db.execute(select(Card).where(Card.node_id == sup.id))).scalars().all()
                assert len(cards) == 5 and {c.organ_slug for c in cards} == {sup.node_key}
                base_cards = (await db.execute(select(Card).where(Card.node_id == nodes["base"].id))).scalars().all()
                assert len(base_cards) == 5                                          # карточки первого материала остались на «основе»
                await delete_source(db, user, subject, stats["source_id"])
                await db.commit()
                left = {n.node_key for n in (await db.execute(select(KnowledgeNode).where(KnowledgeNode.user_id == user))).scalars().all()}
                assert not any(k.startswith("base__add") for k in left) and "base" in left and first["nodes"] == 3
        finally:
            async with AsyncSessionLocal() as db:
                await wipe_subject(db, user, subject)
                await db.commit()

    asyncio.run(scenario())
