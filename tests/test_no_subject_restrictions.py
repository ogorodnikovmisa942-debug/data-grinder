"""Никаких ограничений «по предмету»: ни в промптах, ни в разборе ответов, ни в сохранении. Плюс «лёгкий» формат карточки."""
import asyncio
from unittest.mock import patch

from sqlalchemy import delete, select

from app.database.models import AiTelemetryLog, GenerationJob, KnowledgeNode
from app.database.session import AsyncSessionLocal
from app.services.ai_gateway import path_builder as pb
from app.services.ai_gateway import path_prompts as pp
from app.services.generation_worker import process_generation_job
from app.services.knowledge_path import wipe_subject
from llm_fake import FakeLLM, patched, keys_of

USER = "test_no_subject_restrictions_user"
SUBJECT = "test_nsr_subject"


def _raw(domain):
    return {"title": "Курс", "domain": domain, "nodes": [
        {"key": "b", "name": "Основа", "tier": 0, "order": 1, "kind": "background"},          # прежний признак «справочный» игнорируется
        {"key": "t", "name": "Тема", "tier": 1, "order": 2},
        {"key": "s", "name": "Подтема", "tier": 2, "parent": "t", "order": 3},
        {"key": "hist", "name": "История вопроса", "tier": 2, "parent": "t", "order": 4, "kind": "background"},
    ], "edges": []}


def test_every_node_is_core_whatever_the_discipline_or_the_model_says():
    for domain in ("law", "history", "physics", "generic"):
        kinds = {n["key"]: n["kind"] for n in pb.normalize_map(_raw(domain))["nodes"]}
        assert set(kinds.values()) == {"core"}, domain


def test_prompt_has_no_subject_specific_rules_or_examples():
    sp = pp.PATH_BUILDER_SYSTEM_PROMPT
    for word in ("судоустр", "судеб", "подсудн", "инстанц", "ГПК", "прокурор", "кассац", "апелляц", "Хаммурап", "legislation",
                 "jurisdiction", "appealed_to", "excludes_application", "LIGHT NODES", "background", "obsolete", "quorum"):
        assert word not in sp, word
    assert "Skip only what is not subject matter" in sp and "whatever it concerns" in sp
    for relation in ("part_of", "depends_on", "kind_of", "demarcated_from", "leads_to", "applies_to", "example_of"):
        assert relation in sp
    assert "LIGHT" not in pp.build_lessons_task("x") and "LIGHT" not in pp.build_cards_task("{}", ["a"], {"a": 3})


def test_old_relation_types_are_still_accepted_so_existing_courses_do_not_break():
    nodes = [{"key": "a", "name": "A", "tier": 0, "order": 1}, {"key": "b", "name": "B", "tier": 1, "order": 2}]
    for rel in ("appealed_to", "applies_to", "example_of"):
        m = pb.normalize_map({"nodes": nodes, "edges": [{"from": "a", "to": "b", "relation": rel, "label": "x"}]})
        assert m["edges"][0]["relation"] == rel


def test_a_history_section_in_a_non_history_book_gets_normal_cards(monkeypatch):
    """Раньше такие узлы получали ≤3 карточки и «короткий урок». Теперь — как любой другой."""
    from llm_fake import default_cards
    monkeypatch.setattr(pb, "CARD_CAPS", False)
    path_map = pb.normalize_map(_raw("law"))
    fake = FakeLLM(cards=lambda prompt, f: {"nodes": [{"key": k, "cards": default_cards(k, 8)} for k in keys_of(prompt)]})
    with patched(fake):
        res = asyncio.run(pb.build_cards("КНИГА", path_map, ["s", "hist"], [], quotas={"s": 5, "hist": 6}))
    assert len(res["hist"]) == 8 and len(res["s"]) == 8
    assert "LIGHT" not in fake.of("CARDS")[0]


# --- «лёгкая» карточка: контекст и сложность добавляет программа ---

def test_card_gets_its_context_line_and_difficulty_from_the_app():
    m = pb.normalize_map(_raw("physics"))
    raw = {"nodes": [{"key": "s", "cards": [
        {"t": "Как называется величина, равная произведению массы тела на его скорость?", "d": "Импульс тела.", "y": 0, "at": "term",
         "ev": "импульс тела равен произведению массы на скорость", "x": ["Энергия.", "Сила.", "Работа."]},
        {"t": "При каком условии импульс замкнутой системы сохраняется?", "d": "Если внешние силы отсутствуют.", "y": 2, "x": []},
    ]}]}
    cards = pb.normalize_cards(raw, m, ["s"])["s"]
    assert cards[0]["secondary_text"] == "Курс | Подтема" and cards[0]["example"] == ""            # «s» и «e» модель не писала
    assert [c["initial_difficulty_tier"] for c in cards] == ["easy", "hard"]                      # сложность из слоя
    explicit = pb.normalize_cards({"nodes": [{"key": "s", "cards": [
        {"t": "Какая сила удерживает планеты на орбитах?", "d": "Тяготение.", "s": "Физика | Гравитация", "l": "medium", "y": 0, "x": []}]}]},
        m, ["s"])["s"][0]
    assert explicit["secondary_text"] == "Физика | Гравитация" and explicit["initial_difficulty_tier"] == "medium"   # явное значение уважается


def test_lean_schema_in_prompt_has_no_context_or_difficulty_fields():
    sp = pp.PATH_BUILDER_SYSTEM_PROMPT
    assert "Do NOT write a context line ('s') or a difficulty ('l')" in sp
    schema = sp.split("CARDS JSON SCHEMA")[1].split("PART C")[0]
    assert '"s":' not in schema and '"l":' not in schema and '"ev":' in schema and '"x":' in schema


# --- STRICT: «берём самое важное» только когда не хватает денег ---

def test_the_target_is_always_the_maximum_and_there_is_no_floor_mode():
    import test_pipeline_quality as tpq
    fake = FakeLLM(raw_map=tpq._map(), cards=tpq._cards_handler(tpq.GOOD))
    with patched(fake):
        res = asyncio.run(pb.build_learning_path(tpq._book(), "s"))
    assert fake.of("CARDS") and all("STRICT TARGET" not in p for p in fake.of("CARDS"))          # режима «нижняя граница» больше нет
    assert res["stats"]["quota"]["target"] == sum(res["quotas"].values())

    path_map = pb.normalize_map(tpq._map())
    big = {k: 60_000 for k in ("sub_a", "sub_b", "sub_c", "sub_d")}                                # книга большая: по размеру нужно много
    goal = pb.plan_card_quotas(tpq._book(), path_map, big, total=40)
    less = pb.plan_card_quotas(tpq._book(), path_map, big, total=20)
    assert sum(less.values()) < sum(goal.values())
    task = pp.build_cards_task("{}", ["sub_a"], less)
    assert f"TARGET CARDS: sub_a={less['sub_a']}\n" in task


def test_kind_is_saved_as_core_for_every_node():
    path_map = pb.normalize_map(_raw("law"))
    lesson = {"screens": [{"say": "1", "emo": "talk", "focus": []}] * 3, "check": []}

    async def fake_build(text, subject, calls=None, course=None, **options):
        return {"map": path_map, "packs": {n["key"]: {"lesson": lesson, "cards": []} for n in path_map["nodes"]},
                "missing_nodes": [], "calls": [], "cost_usd": 0.0}

    async def scenario():
        async with AsyncSessionLocal() as db:
            await wipe_subject(db, USER, SUBJECT)
            job = GenerationJob(user_id=USER, subject=SUBJECT, theme="Т", raw_text="Текст учебника " * 10, status="processing")
            db.add(job)
            await db.commit()
            job_id = job.id
        try:
            with patch("app.services.generation_worker.build_learning_path", side_effect=fake_build):
                await process_generation_job(job_id, is_offpeak=True)
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(select(KnowledgeNode).where(KnowledgeNode.user_id == USER))).scalars().all()
                assert {r.node_key: r.kind for r in rows} == {"b": "core", "t": "core", "s": "core", "hist": "core"}
        finally:
            async with AsyncSessionLocal() as db:
                await wipe_subject(db, USER, SUBJECT)
                await db.execute(delete(GenerationJob).where(GenerationJob.user_id == USER))
                await db.execute(delete(AiTelemetryLog).where(AiTelemetryLog.user_id == USER))
                await db.commit()

    asyncio.run(scenario())
