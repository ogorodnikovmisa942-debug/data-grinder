"""Правки нарезки по разбору 2026-10-08: пауза при 429/5xx, бюджет узлов по типу пользователя, повтор пустых узлов, одно вводное
урок на курс, сверка с карточками других материалов, PDF без молчаливой обрезки, предупреждения в сообщении «готово»."""
import asyncio
import io
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.ai_gateway import card_quality
from app.services.ai_gateway import path_builder as pb
from app.services.ai_gateway import source_profile as sp
from app.services.ai_gateway.client import LLMTransientError, MODEL_FALLBACK
from llm_fake import FakeLLM, default_cards, keys_of, patched
from test_pipeline_quality import GOOD, _book, _cards_handler, _map, _run


# --- повторы того, что курс уже спрашивает ---------------------------------------------------------------------------------------------

def test_cards_repeating_another_material_are_dropped_but_new_ones_stay():
    cards = {"n": [{"text": "Как называется деятельность суда по рассмотрению дел?", "translation": "Правосудие."},
                   {"text": "Какой срок полномочий судьи?", "translation": "Пять лет."}]}
    existing = [("Как называется деятельность суда по рассмотрению дел?", "Правосудие."), ("Кто назначает прокурора?", "Президент.")]
    assert card_quality.dedupe_against(cards, existing) == 1
    assert [c["translation"] for c in cards["n"]] == ["Пять лет."]
    assert card_quality.dedupe_against(cards, []) == 0
    # узел, у которого все карточки уже есть в курсе, не остаётся пустым: одна карточка сохраняется
    only_dups = {"m": [{"text": "Как называется деятельность суда по рассмотрению дел?", "translation": "Правосудие."}]}
    assert card_quality.dedupe_against(only_dups, existing) == 0 and len(only_dups["m"]) == 1


# --- 429/5xx: пауза и тот же модель; зависание: как раньше, последняя попытка на страховочной модели --------------------------------

def _flaky(errors):
    seen = []

    async def call(prompt, system_instruction=None, **kwargs):
        seen.append(kwargs.get("model"))
        if len(seen) <= len(errors):
            raise errors[len(seen) - 1]
        return {"ok": True}, {"prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0}
    return call, seen


def test_overload_is_retried_with_a_pause_on_the_same_model():
    call, seen = _flaky([LLMTransientError("503"), LLMTransientError("503")])
    sleeps = AsyncMock()
    log = []
    with patch("app.services.ai_gateway.client.call_deepseek", new=call), patch.object(pb.asyncio, "sleep", new=sleeps):
        assert asyncio.run(pb._call("p", 100, "t", log)) == {"ok": True}
    assert seen == [None, None, None]                                   # pro не подключали: он в 3–7 раз дороже
    assert [c.args[0] for c in sleeps.call_args_list] == [pb.HANG_BACKOFF_S, pb.HANG_BACKOFF_S * 2]
    assert [bool(r["error"]) for r in log] == [True, True, False]


def test_a_hang_still_ends_on_the_fallback_model():
    call, seen = _flaky([asyncio.TimeoutError(), asyncio.TimeoutError()])
    with patch("app.services.ai_gateway.client.call_deepseek", new=call), patch.object(pb.asyncio, "sleep", new=AsyncMock()):
        asyncio.run(pb._call("p", 100, "t", []))
    assert seen == [None, None, MODEL_FALLBACK]


def test_persistent_overload_raises_after_three_tries():
    call, seen = _flaky([LLMTransientError("503")] * 5)
    with patch("app.services.ai_gateway.client.call_deepseek", new=call), patch.object(pb.asyncio, "sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError):
            asyncio.run(pb._call("p", 100, "t", []))
    assert len(seen) == 3


# --- бюджет узлов следует типу, названному пользователем --------------------------------------------------------------------------

def test_node_budget_follows_the_kind_chosen_by_the_user():
    text = "Государство обладает суверенитетом, территорией и населением. " * 500       # ≈ 30 тыс. знаков: по эвристике «статья»
    assert sp.heuristic_kind(text) == "article"
    prompts = []

    async def capture(prompt, *a, **k):
        prompts.append(prompt)
        raise RuntimeError("стоп")

    async def run(kind):
        with patch.object(pb, "_call", new=capture):
            with pytest.raises(pb.PathBuildError):
                await pb.build_knowledge_map(text, "s", [], attempts=1, source_kind=kind)

    asyncio.run(run(None))
    asyncio.run(run("textbook"))
    total = lambda p: int(p.split("NODE BUDGET: about ")[1].split(" nodes")[0])
    assert total(prompts[0]) == sp.node_budget(sp.target_cards("article", text))["total"]
    assert total(prompts[1]) == sp.node_budget(sp.target_cards("textbook", text))["total"]
    assert total(prompts[1]) < total(prompts[0])                               # глава учебника — меньше узлов, чем у статьи того же размера


# --- пустые узлы получают ещё одну попытку; вводный урок один на курс ------------------------------------------------------------------

def test_a_node_that_got_no_cards_is_asked_again_once_more():
    asked = {"sub_b": 0}

    def handler(prompt, fake):
        out = []
        for k in keys_of(prompt):
            if k == "sub_b":
                asked["sub_b"] += 1
                if asked["sub_b"] <= 3:                                         # три попытки внутри партии пропали
                    continue
            out.append({"key": k, "cards": GOOD.get(k) or default_cards(k)})
        return {"nodes": out}

    fake = FakeLLM(raw_map=_map(), cards=handler)
    with patch.object(pb.asyncio, "sleep", new=AsyncMock()):
        res = _run(fake)
    assert "sub_b" in res["packs"] and res["missing_nodes"] == [] and asked["sub_b"] == 4


def test_the_intro_lesson_is_not_written_again_for_a_course_that_has_one():
    first = FakeLLM(raw_map=_map(), cards=_cards_handler(GOOD))
    _run(first)
    assert first.of("INTRO")
    again = FakeLLM(raw_map=_map(), cards=_cards_handler(GOOD))
    with patched(again):
        res = asyncio.run(pb.build_learning_path(_book(), "s", course={"has_intro": True}))
    assert not again.of("INTRO") and res["intro"] is None and res["packs"]


# --- основа главы, чьи темы влились в курс, не остаётся одинокой точкой ------------------------------------------------------------------

def _node(key, tier, parent=None, prereqs=()):
    return {"key": key, "name": key, "tier": tier, "parent": parent, "prereqs": list(prereqs), "order": 1, "summary": "", "src": "", "kind": "core"}


def test_a_foundation_whose_topics_all_merged_joins_the_course_foundation():
    course = [{"key": "c1_base", "name": "Основы 1", "tier": 0, "prereqs": [], "parent": None},
              {"key": "c1_topic", "name": "Тема", "tier": 1, "prereqs": ["c1_base"], "parent": None},
              {"key": "c1_sub", "name": "Подтема", "tier": 2, "prereqs": ["c1_topic"], "parent": "c1_topic"}]
    new_map = {"nodes": [_node("c2_base", 0), _node("c2_topic", 1, prereqs=["c2_base"]), _node("c2_sub", 2, "c2_topic", ["c2_topic"])]}
    # тема слилась с имеющейся → основа слита с основой, на которую опирается эта тема; подтема остаётся новой
    assert pb.orphan_foundations(new_map, {"c2_topic": "c1_topic"}, course) == {"c2_base": "c1_base"}
    # слилась только подтема (ярус 2): основа остаётся, у неё есть своя тема
    assert pb.orphan_foundations(new_map, {"c2_sub": "c1_sub"}, course) == {}
    # ничего не слилось — ничего не меняем
    assert pb.orphan_foundations(new_map, {}, course) == {}
    # тема слилась с подтемой: основу курса находим, поднимаясь по пререквизитам
    assert pb.orphan_foundations(new_map, {"c2_topic": "c1_sub"}, course) == {"c2_base": "c1_base"}
    # у основы нет тем вовсе или нечего слить — не трогаем
    assert pb.orphan_foundations({"nodes": [_node("alone", 0)]}, {}, course) == {}
    assert pb.orphan_foundations(new_map, {"c2_topic": "unknown"}, course) == {}


def test_three_chapters_with_the_same_topic_make_one_branch_without_lonely_foundations():
    """Сквозной прогон через настоящий воркер (подставная модель): основы глав 2–3 вливаются в основу главы 1."""
    import subprocess
    import sys
    from pathlib import Path
    script = Path(__file__).parent / "fixtures" / "chapters_run.py"
    out = subprocess.run([sys.executable, str(script), "merge"], capture_output=True, text=True, encoding="utf-8", timeout=200,
                         cwd=Path(__file__).parents[1])
    assert out.returncode == 0, out.stderr[-800:]
    assert "связных кусков в данных: 1" in out.stdout and "Узлов в графе 11" in out.stdout, out.stdout[-1500:]


# --- PDF: без молчаливой обрезки и не в цикле событий -----------------------------------------------------------------------------------

def _blank_pdf(pages: int) -> bytes:
    import pypdf
    w = pypdf.PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_a_pdf_over_the_page_limit_is_refused_instead_of_cut():
    from app.api.endpoints import imports
    assert imports._extract_pdf_text(_blank_pdf(3), "a.pdf") == ("", 3)
    with patch.object(imports, "MAX_PDF_PAGES", 5):
        with pytest.raises(HTTPException) as e:
            imports._extract_pdf_text(_blank_pdf(6), "big.pdf")
    assert e.value.status_code == 413 and "6 страниц" in e.value.detail and "big.pdf" in e.value.detail


# --- «готово» не скрывает потери --------------------------------------------------------------------------------------------------

def test_the_ready_message_warns_about_empty_topics_and_the_course_ceiling():
    from sqlalchemy import delete
    from app.database.models import GenerationJob
    from app.database.session import AsyncSessionLocal
    from app.services.generation_worker import process_generation_job
    from app.services.knowledge_path import wipe_subject
    from test_knowledge_path import fake_result
    sent = []

    async def fake_build(text, subject, calls=None, course=None, **options):
        res = fake_result()
        res["missing_nodes"] = ["a", "b"]
        res["stats"] = {"deck_policy": {"limited_by": "course"}}
        return res

    async def scenario():
        user, subject = "test_slicing_fixes_user", "test_slicing_fixes_subject"
        async with AsyncSessionLocal() as db:
            job = GenerationJob(user_id=user, telegram_id=user, subject=subject, theme="t", raw_text="Текст материала " * 10,
                                status="processing", source_name="Материал")
            db.add(job)
            await db.commit()
            job_id = job.id
        try:
            with patch("app.services.generation_worker.build_learning_path", side_effect=fake_build), \
                    patch("app.services.generation_worker.send_worker_telegram_push", new=AsyncMock(side_effect=lambda *a, **k: sent.append(a[1]))):
                await process_generation_job(job_id, is_offpeak=True)
        finally:
            async with AsyncSessionLocal() as db:
                await wipe_subject(db, user, subject)
                await db.execute(delete(GenerationJob).where(GenerationJob.user_id == user))
                await db.commit()

    asyncio.run(scenario())
    done = [m for m in sent if "готов" in m]
    assert done and "2 тем остались без карточек" in done[-1] and "потолок карточек предмета" in done[-1]
