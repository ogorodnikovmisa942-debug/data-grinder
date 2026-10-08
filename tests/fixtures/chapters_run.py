"""Поглавная загрузка через настоящий воркер (подставная модель, деньги не тратятся): что получится в курсе и в графе.
Нужен tests/test_slicing_fixes.py; можно запускать и вручную: python tests/fixtures/chapters_run.py [merge]
Сценарий «merge»: тема каждой главы называется одинаково, и модель при сопоставлении говорит «это та же тема»."""
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app.core.config import settings  # noqa: E402
settings.TESTING = True

from sqlalchemy import delete  # noqa: E402
from app.database.models import GenerationJob, AiTelemetryLog  # noqa: E402
from app.database.session import AsyncSessionLocal  # noqa: E402
from app.services.generation_worker import process_generation_job  # noqa: E402
from app.services.knowledge_path import get_path_state, wipe_subject, list_sources  # noqa: E402
from llm_fake import FakeLLM, patched  # noqa: E402

USER, SUBJECT = "chk_chapters_user", "chk_chapters_subject"
NOUNS = ["прокуратуры", "нотариата", "адвокатуры", "арбитража", "присяжных", "экспертизы", "медиации", "архива", "регистра",
         "канцелярии", "следствия", "конвоя", "пробации", "опеки", "таможни"]
FILL = "Институт подробно рассматривается в учебнике, с примерами из практики и ссылками на нормативные акты. "
MERGE = len(sys.argv) > 1 and sys.argv[1] == "merge"
ORDER = ["base", "topic", "sub_a", "sub_b", "sub_c"]


def fact(c, i):
    noun, n = NOUNS[c * 5 + i], 3 + c * 5 + i
    return {"sent": f"Срок полномочий органа {noun} составляет {n} лет", "t": f"Каков срок полномочий органа {noun}?", "d": f"{n} лет."}


def chapter_text(c):
    out = f"Глава {c + 1}\n"
    for i in range(5):
        out += f"Об институте {NOUNS[c * 5 + i]}. " + FILL * 14 + fact(c, i)["sent"] + ". " + FILL * 14 + "\n"
    return out


def chapter_map(c):
    p = f"c{c + 1}_"
    topic = "Органы и их полномочия" if MERGE else f"Тема главы {c + 1}"
    nodes = [
        {"key": p + "base", "name": f"Основы главы {c + 1}", "tier": 0, "order": 1, "summary": "Базовое понятие главы", "src": f"Гл. {c + 1}"},
        {"key": p + "topic", "name": topic, "tier": 1, "order": 2, "summary": topic, "src": f"Гл. {c + 1}"},
    ]
    for i, k in enumerate(("sub_a", "sub_b", "sub_c")):
        nodes.append({"key": p + k, "name": f"Подтема {c + 1}.{i + 1}", "tier": 2, "parent": p + "topic", "order": 3 + i,
                      "summary": f"Подтема {c + 1}.{i + 1}", "src": f"Гл. {c + 1}"})
    return {"title": "Судоустройство", "domain": "law", "source_type": "textbook", "nodes": nodes,
            "edges": [{"from": p + "sub_a", "to": p + "sub_b", "relation": "demarcated_from", "label": "разграничивается с"}]}


def cards_handler(c):
    def handler(prompt, fake):
        res = []
        for k in prompt.split("NODES TO PRODUCE: ")[1].split("\n")[0].split(", "):
            f = fact(c, ORDER.index(k.split("_", 1)[1]))
            res.append({"key": k, "cards": [{"t": f["t"], "s": "Право | Тема", "d": f["d"], "e": "", "l": "medium", "y": 1, "at": "number",
                                             "ev": f["sent"], "x": ["Три года.", "Пять лет.", "Десять лет."]}]})
        return {"nodes": res}
    return handler


async def upload(c, merge_with=None):
    async with AsyncSessionLocal() as db:
        job = GenerationJob(user_id=USER, subject=SUBJECT, theme=f"Глава {c + 1}", raw_text=chapter_text(c), status="processing",
                            source_name=f"Глава {c + 1}")
        db.add(job)
        await db.commit()
        job_id = job.id
    align = (lambda prompt, fake: {"align": [{"new": f"c{c + 1}_topic", "same": merge_with}]}) if merge_with else None
    fake = FakeLLM(raw_map=chapter_map(c), cards=cards_handler(c), align=align)
    with patched(fake):
        await process_generation_job(job_id, is_offpeak=True)
    async with AsyncSessionLocal() as db:
        job = await db.get(GenerationJob, job_id)
        return job.status, job.error_message


async def main():
    async with AsyncSessionLocal() as db:
        await wipe_subject(db, USER, SUBJECT)
        await db.execute(delete(GenerationJob).where(GenerationJob.user_id == USER))
        await db.commit()
    try:
        for c in range(3):
            status, err = await upload(c, merge_with="c1_topic" if (MERGE and c > 0) else None)
            print(f"глава {c + 1}: статус {status} {err or ''}")
        async with AsyncSessionLocal() as db:
            state = await get_path_state(db, USER, SUBJECT)
            sources = await list_sources(db, USER, SUBJECT)
        print("Материалы:", [(s["name"], s["nodes"], s["cards"]) for s in sources])
        nodes, edges = state["nodes"], state["edges"]
        print(f"Узлов в графе {len(nodes)}, связей-рёбер {len(edges)}; карточек {sum(n['cards_total'] for n in nodes)}")
        ids = {n["key"]: n for n in nodes}
        root = {k: k for k in ids}

        def find(x):
            while root[x] != x:
                root[x] = root[root[x]]
                x = root[x]
            return x

        def union(a, b):
            if a in ids and b in ids:
                root[find(a)] = find(b)
        for n in nodes:
            if n.get("parent_key"):
                union(n["key"], n["parent_key"])
            for p in n.get("prereq_keys") or []:
                union(n["key"], p)
        for e in edges:
            union(e["from"], e["to"])
        comps = defaultdict(list)
        for k in ids:
            comps[find(k)].append(ids[k]["name"])
        print(f"связных кусков в данных: {len(comps)}")
        for v in comps.values():
            print("   •", len(v), "узл.:", ", ".join(v[:3]), "…")
    finally:
        async with AsyncSessionLocal() as db:
            await wipe_subject(db, USER, SUBJECT)
            await db.execute(delete(GenerationJob).where(GenerationJob.user_id == USER))
            await db.execute(delete(AiTelemetryLog).where(AiTelemetryLog.user_id == USER))
            await db.commit()

asyncio.run(main())
