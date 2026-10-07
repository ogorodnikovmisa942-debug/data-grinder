"""Этапы 2–3: тип материала, насыщенность и новизна кусков, слияние тем нового материала с курсом (подставной DeepSeek, без трат)."""
import asyncio
from unittest.mock import patch

from sqlalchemy import delete, func, select

import test_pipeline_quality as tpq
from app.database.models import Card, GenerationJob, KnowledgeEdge, KnowledgeNode, Source, utc_now
from app.database.session import AsyncSessionLocal
from app.services.ai_gateway import coverage as cv
from app.services.ai_gateway import path_builder as pb
from app.services.ai_gateway import path_prompts as pp
from app.services.ai_gateway import source_profile as sp
from app.services.generation_worker import process_generation_job
from app.services.knowledge_path import delete_source, wipe_subject
from llm_fake import FakeLLM, keys_of, node_block, patched

_EVENTS = ["Коронация Миндовга в Новогрудке, единственный король в истории ВКЛ", "Битва при Синих Водах, Ольгерд разбил татар и присоединил земли",
           "Кревская уния, династический союз ВКЛ и Польши, Ягайло стал польским королём", "Привилей Ягайло, льготы феодалам-католикам",
           "Островское соглашение, Витовт становится фактическим правителем ВКЛ", "Грюнвальдская битва, разгром войск Тевтонского ордена",
           "Городельская уния, католическая шляхта получает права польской", "Ликвидация Киевского удельного княжества, наместник вместо князя",
           "Привилей Александра, ограничение власти великого князя Радой", "Первый Статут ВКЛ, свод законов на старобелорусском языке",
           "Второй Статут ВКЛ, судебная реформа и новые суды", "Люблинская уния, создание Речи Посполитой", "Третий Статут ВКЛ, высшее достижение права ВКЛ",
           "Брестская уния, создание униатской церкви под властью папы"]
CHEAT = "Шпаргалка по датам. " + " ".join(f"{1253 + 13 * i} — {e}." for i, e in enumerate(_EVENTS))
TEXTBOOK = ("Правовое государство характеризуется тем, что власть подчиняется закону и ограничена правами личности, "
            "а государственные органы действуют в пределах полномочий, которые определены нормами права. ") * 300
LECTURE = "Итак, давайте посмотрим на это. Значит, обратите внимание, запишите: это важно. Смотрите, как вы видите, ну вот. " * 40


# --- тип материала и цель ----------------------------------------------------------------

def test_kind_is_detected_by_the_text_when_the_model_does_not_say():
    assert sp.heuristic_kind(CHEAT) == "notes"
    assert sp.heuristic_kind(TEXTBOOK) == "textbook"
    assert sp.heuristic_kind("Фромм создал гуманистический психоанализ. " * 30) == "article"
    assert sp.heuristic_kind(LECTURE) == "lecture"


def test_declared_kind_wins_and_unknown_kinds_fall_back_to_the_text():
    assert sp.resolve_kind("notes", TEXTBOOK) == "notes"
    assert sp.resolve_kind("textbook", "короткий текст") == "textbook"                 # глава учебника остаётся учебником при любом размере
    assert sp.resolve_kind("слайды", CHEAT) == "notes" and sp.resolve_kind(None, TEXTBOOK) == "textbook"


def test_target_cards_follow_the_kind():
    items = sp.count_items(CHEAT)
    assert 12 <= items <= 20
    assert sp.target_cards("notes", CHEAT) == round(0.85 * items)                     # по карточке на пункт, а не по знакам
    assert sp.target_cards("textbook", TEXTBOOK) == round(len(TEXTBOOK) / 2300)
    assert sp.target_cards("lecture", TEXTBOOK) < sp.target_cards("textbook", TEXTBOOK) < sp.target_cards("article", TEXTBOOK)
    assert sp.target_cards("textbook", "x") == 1


def test_kind_hints_reach_the_cards_and_lesson_tasks():
    task = pp.build_cards_task("{}", ["a"], {"a": 3}, "notes")
    assert "SOURCE TYPE: notes" in task and "SOURCE TYPE" not in pp.build_cards_task("{}", ["a"], {"a": 3}, "textbook")
    assert "SOURCE TYPE: notes" in pp.build_lessons_task("x", "notes") and "SOURCE TYPE" not in pp.build_lessons_task("x", "textbook")
    asked = pp.build_cards_task("{}", ["a", "b"], None, None, {"a": ["Какой срок?", "Кто назначает?"]})
    assert "ALREADY ASKED: a: Какой срок? | Кто назначает?\n" in asked and "ALREADY ASKED: b" not in asked


# --- насыщенность и новизна -------------------------------------------------------------

def _windows_text():
    dense = ("Принцип законности — это требование точного соблюдения закона; принцип гласности называется открытостью; "
             "срок составляет 5 лет, а возраст не менее 30 лет. ") * 40
    water = ("Автор подробно рассуждает о значении явления, приводит общие соображения и возвращается к ним позднее в тексте. ") * 45
    refs = ("Радько, Т.Н. Теория государства и права. М., 2005. С. 96; Корельский, В.М. Проблемы понимания. С. 12 [3]. ") * 50
    toc = "".join(f"Глава {i}. Название раздела ...................... {i * 7}\n" for i in range(1, 120))
    return dense + "\n" + water + "\n" + refs + "\n" + toc


def test_saturation_rewards_dense_text_and_punishes_references_and_contents():
    text = _windows_text()
    index = cv.SourceIndex(text)
    w = cv.window_saturation(index)
    assert len(w) == len(index.spans) and all(cv.WEIGHT_MIN <= x <= cv.WEIGHT_MAX for x in w)
    total = sum(b - a for a, b in index.spans)
    assert abs(sum(x * (b - a) for x, (a, b) in zip(w, index.spans)) / total - 1.0) < 0.5      # в среднем около 1: сумма карточек не раздувается
    first, last = w[0], w[-1]
    assert first > 1.2 and last <= 0.5                                                          # насыщенное окно выше, оглавление в самом низу
    assert w[0] > min(w[1:-1] or [1])                                                           # и вода/ссылки ниже насыщенного


def test_chapter_summaries_gain_weight_and_review_questions_lose_it():
    line = "Суд рассматривает дела в порядке, установленном процессуальным законом, и принимает решение именем республики.\n"
    plain = line * 120                                  # ~13 тыс. знаков: больше двух окон без заголовков
    text = plain + "Выводы\n" + plain + "Вопросы для самоконтроля\n" + plain
    index = cv.SourceIndex(text)
    w = cv.window_saturation(index)
    at = lambda marker: next(i for i, (a, b) in enumerate(index.spans) if a <= text.index(marker) < b)
    neutral = next(i for i, (a, b) in enumerate(index.spans) if "Выводы" not in text[a:b] and "Вопросы для" not in text[a:b])
    assert w[at("Выводы")] > w[neutral] > w[at("Вопросы для самоконтроля")]


def test_novelty_marks_windows_that_existing_cards_already_cover():
    parts = [("Суд кассационной инстанции проверяет законность вступивших в силу судебных актов и вправе отменить решение. " * 60),
             ("Прокуратура осуществляет надзор за точным исполнением законов, а прокурор назначается Президентом республики. " * 60),
             ("Нотариус удостоверяет сделки и выдаёт свидетельство о праве на наследство наследникам умершего. " * 60),
             ("Адвокатура оказывает юридическую помощь, адвокат хранит адвокатскую тайну и не разглашает сведения клиента. " * 60)]
    index = cv.SourceIndex("\n".join(parts))
    assert len(index.spans) >= 4
    cards = ["Кто осуществляет надзор за точным исполнением законов? Прокуратура. Прокурор назначается Президентом республики.",
             "Кто удостоверяет сделки и выдаёт свидетельство о праве на наследство? Нотариус наследникам умершего."]
    nov = cv.window_novelty(index, cards)
    assert len(nov) == len(index.spans)
    assert 0.0 in nov and 1.0 in nov                                                            # часть окон покрыта карточками, часть нет
    assert cv.window_novelty(index, []) == [1.0] * len(index.spans)
    eff = cv.effective_chars(index, nov)
    assert cv.COVERED_FLOOR * len(index.text) < eff < len(index.text)


# --- слияние тем ----------------------------------------------------------------------------

def test_candidates_come_from_node_names_and_the_align_answer_is_validated():
    existing = [{"key": "e_sources", "name": "Источники права", "tier": 1, "summary": "Формы выражения права"},
                {"key": "e_state", "name": "Государство", "tier": 0, "summary": "Политическая организация"},
                {"key": "e_case", "name": "Кейс источников", "tier": 3, "summary": ""}]
    new = [{"key": "n_sources", "name": "Источники права", "tier": 1, "summary": "Нормативные акты и обычаи"},
           {"key": "n_other", "name": "Налоги", "tier": 2, "summary": "Сборы"},
           {"key": "n_case", "name": "Кейс источников", "tier": 3, "summary": ""}]
    cands = pb.node_candidates(new, existing)
    assert cands["n_sources"][0] == "e_sources" and cands["n_other"] == []
    by_new, by_ex = {n["key"]: n for n in new}, {n["key"]: n for n in existing}
    ok = pb.normalize_align({"align": [{"new": "n_sources", "same": "e_sources"}]}, cands, by_new, by_ex)
    assert ok == {"n_sources": "e_sources"}
    bad = {"align": [{"new": "n_sources", "same": "e_state"},               # не из кандидатов
                     {"new": "n_other", "same": "e_sources"},                # не из кандидатов
                     {"new": "n_case", "same": "e_case"}]}                   # кейсы не сливаем
    assert pb.normalize_align(bad, cands, by_new, by_ex) == {}
    twice = {"align": [{"new": "n_sources", "same": "e_sources"}, {"new": "n_sources", "same": "e_sources"}]}
    assert pb.normalize_align(twice, cands, by_new, by_ex) == {"n_sources": "e_sources"}


def _course():
    return {"nodes": [{"key": "sub_a", "name": "Судья", "tier": 2, "summary": "Срок полномочий судьи"},
                      {"key": "old_only", "name": "Присяга", "tier": 2, "summary": "Текст присяги"}],
            "cards": {"sub_a": [{"q": tpq.FACTS["term"][0], "a": tpq.FACTS["term"][1]}]}}


def test_second_material_merges_matching_topics_and_asks_only_what_is_new():
    def align(prompt, fake):
        assert "TYPE: ALIGN" in prompt and "candidates:" in prompt and tpq.BUDGET_FACT not in prompt        # книги в запросе нет
        return {"align": [{"new": "sub_a", "same": "sub_a"}]}

    fake = FakeLLM(raw_map={**tpq._map(), "source_type": "textbook"}, cards=tpq._cards_handler(tpq.GOOD), align=align)
    with patched(fake):
        res = asyncio.run(pb.build_learning_path(tpq._book(), "s", course=_course()))
    assert res["merge"] == {"sub_a": "sub_a"} and res["source_type"] == "textbook"
    cards_prompts = "\n".join(fake.of("CARDS"))
    assert "ALREADY ASKED: sub_a: " + tpq.FACTS["term"][0] in cards_prompts
    assert "sub_a" not in res["packs"]                                                                  # то же слово в карточке не повторили
    assert res["stats"]["source"]["merged_nodes"] == 1 and res["stats"]["source"]["dropped_existing_fronts"] == 1
    lesson_text = "\n".join(fake.of("LESSON"))
    assert "### sub_a |" not in lesson_text and "### sub_b |" in lesson_text                            # у совпавшей темы урок уже есть
    assert res["stats"]["quota"]["target"] == sum(res["quotas"].values())


def test_without_a_course_there_is_no_align_call_and_the_kind_comes_from_the_map():
    fake = FakeLLM(raw_map={**tpq._map(), "source_type": "notes"}, cards=tpq._cards_handler(tpq.GOOD))
    with patched(fake):
        res = asyncio.run(pb.build_learning_path(tpq._book(), "s"))
    assert not fake.of("ALIGN") and res["merge"] == {} and res["source_type"] == "notes"
    assert all("SOURCE TYPE: notes" in p for p in fake.of("CARDS"))
    assert "SOURCE TYPE: notes" in "\n".join(fake.of("LESSON"))


def test_align_failure_means_no_merge_not_a_failed_build():
    def align(prompt, fake):
        raise RuntimeError("DeepSeek недоступен")

    fake = FakeLLM(raw_map=tpq._map(), cards=tpq._cards_handler(tpq.GOOD), align=align)
    with patched(fake):
        res = asyncio.run(pb.build_learning_path(tpq._book(), "s", course=_course()))
    assert res["merge"] == {} and "sub_a" in res["packs"]
    assert res["stats"]["source"]["align"].get("error")


def test_covered_windows_shrink_the_target():
    book = tpq._book()
    cards_covering_everything = [f"{q} {a}" for q, a, _ in tpq.FACTS.values()]
    course = {"nodes": [{"key": "x", "name": "Другое", "tier": 2, "summary": ""}], "cards": {"x": [{"q": q, "a": a} for q, a, _ in tpq.FACTS.values()]}}
    fake = FakeLLM(raw_map={**tpq._map(), "source_type": "textbook"}, cards=tpq._cards_handler(tpq.GOOD))
    with patched(fake):
        fresh = asyncio.run(pb.build_learning_path(book, "s"))
    fake2 = FakeLLM(raw_map={**tpq._map(), "source_type": "textbook"}, cards=tpq._cards_handler(tpq.GOOD))
    with patched(fake2):
        again = asyncio.run(pb.build_learning_path(book, "s", course=course))
    assert again["stats"]["source"]["effective_chars"] <= fresh["stats"]["source"]["effective_chars"]
    assert again["stats"]["source"]["target"] <= fresh["stats"]["source"]["target"]
    assert cards_covering_everything


# --- сохранение слитых тем -----------------------------------------------------------------

USER = "test_stage23_user"
SUBJECT = "test_stage23_subject"


def _result(with_merge: bool):
    from test_knowledge_path import fake_result
    res = fake_result()
    if with_merge:
        res["merge"] = {"base": "base"}
        res["packs"]["base"]["lesson"] = None                                           # у совпавшей темы нового урока нет
    res["source_type"] = "notes" if with_merge else "textbook"
    res["facts"] = ({"base": ["Присяжные заседатели решают вопросы факта по уголовным делам.", FACT_A], "topic": ["Нотариус удостоверяет подлинность подписи на документе."]}
                    if with_merge else
                    {"base": [FACT_A, "Прокурор поддерживает государственное обвинение в суде."], "sub": ["Мировой судья рассматривает дела о мелких правонарушениях."]})
    return res


FACT_A = "Судебная власть самостоятельна и независима от законодательной власти."


async def _cleanup():
    async with AsyncSessionLocal() as db:
        await wipe_subject(db, USER, SUBJECT)
        await db.execute(delete(GenerationJob).where(GenerationJob.user_id == USER))
        await db.commit()


async def _job(name):
    async with AsyncSessionLocal() as db:
        job = GenerationJob(user_id=USER, subject=SUBJECT, theme="t", raw_text="Текст материала " * 10, status="processing", source_name=name)
        db.add(job)
        await db.commit()
        return job.id


def test_merged_topic_gets_new_cards_without_a_second_node_and_shared_nodes_survive_deletion():
    calls_seen = []

    async def fake_build(text, subject, calls=None, course=None):
        calls_seen.append(course)
        calls.append({"label": "map#1", "cost_usd": 0.01, "prompt_tokens": 10, "cache_hit_tokens": 0, "completion_tokens": 1,
                      "duration_ms": 1, "finish_reason": "stop", "model": "deepseek-flash"})
        return _result(with_merge=len(calls_seen) > 1)

    async def scenario():
        await _cleanup()
        try:
            with patch("app.services.generation_worker.build_learning_path", side_effect=fake_build):
                await process_generation_job(await _job("Учебник"), is_offpeak=True)
                await process_generation_job(await _job("Конспект"), is_offpeak=True)
            assert calls_seen[0] is None and {n["key"] for n in calls_seen[1]["nodes"]} == {"base", "topic", "sub"}     # второй получил курс
            assert len(calls_seen[1]["cards"]["base"]) == 5

            async with AsyncSessionLocal() as db:
                a, b = (await db.execute(select(Source).where(Source.user_id == USER).order_by(Source.id))).scalars().all()
                assert (a.kind, b.kind) == ("textbook", "notes")
                nodes = (await db.execute(select(KnowledgeNode).where(KnowledgeNode.user_id == USER))).scalars().all()
                keys = sorted(n.node_key for n in nodes)
                assert "base" in keys and keys.count("base") == 1                           # «основа» одна: тема слита
                assert len(nodes) == 3 + 2                                                  # у второго материала только topic и sub (с суффиксом)
                base = next(n for n in nodes if n.node_key == "base")
                assert base.source_id == a.id
                base_cards = (await db.execute(select(Card).where(Card.node_id == base.id))).scalars().all()
                assert len(base_cards) == 10 and {c.source_id for c in base_cards} == {a.id, b.id}                      # карточки обоих материалов
                assert (b.nodes_count, b.cards_count) == (2, 7)
                assert [f["t"] for f in base.facts] == [FACT_A, "Прокурор поддерживает государственное обвинение в суде.",
                                                        "Присяжные заседатели решают вопросы факта по уголовным делам."]
                assert [f["s"] for f in base.facts] == [a.id, a.id, b.id]                    # у каждого факта известен материал
                second = {n.node_key.split("__")[0]: [f["t"] for f in n.facts] for n in nodes if n.source_id == b.id and n.facts}
                assert second == {"topic": ["Нотариус удостоверяет подлинность подписи на документе."]}      # у новых тем факты свои
                assert [f["t"] for f in next(n for n in nodes if n.node_key == "sub").facts] == ["Мировой судья рассматривает дела о мелких правонарушениях."]
                edges = (await db.execute(select(KnowledgeEdge).where(KnowledgeEdge.user_id == USER))).scalars().all()
                assert len({(e.source_key, e.target_key, e.relation) for e in edges}) == len(edges)

                gone = await delete_source(db, USER, SUBJECT, a.id)                         # удаляем ПЕРВЫЙ: «основа» нужна карточкам второго
                await db.commit()
                assert gone["kept_shared_nodes"] == 1
                base2 = (await db.execute(select(KnowledgeNode).where(KnowledgeNode.user_id == USER, KnowledgeNode.node_key == "base"))).scalar_one()
                assert base2.source_id == b.id
                left = (await db.execute(select(Card).where(Card.node_id == base2.id))).scalars().all()
                assert len(left) == 5 and {c.source_id for c in left} == {b.id}
                # Конспект темы: факты второго материала дописаны в имеющуюся тему (повтор не добавлен), у удалённого материала пропали
                assert [f["t"] for f in base2.facts] == ["Присяжные заседатели решают вопросы факта по уголовным делам."] and base2.facts[0]["s"] == b.id
        finally:
            await _cleanup()

    asyncio.run(scenario())
