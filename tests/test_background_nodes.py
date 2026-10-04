"""Справочные узлы (история, предыстория): лёгкие уроки и карточки; в учебнике по истории всё основное."""
import asyncio
from unittest.mock import patch

from sqlalchemy import delete, select

from app.database.models import AiTelemetryLog, GenerationJob, KnowledgeNode
from app.database.session import AsyncSessionLocal
from app.services.ai_gateway import path_builder as pb
from app.services.ai_gateway.path_prompts import build_node_pack_task, PATH_BUILDER_SYSTEM_PROMPT
from app.services.generation_worker import process_generation_job
from app.services.knowledge_path import wipe_subject

USER = "test_background_nodes_user"
SUBJECT = "test_bg_subject"


def _raw(domain):
    return {"title": "T", "domain": domain, "nodes": [
        {"key": "b", "name": "Основа", "tier": 0, "order": 1, "kind": "background"},      # ярус 0 справочным быть не может
        {"key": "t", "name": "Тема", "tier": 1, "order": 2},
        {"key": "s", "name": "Подтема", "tier": 2, "parent": "t", "order": 3},
        {"key": "hist", "name": "История института", "tier": 2, "parent": "t", "order": 4, "kind": "background"},
    ], "edges": []}


def test_background_kind_only_for_tier2_and_never_in_a_history_course():
    law = {n["key"]: n["kind"] for n in pb.normalize_map(_raw("law"))["nodes"]}
    assert law == {"b": "core", "t": "core", "s": "core", "hist": "background"}
    history = {n["key"]: n["kind"] for n in pb.normalize_map(_raw("history"))["nodes"]}
    assert set(history.values()) == {"core"}                    # в учебнике по истории история — основной материал


def test_light_nodes_get_short_task_and_card_cap():
    task = build_node_pack_task("{}", ["s", "hist"], {"s": 8, "hist": 6}, ["hist"])
    assert "TARGET CARDS: s=8, hist=6\nLIGHT NODES: hist\n" in task
    assert "LIGHT NODES" not in build_node_pack_task("{}", ["s"], {"s": 8})
    assert "LIGHT NODES:" in PATH_BUILDER_SYSTEM_PROMPT and '"kind": "background"' in PATH_BUILDER_SYSTEM_PROMPT

    path_map = pb.normalize_map(_raw("law"))
    text = ("Районный суд рассматривает дела. История института насчитывает много лет. " * 40 + "\n") * 40
    quotas = pb.plan_card_quotas(text, path_map, size_hints={"hist": 40_000, "s": 40_000})
    assert quotas["s"] > quotas["hist"] == pb.BACKGROUND_MAX_CARDS


def test_pack_request_marks_background_nodes_as_light():
    path_map = pb.normalize_map(_raw("law"))
    prompts = []

    async def fake_call(user_prompt, **kwargs):
        prompts.append(user_prompt)
        keys = user_prompt.split("NODES TO PRODUCE: ")[1].split("\n")[0].split(", ")
        lesson = {"screens": [{"say": "1"}, {"say": "2"}, {"say": "3"}], "check": []}
        return {"nodes": [{"key": k, "lesson": lesson, "cards": []} for k in keys]}, {"cost_usd": 0.0}

    with patch("app.services.ai_gateway.client.call_deepseek", side_effect=fake_call):
        asyncio.run(pb.build_node_pack("КНИГА", path_map, ["s", "hist"], [], quotas={"s": 5, "hist": 3}))
    assert "LIGHT NODES: hist\n" in prompts[0]


def test_kind_is_saved_and_returned_with_the_node():
    path_map = pb.normalize_map(_raw("law"))
    lesson = {"screens": [{"say": "1", "emo": "talk", "focus": []}] * 3, "check": []}

    async def fake_build(text, subject, calls=None):
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
                assert {r.node_key: r.kind for r in rows} == {"b": "core", "t": "core", "s": "core", "hist": "background"}
                assert next(r for r in rows if r.node_key == "hist").to_dict()["kind"] == "background"
        finally:
            async with AsyncSessionLocal() as db:
                await wipe_subject(db, USER, SUBJECT)
                await db.execute(delete(GenerationJob).where(GenerationJob.user_id == USER))
                await db.execute(delete(AiTelemetryLog).where(AiTelemetryLog.user_id == USER))
                await db.commit()

    asyncio.run(scenario())
