#!/usr/bin/env python3
"""
Сквозная проверка всей системы на ограниченный бюджет (по умолчанию $1.00 на всё, что осталось на балансе DeepSeek).

Один прогон проходит весь путь пользователя на ОТДЕЛЬНОЙ временной базе (ваши данные не затрагиваются):
  1. шпаргалка (конспект)               → тип «notes», карточек по пунктам;
  2. короткий доклад                     → тип «article», маленький граф;
  3. часть учебника (первые 148 стр.)    → тип «textbook», граф, цена;
  4. «повторения» по части учебника      → имитация учёбы, чтобы проверить, что они переживут добавление;
  5. весь учебник (в него входит часть)  → слияние тем, пропуск уже покрытого, повторения целы, цена целой книги;
  6. билеты по учебнику (+ посторонние)  → сопоставление билетов с темами, «не найдено» для чужих вопросов;
  7. удаление второго материала          → первый цел.

Защита кошелька: каждый платный вызов проходит через счётчик (файл .e2e_ledger.json), при исчерпании остатка вызовы
останавливаются, шаги пропускаются по прогнозу цены. Учёт — по токенам и ценам из настроек (оценка, а не счёт провайдера),
поэтому оставлен запас (--reserve, 12%).

    python scripts/e2e_check.py --plan            # ничего не тратит: что и сколько будет стоить
    python scripts/e2e_check.py --fake            # репетиция на подставной модели (бесплатно): проверяет всю «проводку»
    python scripts/e2e_check.py --yes             # реальный прогон (платно, не более остатка кошелька)
    python scripts/e2e_check.py --yes --steps notes,article,part   # только часть шагов
"""
import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import zipfile
import html as htmllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

STEPS = ["notes", "article", "gold", "part", "progress", "whole", "exam", "delete", "cold"]
DEFAULT_STEPS = [s_ for s_ in STEPS if s_ != "cold"]       # «cold» — отдельная дорогая проверка (~18¢), по умолчанию не входит
PAID_STEPS = {"notes", "article", "gold", "part", "whole", "exam", "cold"}
USER = "e2e_user"
SUBJECTS = {"notes": "e2e_history", "article": "e2e_psychology", "gold": "e2e_law_ch23", "part": "e2e_sudoustr", "whole": "e2e_sudoustr",
            "cold": "e2e_cold"}
DEFAULT_SOURCES = {
    "notes": "C:/Users/Morpheas/Desktop/шпора на истоиря.txt",
    "article": "C:/Users/Morpheas/Downloads/Doklad_Fromm_kratko.docx",
    "gold": "C:/Users/Morpheas/AppData/Local/Temp/claude/C--Users-Morpheas-GRINDER/4c0486a3-b140-4fb4-bfd1-592b777edbb9/scratchpad/chunk_ch23.txt",
    "part": "C:/Users/Morpheas/AppData/Local/Temp/claude/C--Users-Morpheas-GRINDER/4c0486a3-b140-4fb4-bfd1-592b777edbb9/scratchpad/book_bibilo1.txt",
    "whole": "C:/Users/Morpheas/AppData/Local/Temp/claude/C--Users-Morpheas-GRINDER/3fc79a9a-05d6-4d3e-942d-1c3e3fdbbf7c/scratchpad/book_sudoustr.txt",
}
OFF_TOPIC = ["Какова роль налоговой системы в рыночной экономике?", "Назовите основные законы термодинамики.",
             "Чем отличается митоз от мейоза?", "Как работает алгоритм быстрой сортировки?"]


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="только план и прогноз цены (по умолчанию)")
    mode.add_argument("--fake", action="store_true", help="репетиция на подставной модели, бесплатно")
    mode.add_argument("--yes", action="store_true", help="реальный прогон: платные вызовы DeepSeek")
    ap.add_argument("--wallet", type=float, default=1.00, help="сколько всего долларов на балансе (по умолчанию 1.00)")
    ap.add_argument("--reserve", type=float, default=0.12, help="доля кошелька, которую не трогаем (запас на неточность учёта)")
    ap.add_argument("--steps", default=",".join(DEFAULT_STEPS), help="какие шаги выполнять, через запятую (cold — первая книга курса «с нуля», ~18¢)")
    for k, v in DEFAULT_SOURCES.items():
        ap.add_argument(f"--{k}-file", default=v, help=f"материал для шага «{k}»")
    ap.add_argument("--ledger", default=str(ROOT / ".e2e_ledger.json"), help="файл учёта потраченного (между запусками)")
    ap.add_argument("--out", default=str(ROOT / "e2e_out"), help="куда складывать результаты шагов")
    ap.add_argument("--wait-offpeak", action="store_true", help="если сейчас пик DeepSeek, дождаться окна скидки 50% и только потом начать")
    ap.add_argument("--allow-peak", action="store_true", help="разрешить платные вызовы в пик (дороже вдвое); по умолчанию в пик скрипт не тратит ничего")
    return ap.parse_args()


ARGS = parse_args()
TMP = tempfile.mkdtemp(prefix="grinder_e2e_")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{Path(TMP, 'e2e.db').as_posix()}"      # до импорта приложения: своя база
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("PATH_DEBUG_RAW_DIR", str(ROOT / "e2e_out" / "raw"))          # сырые ответы, которые не удалось разобрать

from app.database.session import AsyncSessionLocal, Base, engine  # noqa: E402
import app.database.models as models  # noqa: E402,F401  (регистрирует таблицы)
from sqlalchemy import func, select  # noqa: E402
from app.database.models import (  # noqa: E402
    AiTelemetryLog, Card, ExamTicket, GenerationJob, KnowledgeEdge, KnowledgeNode, NodeProgress, ReviewLog, Source, utc_now,
)
from app.services import generation_worker as gw  # noqa: E402
from app.services.ai_gateway import client as dsclient  # noqa: E402
from app.services.ai_gateway.client import format_offpeak_start_msk, is_deepseek_offpeak_now, next_offpeak_start  # noqa: E402
from app.services.ai_gateway import coverage, source_profile  # noqa: E402
from app.services.ai_gateway.budget import Budget  # noqa: E402
from app.services.knowledge_path import complete_lesson, delete_source, list_sources, text_fingerprint  # noqa: E402


# ---------------------------------------------------------------------------------------------------------------- кошелёк

from spend_wallet import Wallet, WalletExhausted  # noqa: E402


WALLET = Wallet(ARGS.ledger, ARGS.wallet, ARGS.reserve)


def install_real_model() -> None:
    real = dsclient.call_deepseek

    async def guarded(user_prompt, system_instruction, **kwargs):
        WALLET.allow()
        try:
            res, meta = await real(user_prompt, system_instruction, **kwargs)
        except dsclient.LLMCallError as e:                      # ответ оплачен, даже если непригоден
            WALLET.charge((e.meta or {}).get("cost_usd", 0.0), "ошибка разбора")
            raise
        WALLET.charge(meta.get("cost_usd", 0.0), (re.search(r"TYPE: (\w+)", user_prompt) or [None, "?"])[1])
        return res, meta

    dsclient.call_deepseek = guarded


def fake_map_for(text: str) -> dict:
    """Подставная карта нужного размера: ярусы и подтемы по размеру текста; в описании подтем — слова из своего куска текста,
    чтобы разбиение на куски и слияние тем отрабатывали на настоящем тексте."""
    budget = source_profile.node_budget(source_profile.target_cards(source_profile.heuristic_kind(text), text))
    n2 = budget["tier2"]
    paras = [re.sub(r"\s+", " ", p_).strip() for p_ in re.split(r"\n", text) if len(p_.strip()) > 60] or [text[:400]]
    nodes = [{"key": f"base{i}", "name": f"Основа {i}", "tier": 0, "order": i, "summary": "Основа курса", "src": "Гл. 1"}
             for i in range(1, budget["tier0"] + 1)]
    for i in range(1, budget["tier1"] + 1):
        nodes.append({"key": f"topic{i}", "name": f"Тема {i}", "tier": 1, "order": 10 + i, "summary": "Тема курса", "src": f"Гл. {i}",
                      "prereqs": [f"base{1 + i % budget['tier0']}"]})
    for j in range(n2):
        sample = " ".join(paras[(j * len(paras)) // n2].split()[:14])
        nodes.append({"key": f"sub{j}", "name": f"Подтема {j + 1}", "tier": 2, "parent": f"topic{1 + j % budget['tier1']}", "order": 30 + j,
                      "summary": sample, "src": f"Гл. {1 + j % budget['tier1']}"})
    for k in range(1, budget["tier3"] + 1):
        nodes.append({"key": f"case{k}", "name": f"Кейс {k}", "tier": 3, "order": 200 + k, "summary": "Ситуация", "src": ""})
    edges = [{"from": f"topic{i}", "to": f"topic{i % budget['tier1'] + 1}", "relation": "leads_to", "label": "ведёт к"}
             for i in range(1, budget["tier1"] + 1)]
    edges += [{"from": f"base{i}", "to": f"topic{min(i, budget['tier1'])}", "relation": "leads_to", "label": "ведёт к"}
              for i in range(1, budget["tier0"] + 1)]
    return {"title": "Подставной курс", "domain": "law", "source_type": source_profile.heuristic_kind(text), "nodes": nodes, "edges": edges}


def install_fake_model() -> None:
    from llm_fake import FakeLLM

    def align(prompt, fake):
        """Репетиция слияния: у узлов основ и тем берём первого кандидата."""
        out = []
        for m in re.finditer(r'^(\w+) \| "[^"]*" \(tier (\d)\).*?-> candidates: (\w+) "', prompt, re.M):
            if int(m.group(2)) <= 1:
                out.append({"new": m.group(1), "same": m.group(3)})
        return {"align": out}

    def facts(prompt, fake):
        """Репетиция конспекта: по K первых содержательных предложений каждого блока книги."""
        src = prompt.split("[SOURCE MATERIAL — FULL TEXT]\n", 1)[-1].split("\n[END OF SOURCE MATERIAL]", 1)[0]
        spans = coverage.fact_blocks(src)
        out = []
        for m in re.finditer(r"^B(\d+) \| K=(\d+)", prompt, re.M):
            i, k = int(m.group(1)) - 1, int(m.group(2))
            a, b = spans[i]
            sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", src[a:b])) if 40 <= len(x.strip()) <= 220]
            out.append({"id": f"B{i + 1}", "facts": sents[:k]})
        return {"blocks": out}

    fake = FakeLLM(align=align, facts=facts)

    async def call(user_prompt, **kwargs):
        if "[TICKETS]" in user_prompt:                          # разбор билетов: прежние подставные ответы тестов exam
            keys = re.findall(r"^@(\w+) \|", user_prompt, re.M)
            tickets = []
            for m in re.finditer(r"^(\d+)\. (.+)$", user_prompt.split("[TICKETS]", 1)[1], re.M):
                off = any(q[:12] in m.group(2) for q in OFF_TOPIC)
                tickets.append({"i": int(m.group(1)), "nodes": [] if off else keys[:2], "found": not off,
                                "points": [] if off else [{"t": "Тезис ответа", "v": ["тезис"], "w": 1}]})
            return {"tickets": tickets}, {"cost_usd": 0.0, "cache_hit_tokens": 0, "completion_tokens": 10, "prompt_tokens": 100}
        if "TYPE: MAP" in user_prompt:
            src = user_prompt.split("[SOURCE MATERIAL — FULL TEXT]\n", 1)[-1].split("\n[END OF SOURCE MATERIAL]", 1)[0]
            fake.raw_map = fake_map_for(src)
        return await fake(user_prompt, **kwargs)

    dsclient.call_deepseek = call


# ---------------------------------------------------------------------------------------------------------------- материалы

def load_text(path: str) -> str:
    p = Path(path)
    if p.suffix.lower() == ".docx":
        xml = zipfile.ZipFile(p).read("word/document.xml").decode("utf-8")
        return htmllib.unescape(re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", xml)))
    if p.suffix.lower() == ".pdf":
        import pypdf
        return "\n".join((pg.extract_text() or "") for pg in pypdf.PdfReader(str(p)).pages)
    return p.read_text(encoding="utf-8", errors="replace")


def estimate(step: str, text: str) -> tuple[str, int, float]:
    kind = source_profile.heuristic_kind(text)
    target = source_profile.target_cards(kind, text)
    return kind, target, Budget.estimate_book_cost(len(text), target)


# ---------------------------------------------------------------------------------------------------------------- шаги

REPORT: list[dict] = []
RESULTS: dict[str, dict] = {}


def check(step: str, name: str, ok: bool, detail: str = "", content: bool = False, soft: bool = False) -> bool:
    """content=True — проверка качества содержимого: на подставной модели (репетиция) не имеет смысла и не считается сбоем."""
    if content and ARGS.fake:
        print(f"   [скип] {name}{(' — ' + detail) if detail else ''} (репетиция)")
        return True
    mark = "OK  " if ok else "СБОЙ"
    print(f"   [{mark}] {name}{(' — ' + detail) if detail else ''}")
    REPORT.append({"step": step, "check": name, "ok": bool(ok), "detail": detail, "soft": soft})
    return ok


def capture_builder():
    """Результат конвейера (статистика, слияние) виден только воркеру; перехватываем его, не меняя поведения."""
    real = gw.build_learning_path

    async def wrapper(text, subject, calls=None, **kw):
        res = await real(text, subject, calls=calls, **kw)
        RESULTS["last"] = res
        return res

    gw.build_learning_path = wrapper


async def dump_deck(step: str, subject: str, result: dict | None) -> None:
    """Колода шага читаемым текстом: для разбора глазами (карточки по узлам) и пары слитых тем."""
    out = Path(ARGS.out)
    out.mkdir(parents=True, exist_ok=True)
    async with AsyncSessionLocal() as db:
        nodes = {n.id: n for n in (await db.execute(select(KnowledgeNode).where(KnowledgeNode.user_id == USER, KnowledgeNode.subject == subject))).scalars().all()}
        rows = (await db.execute(select(Card).where(Card.user_id == USER, Card.subject == subject, Card.node_id.isnot(None))
                                 .order_by(Card.topological_rank))).scalars().all()
    names = {n.node_key: n.name for n in nodes.values()}
    lines = [f"# {step}: узлов {len([n for n in nodes.values() if n.node_key != '__intro__'])}, карточек {len(rows)}"]
    if result and result.get("merge"):
        new_names = {n["key"]: n["name"] for n in result["map"]["nodes"]}
        lines.append("## Слитые темы (новый материал → уже имевшаяся тема)")
        lines += [f"  «{new_names.get(k, k)}»  →  «{names.get(v, v)}»" for k, v in result["merge"].items()]
    lines.append("## Карточки")
    for c in rows:
        lines.append(f"[{nodes[c.node_id].name}] {c.text}  =>  {c.translation}")
    lines.append("## Конспект темы (факты)")
    for n in sorted(nodes.values(), key=lambda x: x.order_idx):
        for f in n.facts or []:
            lines.append(f"[{n.name}] {f['t'] if isinstance(f, dict) else f}")
    (out / f"deck_{step}.txt").write_text("\n".join(lines), encoding="utf-8")


async def upload(step: str, text: str, name: str) -> dict:
    subject = SUBJECTS[step]
    async with AsyncSessionLocal() as db:
        job = GenerationJob(user_id=USER, subject=subject, theme=name, raw_text=text, status="processing", source_name=name,
                            text_hash=text_fingerprint(text))
        db.add(job)
        await db.commit()
        job_id = job.id
    t0 = time.time()
    await gw.process_generation_job(job_id, is_offpeak=True)
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(GenerationJob).where(GenerationJob.id == job_id))).scalar_one()
        status, err = job.status, job.error_message
        cost = (await db.execute(select(func.coalesce(func.sum(AiTelemetryLog.cost_usd), 0.0)).where(AiTelemetryLog.job_id == str(job_id)))).scalar()
        sources = await list_sources(db, USER, subject)
        nodes = (await db.execute(select(KnowledgeNode).where(KnowledgeNode.user_id == USER, KnowledgeNode.subject == subject))).scalars().all()
        edges = (await db.execute(select(func.count(KnowledgeEdge.id)).where(KnowledgeEdge.user_id == USER, KnowledgeEdge.subject == subject))).scalar()
        cards = (await db.execute(select(Card).where(Card.user_id == USER, Card.subject == subject, Card.node_id.isnot(None)))).scalars().all()
    real_nodes = [n for n in nodes if n.node_key != "__intro__"]
    await dump_deck(step, subject, RESULTS.get("last"))
    return {"job_id": job_id, "status": status, "error": err, "seconds": round(time.time() - t0), "cost": float(cost or 0), "sources": sources,
            "nodes": len(real_nodes), "lessons_ready": sum(1 for n in real_nodes if n.lesson_status == "ready"), "edges": edges, "cards": len(cards),
            "with_distractors": sum(1 for c in cards if c.distractors), "result": RESULTS.pop("last", None)}


async def step_generic(step: str, text: str, name: str, expect_kind: str | None) -> None:
    kind, target, est = estimate(step, text)
    print(f"\n== Шаг «{step}»: {name}: {len(text)} знаков; тип по тексту {kind}; цель ~{target} карточек; прогноз ${est:.3f}")
    if ARGS.yes and est > WALLET.room:
        print(f"   ПРОПУЩЕН: прогноз ${est:.3f} больше остатка кошелька ${WALLET.room:.3f}")
        check(step, "хватает денег на шаг", False, f"остаток ${WALLET.room:.3f}")
        return
    WALLET.begin(step, max(0.03, est * 1.6))
    r = await upload(step, text, name)
    RESULTS[step] = r
    stats = (r["result"] or {}).get("stats", {})
    src = stats.get("source", {})
    print(f"   {r['seconds']} с, ${r['cost']:.4f}; узлов {r['nodes']}, связей {r['edges']}, карточек {r['cards']}, тип {src.get('kind')}, "
          f"цель {src.get('target')}, фактов конспекта {(stats.get('facts') or {}).get('kept')}")
    check(step, "материал обработан", r["status"] == "completed", r["error"] or "")
    if r["status"] != "completed":
        return
    check(step, "узлов достаточно для графа", r["nodes"] >= 3, str(r["nodes"]))
    if expect_kind in ("textbook", "article"):
        fs = stats.get("facts") or {}
        check(step, "конспект темы собран (факты по блокам книги)", (fs.get("kept") or 0) >= 0.4 * (fs.get("asked") or 1),
              f"оставлено {fs.get('kept')} из {fs.get('asked')} запрошенных; отброшено: нет опоры {fs.get('unsupported')}, повтор карточки {fs.get('repeat_card')}")
    check(step, "уроки есть почти у всех узлов", r["lessons_ready"] >= 0.8 * r["nodes"], f"{r['lessons_ready']} из {r['nodes']}", content=True)
    check(step, "у карточек есть неверные варианты", r["with_distractors"] >= 0.9 * max(1, r["cards"]), f"{r['with_distractors']} из {r['cards']}", content=True)
    if expect_kind and ARGS.yes:
        check(step, f"тип материала «{expect_kind}»", src.get("kind") == expect_kind, str(src.get("kind")))
    if ARGS.yes and src.get("target"):
        check(step, "число карточек около цели (±45%)", 0.55 * src["target"] <= r["cards"] <= 1.45 * src["target"] + 3,
              f"{r['cards']} при цели {src['target']}")
    support = stats.get("support", {})
    good = (support.get("grounded", 0) + support.get("audited", 0) + support.get("fixed", 0) + support.get("near", 0))
    new_cards = sum(len(p_["cards"]) for p_ in (r["result"] or {}).get("packs", {}).values())      # карточки ЭТОГО прогона, а не всего предмета
    if ARGS.yes:
        check(step, "цитаты найдены в тексте (≥80% новых карточек)", good >= 0.8 * max(1, new_cards), f"{good} из {new_cards}")
        per_node = new_cards / max(1, len((r["result"] or {}).get("packs", {})))
        check(step, "в узле в среднем ≥ 2 карточки (колода не плоская)", per_node >= 2.0 or new_cards < 12, f"{per_node:.1f} на узел")
        q = (stats.get("quality") or {})
        pol = (stats.get("deck_policy") or {})
        print(f"   качество: подтверждено книгой {q.get('supported_share')}, на понимание {q.get('understanding_share')}, с числом {q.get('numeric_share')}, "
              f"повторов {q.get('duplicate_fronts')}, отбраковано {q.get('dropped_unsupported')}, обрезано {q.get('trimmed_by_priority')}; "
              f"политика: цель {pol.get('goal')} из {pol.get('planned')}, ограничение {pol.get('limited_by')}, в курсе было {pol.get('existing_cards')}")
        check(step, "качество: ≥ 85% карточек подтверждены книгой", (q.get("supported_share") or 0) >= 0.85, str(q.get("supported_share")), content=True)
    check(step, "цена в пределах прогноза ×1.6", r["cost"] <= max(0.03, est * 1.6), f"${r['cost']:.4f}", content=True)


def gold_check(step: str, gold_file: str, label: str, was: str) -> None:
    """Покрытие ручного эталона колодой шага: карточки, конспект темы, уроки. Мягкая проверка: порог 65% (цель пользователя 65–70%)."""
    from gold_recall import evaluate, load_gold, norm
    r = RESULTS.get(step)
    if not r or r["status"] != "completed" or not r["result"]:
        return
    facts = load_gold(ROOT / "tests" / "fixtures" / "bench" / gold_file)
    packs = r["result"]["packs"]
    cards = [norm(f"{c['text']} {c['translation']}") for p_ in packs.values() for c in p_["cards"]]
    screens = [norm(sc.get("say", "")) for p_ in packs.values() if p_.get("lesson") for sc in p_["lesson"].get("screens", [])]
    sheet = [norm(f) for fs in (r["result"].get("facts") or {}).values() for f in fs]
    ev = evaluate(cards, screens, facts, sheet)
    n = max(1, ev["facts"])
    print(f"   эталон «{label}»: фактов {ev['facts']}; карточка {ev['cards']} ({100 * ev['cards'] / n:.0f}%), конспект {ev['sheet']} ({100 * ev['sheet'] / n:.0f}%), "
          f"урок {ev['lessons']} ({100 * ev['lessons'] / n:.0f}%); хоть где-то {ev['any']} ({100 * ev['any'] / n:.0f}%); карточек {len(cards)}, фактов {len(sheet)} ({was})")
    check(step, f"покрытие эталона «{label}» (карточка, конспект или урок) ≥ 65%", ev["any"] >= 0.65 * n, f"{100 * ev['any'] / n:.0f}%", content=True, soft=True)
    RESULTS[step]["recall"] = {"cards": ev["cards"], "sheet": ev["sheet"], "any": ev["any"], "facts": ev["facts"], "deck": len(cards)}


async def step_gold(text: str) -> None:
    """Главы 2–3 «Общей теории права» с целью по типу материала: покрытие ручного списка из 86 фактов (раньше 90% на 207 карточках)."""
    await step_generic("gold", text, "Общая теория права, гл. 2–3", "textbook")
    gold_check("gold", "obshteorprava_ch2-3_gold.txt", "гл. 2–3", "раньше: 90% / 93% при 207 карточках")


async def step_part(text: str) -> None:
    """Первые 148 страниц «Судоустройства»: 102 факта из 24 случайных отрывков (эталон составлен до построения конспекта)."""
    await step_generic("part", text, "Судоустройство, часть 1 (148 стр.)", "textbook")
    gold_check("part", "sudoustr_part_gold.txt", "148 стр.", "узловой конспект давал 45%")


async def step_cold(text: str) -> None:
    """Учебник целиком КАК ПЕРВАЯ книга курса и с холодным кешем (в начале текста уникальная строка: префикс не найдётся в кеше DeepSeek).
    Это цена для нового пользователя: без прогретого кеша прежних запусков и без слияния с имеющимся курсом."""
    import uuid
    cold = f"[холодный прогон {uuid.uuid4().hex}]\n{text}"
    await step_generic("cold", cold, "Судоустройство целиком, первая книга курса (холодное чтение)", "textbook")
    r = RESULTS.get("cold")
    if r and r["status"] == "completed":
        check("cold", "цена первой книги с нуля в пределах потолка", r["cost"] <= Budget([], offpeak=True).limit, f"${r['cost']:.4f}", content=True)


async def step_progress() -> None:
    print("\n== Шаг «progress»: имитация учёбы по части учебника")
    subject = SUBJECTS["part"]
    async with AsyncSessionLocal() as db:
        nodes = (await db.execute(select(KnowledgeNode).where(KnowledgeNode.user_id == USER, KnowledgeNode.subject == subject,
                                                              KnowledgeNode.node_key != "__intro__").order_by(KnowledgeNode.order_idx).limit(4))).scalars().all()
        for n in nodes:
            await complete_lesson(db, USER, n.id, 1)
        cards = (await db.execute(select(Card).where(Card.user_id == USER, Card.subject == subject, Card.node_id.in_([n.id for n in nodes])))).scalars().all()
        for c in cards[:25]:
            db.add(ReviewLog(card_id=c.id, user_id=USER, rating=3, review_time=utc_now(), state=2))
        await db.commit()
        RESULTS["progress"] = {"reviews": min(25, len(cards)), "card_ids": [c.id for c in cards[:25]], "lessons_done": len(nodes)}
    print(f"   пройдено уроков {len(nodes)}, ответов {RESULTS['progress']['reviews']}")


def normalized(s: str) -> str:
    return " ".join(sorted(set(coverage.stems(s))))


async def step_whole(text: str, name: str) -> None:
    await step_generic("whole", text, name, "textbook")
    r = RESULTS.get("whole")
    if not r or r["status"] != "completed":
        return
    stats = (r["result"] or {}).get("stats", {})
    src = stats.get("source", {})
    prog = RESULTS.get("progress", {})
    async with AsyncSessionLocal() as db:
        sources = await list_sources(db, USER, SUBJECTS["whole"])
        kept = (await db.execute(select(func.count(ReviewLog.id)).where(ReviewLog.card_id.in_(prog.get("card_ids") or [0])))).scalar()
        done = (await db.execute(select(func.count(NodeProgress.id)).where(NodeProgress.user_id == USER, NodeProgress.lesson_done == True))).scalar()  # noqa: E712
        rows = (await db.execute(select(Card.source_id, Card.text, Card.translation).where(Card.user_id == USER, Card.subject == SUBJECTS["whole"],
                                                                                      Card.node_id.isnot(None)))).all()
    check("whole", "в предмете два материала", len(sources) == 2, str([s["name"] for s in sources]))
    check("whole", "цена целой книги в пределах потолка", r["cost"] <= Budget([], offpeak=True).limit, f"${r['cost']:.4f}", content=True)
    check("whole", "повторения первого материала целы", kept == prog.get("reviews"), f"{kept} из {prog.get('reviews')}")
    check("whole", "пройденные уроки целы", done >= prog.get("lessons_done", 0), str(done))
    if ARGS.yes:
        check("whole", "темы совпавшие с имеющимися слиты", src.get("merged_nodes", 0) >= 3, f"слито {src.get('merged_nodes')}")
        check("whole", "покрытые части учтены", src.get("covered_windows", 0) >= 0.15 * max(1, src.get("windows", 1)),
              f"{src.get('covered_windows')} из {src.get('windows')} окон")
    old = [r_ for r_ in rows if r_[0] == sources[0]["id"]] if len(sources) == 2 else []
    new = [r_ for r_ in rows if len(sources) == 2 and r_[0] == sources[1]["id"]]
    old_fronts = {normalized(q) for _, q, _ in old}
    dup = sum(1 for _, q, _ in new if normalized(q) in old_fronts)
    check("whole", "новые карточки не повторяют старые вопросы (<10%)", dup <= 0.1 * max(1, len(new)), f"{dup} из {len(new)}", content=True)


def tickets_from(text: str) -> list[str]:
    sections = coverage.extract_sections(text)
    titles = [re.sub(r"\s+", " ", s["title"]).strip() for s in sections if 20 <= len(s["title"]) <= 110]
    if len(titles) < 10:                                                   # книга без нумерованных заголовков: берём первые строки абзацев
        titles = [re.sub(r"\s+", " ", ln).strip()[:90] for ln in text.split("\n") if 40 <= len(ln.strip()) <= 120][::40]
    step = max(1, len(titles) // 26)
    picked = titles[::step][:26]
    return [f"Раскройте вопрос: {t}" for t in picked] + OFF_TOPIC


async def step_exam(text: str) -> None:
    from app.services.exam_prep import create_plan, run_plan_matching
    print("\n== Шаг «exam»: билеты по учебнику (+ посторонние вопросы)")
    questions = tickets_from(text)
    est = 0.0025 * len(questions) / 10 + 0.004
    print(f"   билетов {len(questions)}; прогноз ~${est:.3f}")
    if ARGS.yes and est > WALLET.room:
        check("exam", "хватает денег на шаг", False, f"остаток ${WALLET.room:.3f}")
        return
    WALLET.begin("exam", max(0.06, est * 3))
    subject = SUBJECTS["whole"]
    async with AsyncSessionLocal() as db:
        plan = await create_plan(db, USER, subject, "Билеты E2E", date.today() + timedelta(days=30), [{"question": q} for q in questions])
        plan_id = plan.id
    t0 = time.time()
    await run_plan_matching(plan_id)
    async with AsyncSessionLocal() as db:
        from app.database.models import ExamPlan
        plan = (await db.execute(select(ExamPlan).where(ExamPlan.id == plan_id))).scalar_one()
        tickets = (await db.execute(select(ExamTicket).where(ExamTicket.plan_id == plan_id).order_by(ExamTicket.order_idx))).scalars().all()
    ok = [t for t in tickets if t.status == "ok"]
    missing = [t for t in tickets if t.status == "missing"]
    off_found = [t for t in tickets if t.question in OFF_TOPIC and t.status == "ok"]
    print(f"   {round(time.time() - t0)} с, ${plan.cost_usd:.4f}; найдено в курсе {len(ok)}, не найдено {len(missing)}")
    check("exam", "разбор билетов завершён", plan.status == "ready", plan.error or "")
    if ARGS.yes:
        check("exam", "настоящие билеты в основном найдены (≥60%)", len(ok) >= 0.6 * (len(tickets) - len(OFF_TOPIC)), f"{len(ok)} из {len(tickets) - len(OFF_TOPIC)}", content=True)
        check("exam", "посторонние вопросы помечены «не найдено» (≥3 из 4)", len(off_found) <= 1, f"ложно найдено {len(off_found)}", content=True)
        check("exam", "цена разбора ≤ 3 центов", plan.cost_usd <= 0.03, f"${plan.cost_usd:.4f}", content=True)
    RESULTS["exam"] = {"tickets": len(tickets), "found": len(ok), "missing": len(missing), "cost": plan.cost_usd}


async def step_delete() -> None:
    print("\n== Шаг «delete»: удаление второго материала")
    subject = SUBJECTS["whole"]
    prog = RESULTS.get("progress", {})
    async with AsyncSessionLocal() as db:
        sources = await list_sources(db, USER, subject)
        if len(sources) != 2:
            check("delete", "есть два материала для проверки", False, str(len(sources)))
            return
        removed = await delete_source(db, USER, subject, sources[1]["id"])
        await db.commit()
        left = await list_sources(db, USER, subject)
        kept = (await db.execute(select(func.count(ReviewLog.id)).where(ReviewLog.card_id.in_(prog.get("card_ids") or [0])))).scalar()
    check("delete", "остался один материал", len(left) == 1 and left[0]["id"] == sources[0]["id"], str(removed))
    check("delete", "повторения первого материала целы", kept == prog.get("reviews"), f"{kept} из {prog.get('reviews')}")


# ---------------------------------------------------------------------------------------------------------------- запуск

async def main() -> int:
    wanted = [s.strip() for s in ARGS.steps.split(",") if s.strip() in STEPS]
    texts = {k: load_text(getattr(ARGS, f"{k}_file")) for k in DEFAULT_SOURCES if k in wanted or k in ("whole", "part")}
    mode = "РЕАЛЬНЫЙ (платный)" if ARGS.yes else ("репетиция на подставной модели" if ARGS.fake else "только план")
    print(f"Режим: {mode}. Кошелёк ${ARGS.wallet:.2f}, запас {ARGS.reserve:.0%}, уже потрачено ${WALLET.spent:.4f}, доступно ${WALLET.room:.4f}")
    total = 0.0
    print("\nПлан шагов и прогноз цены:")
    for k in ("notes", "article", "gold", "part", "whole", "cold"):
        if k in wanted:
            kind, target, est = estimate(k, texts["whole" if k == "cold" else k])
            total += est
            print(f"  {k:8} {len(texts['whole' if k == 'cold' else k]):8} знаков, тип по тексту {kind:9} цель ~{target:4} карточек, ~${est:.3f}")
    if "exam" in wanted:
        total += 0.01
        print("  exam     ~30 билетов                                          ~$0.010")
    print(f"  ИТОГО прогноз ~${total:.3f}; остаток после ~${WALLET.room - total:.3f}")
    if not (ARGS.yes or ARGS.fake):
        print("\nПлан без запуска. --fake — репетиция без трат, --yes — реальный прогон.")
        return 0
    if ARGS.yes and total > WALLET.room:
        print("Прогноз больше остатка кошелька: уберите шаги через --steps.")
        return 2
    if ARGS.yes and not is_deepseek_offpeak_now() and not ARGS.allow_peak:
        if not ARGS.wait_offpeak:
            print(f"\nСейчас пик DeepSeek (цены вдвое выше): скидка начнётся в {format_offpeak_start_msk()}. Ничего не потрачено.\n"
                  "Запустите позже или добавьте --wait-offpeak (скрипт сам дождётся скидки), либо --allow-peak, если пик не важен.")
            return 3
        wait = (next_offpeak_start() - datetime.now(timezone.utc)).total_seconds()
        print(f"\nСейчас пик DeepSeek: жду скидки до {format_offpeak_start_msk()} (~{max(0, int(wait // 60))} мин), потом начну.", flush=True)
        while not is_deepseek_offpeak_now():
            await asyncio.sleep(30)
        print("Скидка началась, запускаю.", flush=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    (install_real_model if ARGS.yes else install_fake_model)()
    capture_builder()

    for step in wanted:
        try:
            if ARGS.yes and step == "whole":
                # дорогой шаг (~17¢) идёт только если дешёвые (≈12¢) прошли без сбоев: иначе деньги пропали бы на заведомо плохой результат
                early_bad = [r for r in REPORT if not r["ok"] and not r.get("soft") and r["step"] in ("notes", "article", "gold", "part")]
                if early_bad:
                    print(f"\n   ШАГ «whole» НЕ ЗАПУЩЕН: в дешёвых шагах есть сбои ({len(early_bad)}), деньги сохранены: " +
                          "; ".join(f"[{r['step']}] {r['check']}" for r in early_bad))
                    break
            if ARGS.yes and step in PAID_STEPS and not ARGS.allow_peak and not is_deepseek_offpeak_now():
                # длинный прогон мог дойти до пика: между шагами останавливаемся, чтобы не платить вдвое
                raise WalletExhausted(f"начался пик DeepSeek, шаг «{step}» не запущен (скидка с {format_offpeak_start_msk()})")
            if step == "notes":
                await step_generic("notes", texts["notes"], "Шпаргалка по истории", "notes")
            elif step == "article":
                await step_generic("article", texts["article"], "Доклад: Эрих Фромм", "article")
            elif step == "gold":
                await step_gold(texts["gold"])
            elif step == "part":
                await step_part(texts["part"])
            elif step == "progress":
                await step_progress()
            elif step == "whole":
                await step_whole(texts["whole"], "Судоустройство, учебник целиком")
            elif step == "exam":
                await step_exam(texts["whole"])
            elif step == "delete":
                await step_delete()
            elif step == "cold":
                await step_cold(texts["whole"])
        except WalletExhausted as e:
            print(f"   ОСТАНОВЛЕНО: {e}")
            check(step, "кошелёк не исчерпан", False, str(e))
            break

    bad = [r for r in REPORT if not r["ok"]]
    out = Path(ARGS.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({"checks": REPORT, "wallet_spent": WALLET.spent,
                                                 "steps": {k: {kk: vv for kk, vv in v.items() if kk != "result"} for k, v in RESULTS.items() if isinstance(v, dict)}},
                                                ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\n== Итог: проверок {len(REPORT)}, сбоев {len(bad)}; потрачено всего ${WALLET.spent:.4f} из ${ARGS.wallet:.2f}")
    for r in bad:
        print(f"   СБОЙ [{r['step']}] {r['check']}: {r['detail']}")
    print(f"Отчёт: {out / 'report.json'}; временная база: {TMP}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
