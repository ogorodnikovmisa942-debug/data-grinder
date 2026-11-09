"""Восстановление полей нарезки ('s', 'e', 'm'), предпочтения мнемоник, языковой/CJK режим, $0-парсер словарей и экономия токенов."""
import asyncio
import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database.models import AiTelemetryLog, Card, GenerationJob, UserSetting
from app.database.session import AsyncSessionLocal
from app.services.ai_gateway import blacklist, coverage, glossary
from app.services.ai_gateway import card_quality as cq
from app.services.ai_gateway import path_builder as pb
from app.services.ai_gateway import path_prompts as pp
from app.services.ai_gateway import source_profile as sp
from app.services.generation_worker import process_generation_job
from app.services.knowledge_path import save_learning_path, wipe_subject
from llm_fake import FakeLLM, patched
from main import app

USER = "test_slicing_lang_user"
SUBJECT = "test_slicing_lang_subj"


# ---------------------------------------------------------------------------
# 1. Сохранение Card.mnemonic в БД
# ---------------------------------------------------------------------------

def test_save_learning_path_persists_dict_and_string_mnemonics():
    path_map = pb.normalize_map({
        "title": "Китайский HSK1",
        "domain": "language",
        "source_type": "language",
        "nodes": [
            {"key": "tones", "name": "Тоны и базовые слова", "tier": 0, "order": 1},
        ],
        "edges": [],
    })
    packs = {
        "tones": {
            "lesson": {"screens": [{"say": "1", "emo": "talk", "focus": []}] * 3, "check": []},
            "cards": [
                {
                    "text": "好",
                    "secondary_text": "hǎo | прил.",
                    "translation": "Хороший.",
                    "example": "你好！",
                    "mnemonic": {"keyword": "Женщина + ребёнок", "verbal_cue": "Мама с малышом под рукой — это хорошо."},
                    "initial_difficulty_tier": "easy",
                    "layer": 0,
                    "answer_type": "term",
                    "distractors": ["Плохой.", "Большой.", "Новый."],
                },
                {
                    "text": "妈",
                    "secondary_text": "mā | сущ.",
                    "translation": "Мама.",
                    "example": "我爱妈妈。",
                    "mnemonic": "Ровный 1-й тон — протяжное «мааа».",
                    "initial_difficulty_tier": "easy",
                    "layer": 0,
                    "answer_type": "term",
                    "distractors": ["Папа.", "Брат.", "Сестра."],
                },
                {
                    "text": "Что изучает фонология тонов?",
                    "secondary_text": "Китайский | Фонетика",
                    "translation": "Смыслоразличительную высоту слога.",
                    "example": "",
                    "mnemonic": None,
                    "initial_difficulty_tier": "medium",
                    "layer": 1,
                    "answer_type": "term",
                    "distractors": ["Порядок слов.", "Написание черт.", "Словообразование."],
                },
            ],
        }
    }
    res = {"map": path_map, "packs": packs, "facts": {}, "source_type": "language"}

    async def scenario():
        async with AsyncSessionLocal() as db:
            await wipe_subject(db, USER, SUBJECT)
            try:
                await save_learning_path(db, USER, SUBJECT, res, source_name="HSK1.txt", chars=300)
                await db.commit()
                rows = (await db.execute(
                    select(Card).where(Card.user_id == USER, Card.subject == SUBJECT).order_by(Card.topological_rank)
                )).scalars().all()
                assert len(rows) == 3
                assert rows[0].mnemonic == {"keyword": "Женщина + ребёнок", "verbal_cue": "Мама с малышом под рукой — это хорошо."}
                assert rows[0].secondary_text == "hǎo | прил."
                assert rows[0].example == "你好！"
                assert rows[1].mnemonic == "Ровный 1-й тон — протяжное «мааа»."
                assert rows[2].mnemonic is None
            finally:
                await wipe_subject(db, USER, SUBJECT)
                await db.commit()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 2. Проброс assoc_preference: API импорта -> GenerationJob -> Воркер -> Промпт
# ---------------------------------------------------------------------------

def test_import_endpoints_persist_assoc_preference_and_source_kind():
    from app.api.endpoints.imports import _normalize_assoc_preference

    assert _normalize_assoc_preference("Visual") == "visual"
    assert _normalize_assoc_preference(" acoustic ") == "acoustic"
    assert _normalize_assoc_preference("other") is None
    assert _normalize_assoc_preference(None) is None

    created_job_ids: list[int] = []

    with patch("app.services.generation_worker.claim_next_pending_job", return_value=None):
        with TestClient(app) as client:
            # JSON import
            r1 = client.post(
                "/api/config/import",
                headers={"X-User-Id": "dev_user"},
                json={
                    "text": "Глава 1. Тоны в китайском языке\nПервый тон ровный и высокий. Второй тон восходящий.",
                    "subject": SUBJECT,
                    "source_kind": "language",
                    "assoc_preference": "visual",
                },
            )
            assert r1.status_code == 200, r1.text
            d1 = r1.json()
            assert d1["status"] == "queued"
            created_job_ids.append(d1["job_id"])

            # Multipart file import
            r2 = client.post(
                "/api/config/import/file",
                headers={"X-User-Id": "dev_user"},
                data={
                    "subject": SUBJECT,
                    "source_kind": "language",
                    "assoc_preference": "acoustic",
                },
                files={"files": ("lesson1.txt", "Урок 1. Звуки и тоны.\nМама и лошадь различаются тоном.".encode("utf-8"), "text/plain")},
            )
            assert r2.status_code == 200, r2.text
            d2 = r2.json()
            assert d2["status"] == "queued"
            created_job_ids.append(d2["job_id"])

    async def verify_and_cleanup():
        async with AsyncSessionLocal() as db:
            try:
                j1 = (await db.execute(select(GenerationJob).where(GenerationJob.id == created_job_ids[0]))).scalar_one()
                j2 = (await db.execute(select(GenerationJob).where(GenerationJob.id == created_job_ids[1]))).scalar_one()
                assert j1.assoc_preference == "visual" and j1.source_kind == "language"
                assert j2.assoc_preference == "acoustic" and j2.source_kind == "language"
            finally:
                await db.execute(delete(GenerationJob).where(GenerationJob.id.in_(created_job_ids)))
                await db.commit()

    asyncio.run(verify_and_cleanup())


def test_worker_resolves_assoc_preference_from_job_and_user_setting_fallback():
    from test_knowledge_path import fake_result

    seen_calls: list[dict] = []

    async def fake_build(text, subject, calls=None, course=None, **options):
        seen_calls.append(dict(options))
        calls.append({
            "label": "map#1", "cost_usd": 0.001, "prompt_tokens": 10, "cache_hit_tokens": 0,
            "completion_tokens": 5, "duration_ms": 1, "finish_reason": "stop", "model": "deepseek-flash",
        })
        return fake_result()

    async def scenario():
        async with AsyncSessionLocal() as db:
            await wipe_subject(db, USER, SUBJECT)
            await db.execute(delete(GenerationJob).where(GenerationJob.user_id == USER))
            await db.execute(delete(AiTelemetryLog).where(AiTelemetryLog.user_id == USER))
            await db.execute(delete(UserSetting).where(UserSetting.user_id == USER))
            db.add(UserSetting(user_id=USER, daily_limit=10, target_retention=0.9, assoc_preference="acoustic"))
            job_override = GenerationJob(
                user_id=USER, subject=SUBJECT, theme="T1", raw_text="Текст урока китайского языка " * 5,
                status="processing", source_name="M1", depth="standard", source_kind="language", assoc_preference="visual",
            )
            job_fallback = GenerationJob(
                user_id=USER, subject=SUBJECT, theme="T2", raw_text="Второй текст урока китайского " * 5,
                status="processing", source_name="M2", depth="compact", source_kind="language", assoc_preference=None,
            )
            db.add_all([job_override, job_fallback])
            await db.commit()
            id1, id2 = job_override.id, job_fallback.id

        try:
            with patch("app.services.generation_worker.build_learning_path", side_effect=fake_build):
                await process_generation_job(id1, is_offpeak=True)
                await process_generation_job(id2, is_offpeak=True)
        finally:
            async with AsyncSessionLocal() as db:
                await wipe_subject(db, USER, SUBJECT)
                await db.execute(delete(GenerationJob).where(GenerationJob.user_id == USER))
                await db.execute(delete(AiTelemetryLog).where(AiTelemetryLog.user_id == USER))
                await db.execute(delete(UserSetting).where(UserSetting.user_id == USER))
                await db.commit()

    asyncio.run(scenario())
    assert seen_calls[0]["assoc_preference"] == "visual"
    assert seen_calls[1]["assoc_preference"] == "acoustic"


# ---------------------------------------------------------------------------
# 3. Промпты и экономия токенов (LINKS без книги, компактный MAP в CARDS)
# ---------------------------------------------------------------------------

def test_prompts_restore_fields_respect_assoc_preference_and_keep_cache_prefix():
    sp_text = pp.PATH_BUILDER_SYSTEM_PROMPT
    assert 'guide | language"' in sp_text
    assert "Zero-Spoiler Law" in sp_text
    assert "PART C. TASK \"LINKS\"" in sp_text
    assert "core MAP" in sp_text
    assert "{" not in sp_text.split("PART A")[0]

    t_vis = pp.build_cards_task("{}", ["n1"], {"n1": 4}, source_type="language", assoc_preference="visual")
    assert "ASSOC PREFERENCE: visual" in t_vis
    assert "SOURCE TYPE: foreign language" in t_vis

    t_aco = pp.build_cards_task("{}", ["n1"], {"n1": 4}, source_type="language", assoc_preference="acoustic")
    assert "ASSOC PREFERENCE: acoustic" in t_aco

    t_none = pp.build_cards_task("{}", ["n1"], {"n1": 4}, source_type="textbook", assoc_preference=None)
    assert "ASSOC PREFERENCE" not in t_none

    t_fill = pp.build_fill_task("n1 | Тоны | summary", "Первый тон ровный.", source_type="language", assoc_preference="visual")
    assert "ASSOC PREFERENCE: visual" in t_fill
    assert "SOURCE TYPE: foreign language" in t_fill

    t_lessons = pp.build_lessons_task("### n1 | Тоны | tier 0", source_type="language")
    assert "SOURCE TYPE: language." in t_lessons and "components/radicals" in t_lessons


def test_compact_map_and_cross_links_omit_full_book_and_leaf_nodes():
    raw_map = {
        "title": "Практический китайский",
        "domain": "language",
        "source_type": "language",
        "nodes": [
            {"key": "tones", "name": "Тоны", "tier": 0, "order": 1, "summary": "Основы интонации слога " * 8, "src": "Гл. 1"},
            {"key": "radicals", "name": "Ключи", "tier": 0, "order": 2, "summary": "Графемы иероглифов", "src": "Гл. 1"},
            {"key": "greetings", "name": "Приветствия", "tier": 1, "prereqs": ["tones"], "order": 3, "summary": "Базовый этикет", "src": "Гл. 2"},
            {"key": "ni_hao", "name": "Фраза нихао", "tier": 2, "parent": "greetings", "order": 4, "summary": "Детали употребления", "src": "Гл. 2.1"},
            {"key": "case_tone3", "name": "Сандхи третьего тона", "tier": 3, "parent": "greetings", "order": 5, "summary": "Кейс", "src": "Гл. 2.2"},
        ],
        "edges": [
            {"from": "tones", "to": "greetings", "relation": "applies_to", "label": "применяется в"},
            {"from": "ni_hao", "to": "case_tone3", "relation": "leads_to", "label": "ведёт к"},
        ],
    }
    path_map = pb.normalize_map(raw_map)

    compact_raw = pb._compact_map_for_cards(path_map)
    compact = json.loads(compact_raw)
    assert "edges" not in compact
    n0 = compact["nodes"][0]
    assert "order" not in n0 and "kind" not in n0 and "prereqs" not in n0
    assert len(n0["summary"]) <= 90

    big_book = "Учебник китайского языка. Подробное описание фонетики и грамматики. " * 200
    fake = FakeLLM(raw_map=raw_map)
    with patched(fake):
        asyncio.run(pb.build_cross_links(big_book, path_map, []))

    links_prompts = fake.of("LINKS")
    assert len(links_prompts) == 1
    lp = links_prompts[0]
    assert "[SOURCE MATERIAL" not in lp
    assert "Подробное описание фонетики и грамматики" not in lp
    assert '"tones"' in lp and '"greetings"' in lp
    assert '"ni_hao"' not in lp and '"case_tone3"' not in lp
    assert len(lp) < len(big_book) / 5


# ---------------------------------------------------------------------------
# 4. CJK-токенизация, локатор, чёрный список и профиль "language"
# ---------------------------------------------------------------------------

def test_cjk_coverage_locator_and_blacklist_support():
    toks = coverage.stems("汉字的学习方法")
    assert "汉" in toks and "字" in toks and "汉字" in toks and "学习" in toks

    # Однообразная каша из одного иероглифа не считается пригодным TF-IDF индексом
    junk = "字" * 30_000
    junk_idx = coverage.SourceIndex(junk)
    assert not junk_idx.usable
    assert cq.CardVerifier(junk, junk_idx).check({"text": "字", "translation": "Знак.", "evidence": "Что-то."})["status"] == "unchecked"

    # Настоящий учебный текст с разнообразной лексикой проходит индексацию и точный поиск цитаты
    target_sent = "今天天气很好，我们去公园散步吧。"
    varied_blocks = [
        f"Урок {i}. Изучаем диалог номер {i}: слово 语言学习, выражение 公园散步, чтение 汉字书写 и грамматику 了。\n"
        f"Разбираем иероглифы 第{chr(0x4e00 + i)}课, 词{chr(0x5000 + i)}, 语{chr(0x5200 + i)} и пояснения.\n"
        for i in range(1, 140)
    ]
    book = "".join(varied_blocks[:40]) + f"\nДиалог о погоде: {target_sent}\n" + "".join(varied_blocks[40:])
    idx = coverage.SourceIndex(book)
    assert idx.usable

    ver = cq.CardVerifier(book, idx).check({"text": "今天天气怎么样？", "translation": "今天天气很好。", "evidence": "今天天气很好"})
    assert ver["status"] == "grounded"

    # Чёрный список пропускает короткие CJK и иностранные слова, но по-прежнему отсекает мусор
    assert not blacklist.is_structurally_invalid_card({"text": "好", "translation": "Хороший."})[0]
    assert not blacklist.is_structurally_invalid_card({"text": "go", "translation": "идти."})[0]
    assert not blacklist.is_structurally_invalid_card({"text": "thé", "translation": "чай"})[0]
    assert blacklist.is_structurally_invalid_card({"text": "Что?", "translation": "А"})[0]
    assert blacklist.is_structurally_invalid_card({"text": "Можно ли шуметь?", "translation": "Нет."})[0]


def test_source_profile_language_kind_and_normalize_map():
    assert sp.CHARS_PER_CARD["language"] == 750
    assert sp.FACTS_PER_CARD["language"] == 0.5

    cjk_text = (
        "Урок 1. Иероглифы и тоны в китайском языке.\n"
        "你好 (nǐ hǎo) — приветствие при встрече.\n"
        "谢谢 (xièxie) — благодарность собеседнику.\n"
        "今天天气很好，我们学习汉字和汉语拼音规则。\n"
        "老师学生朋友中国北京上海汉语汉字声调练习。"
    )
    assert sp.heuristic_kind(cjk_text) == "language"

    m = pb.normalize_map({
        "title": "Китайский язык",
        "domain": "language",
        "source_type": "language",
        "nodes": [{"key": "a", "name": "Основы", "tier": 0, "order": 1}],
        "edges": [],
    })
    assert m["source_type"] == "language"

    m_gl = pb.normalize_map({
        "title": "Словарь",
        "domain": "language",
        "source_type": "glossary",
        "nodes": [{"key": "a", "name": "Слова", "tier": 0, "order": 1}],
        "edges": [],
    })
    assert m_gl["source_type"] == "glossary"


# ---------------------------------------------------------------------------
# 5. $0-парсер словарей слов VS полноценный учебник языка через ИИ
# ---------------------------------------------------------------------------

def test_bilingual_vocab_list_runs_zero_cost_without_llm():
    vocab_lines = [
        "你好 [nǐ hǎo] — привет",
        "谢谢 [xièxie] — спасибо",
        "再见 [zàijiàn] — до свидания",
        "请 [qǐng] — пожалуйста",
        "对不起 [duìbuqǐ] — извините",
        "没关系 [méi guānxi] — ничего страшного",
        "1. 学习\txuéxí\tучиться",
        "2. 工作\tgōngzuò\tработать",
        "老师 | lǎoshī | учитель",
        "学生 | xuésheng | студент",
        "朋友 (péngyou) — друг",
        "家人 (jiārén) — семья",
        "中国 (Zhōngguó) — Китай",
    ]
    vocab_text = "\n".join(vocab_lines)
    assert glossary.looks_like_vocab(vocab_text)

    fake = FakeLLM()
    course = {
        "nodes": [{"key": "old", "name": "База", "tier": 0, "summary": "x"}],
        "cards": {"old": [{"q": "你好", "a": "привет."}]},
    }
    with patched(fake):
        res = asyncio.run(pb.build_learning_path(vocab_text, "chinese", course=course, source_kind="language"))

    assert fake.log == []
    assert res["cost_usd"] == 0.0
    assert res["source_type"] == "language"
    assert res["stats"]["source"]["skipped_already_asked"] == 1
    cards = [c for p in res["packs"].values() for c in p["cards"]]
    assert len(cards) == len(vocab_lines) - 1
    assert all(c["text"] != "你好" for c in cards)
    thanks = next(c for c in cards if c["text"] == "谢谢")
    assert thanks["secondary_text"] == "xièxie"
    assert thanks["translation"] == "спасибо."
    assert len(thanks["distractors"]) == 3
    study = next(c for c in cards if c["text"] == "学习")
    assert study["secondary_text"] == "xuéxí"
    friend = next(c for c in cards if c["text"] == "朋友")
    assert friend["secondary_text"] == "péngyou"


def test_russian_glossary_and_full_language_textbook_do_not_trigger_vocab_fast_path():
    ru_glossary = "\n".join([
        f"Термин номер {i} — развёрнутое определение правового института номер {i}, регулирующего отношения субъектов."
        for i in range(1, 20)
    ])
    assert not glossary.looks_like_vocab(ru_glossary)

    chinese_textbook = (
        "Глава 1. Фонетическая система и структура простого предложения в китайском языке.\n\n"
        "В китайском языке каждый слог несет смыслоразличительный тон. Первый тон ровный и высокий, "
        "второй поднимается снизу вверх, третий сначала опускается, затем поднимается, а четвертый резко падает.\n\n"
        "Диалог 1. Встреча в университете.\n"
        "А: 你好！请问你是新来的留学生吗？(Здравствуйте! Скажите, вы новый иностранный студент?)\n"
        "Б: 是的，我叫安娜，很高兴认识你。(Да, меня зовут Анна, очень рада знакомству с тобой.)\n\n"
        "Грамматический комментарий: глагол-связка 是 (shì) ставится между подлежащим и именным сказуемым, "
        "а отрицание строится с помощью наречия 不 (bù), при этом перед четвертым тоном оно читается вторым тоном (bú). "
        "Для подтверждения завершения действия используется частица 了 (le) после глагола.\n"
    )
    assert not glossary.looks_like_vocab(chinese_textbook, min_entries=5, min_share=0.6)

    raw_map = {
        "title": "Практический курс китайского языка",
        "domain": "language",
        "source_type": "language",
        "nodes": [
            {"key": "tones", "name": "Система тонов", "tier": 0, "order": 1, "summary": "Четыре тона слога", "src": "Гл. 1"},
            {"key": "copula_shi", "name": "Связка 是 и отрицание 不", "tier": 1, "prereqs": ["tones"], "order": 2, "summary": "Именное сказуемое", "src": "Гл. 1"},
            {"key": "particle_le", "name": "Частица 了", "tier": 2, "parent": "copula_shi", "order": 3, "summary": "Завершение действия", "src": "Гл. 1"},
        ],
        "edges": [{"from": "tones", "to": "copula_shi", "relation": "applies_to", "label": "меняет чтение в"}],
    }

    def cards_handler(prompt, _fake):
        assert "ASSOC PREFERENCE: visual" in prompt
        assert "SOURCE TYPE: foreign language" in prompt
        return {
            "nodes": [
                {
                    "key": "tones",
                    "cards": [{
                        "t": "Как меняется произношение слога в четвертом тоне в китайском языке?",
                        "s": "Фонетика | Четвёртый тон",
                        "d": "Высота голоса резко падает сверху вниз.",
                        "ev": "четвертый резко падает",
                        "y": 0,
                        "at": "rule",
                        "x": ["Тон остаётся ровным.", "Голос плавно поднимается.", "Слог читается шёпотом."],
                        "e": "Слог bù без тона-сандхи резко обрывается вниз.",
                        "m": {"k": "Горка вниз", "c": "Четвёртый тон — стремительный спуск с вершины горки."},
                    }],
                },
                {
                    "key": "copula_shi",
                    "cards": [{
                        "t": "是",
                        "s": "shì | глагол-связка",
                        "d": "Быть, являться.",
                        "ev": "глагол-связка 是 (shì) ставится между подлежащим",
                        "y": 0,
                        "at": "term",
                        "x": ["Иметь.", "Делать.", "Идти."],
                        "e": "我是学生。(Я — студент.)",
                        "m": {"k": "Солнце над землёй", "c": "Иероглиф 日 сверху — как солнце, утверждающее факт."},
                    }],
                },
                {
                    "key": "particle_le",
                    "cards": [{
                        "t": "Где в предложении ставится частица 了 для выражения завершённости?",
                        "s": "Грамматика | Аспект",
                        "d": "После глагола.",
                        "ev": "используется частица 了 (le) после глагола",
                        "y": 1,
                        "at": "rule",
                        "x": ["Перед подлежащим.", "Перед отрицанием.", "Внутри связки."],
                        "e": "我买了书。(Я купил книгу.)",
                    }],
                },
            ]
        }

    def links_handler(_prompt, _fake):
        return {"edges": [{"from": "tones", "to": "copula_shi", "relation": "leads_to", "label": "вызывает сандхи для"}]}

    fake = FakeLLM(raw_map=raw_map, cards=cards_handler)
    fake.handlers["LINKS"] = links_handler

    with patched(fake):
        res = asyncio.run(pb.build_learning_path(
            chinese_textbook, "chinese", source_kind="language", assoc_preference="visual"
        ))

    kinds = [k for k, _ in fake.log]
    assert "MAP" in kinds and "CARDS" in kinds and "LESSON" in kinds
    assert res["source_type"] == "language"
    assert len(res["map"]["edges"]) >= 1
    shi_card = res["packs"]["copula_shi"]["cards"][0]
    assert shi_card["secondary_text"] == "shì | глагол-связка"
    assert shi_card["example"] == "我是学生。(Я — студент.)"
    assert shi_card["mnemonic"]["keyword"] == "Солнце над землёй"
