# tests/test_milestone2_pipeline.py
"""
Unit and Integration Tests for Milestone 2: AI Gateway & Worker Pipeline Integration.
Validates:
1. unpack_minified_cards: Extraction, normalization, and tree synthesis of graph payload.
2. Robust error handling and fallback when graph is missing or malformed.
3. Volume calibration: Anti-overload limits (2-4 cards per chunk) and cognitive directives.
4. Worker pipeline: End-to-end multi-chunk graph consolidation, TopicKnowledgeGraph DB persistence, and incremental merges.
"""

import unittest
import asyncio
import json
from unittest.mock import patch, AsyncMock
from datetime import datetime
from sqlalchemy import select, delete

from app.database.session import AsyncSessionLocal
from app.database.models import GenerationJob, TopicKnowledgeGraph, Card
from app.services.ai_gateway import (
    unpack_minified_cards,
    build_granularity_prompt,
    DEEPSEEK_CACHED_SYSTEM_PROMPT,
)
from app.services.generation_worker import process_generation_job
from app.services.graph_service import (
    clean_graph_data,
    build_hierarchical_tree,
    consolidate_knowledge_graphs,
)


class TestMilestone2Pipeline(unittest.TestCase):
    def setUp(self):
        self.test_user = "test_m2_user"
        self.test_subject = "sudoustroystvo_m2"

        async def cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(GenerationJob).where(GenerationJob.user_id == self.test_user))
                await db.execute(delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == self.test_user))
                await db.commit()

        asyncio.run(cleanup())

    def tearDown(self):
        async def cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(GenerationJob).where(GenerationJob.user_id == self.test_user))
                await db.execute(delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == self.test_user))
                await db.commit()

        asyncio.run(cleanup())

    def test_01_unpack_minified_cards_with_knowledge_graph(self):
        """Проверка распаковки карточек и извлечения графа знаний с синтезом дерева из JSON ответа LLM."""
        mock_llm_payload = {
            "domain": "law",
            "slug": "sudoustroystvo",
            "title": "Судоустройство РФ",
            "graph": {
                "nodes": [
                    {
                        "id": "vs_rf",
                        "name": "Верховный Суд РФ",
                        "category": "authority",
                        "summary": "Высший судебный орган по гражданским, уголовным и административным делам.",
                        "parent_id": None,
                        "level": 0
                    },
                    {
                        "id": "kassation_court",
                        "name": "Кассационный суд общей юрисдикции",
                        "category": "instance",
                        "summary": "Суд третьей инстанции для проверки вступивших в законную силу актов.",
                        "parent_id": "vs_rf",
                        "level": 1
                    },
                    {
                        "id": "term_exception",
                        "name": "Срок сплошной кассации",
                        "category": "condition",
                        "summary": "6 месяцев со дня вступления приговора в законную силу.",
                        "parent_id": "kassation_court",
                        "level": 2
                    }
                ],
                "edges": [
                    {
                        "source": "kassation_court",
                        "target": "vs_rf",
                        "relation": "appealed_to",
                        "label": "Обжалование в коллегию ВС РФ"
                    },
                    {
                        "source": "term_exception",
                        "target": "kassation_court",
                        "relation": "subject_to_jurisdiction",
                        "label": "Условие допустимости"
                    }
                ]
            },
            "c": [
                {
                    "t": "Судебный акт уже вступил в законную силу. В какую инстанцию подается жалоба на существенные нарушения?",
                    "s": "ст. 401.3 УПК РФ | Сплошная кассация",
                    "d": "В кассационный суд общей юрисдикции.",
                    "e": "Жалоба подается в течение 6 месяцев со дня вступления.",
                    "l": "medium",
                    "h": "Кассация"
                }
            ]
        }

        unpacked = unpack_minified_cards(mock_llm_payload, fallback_subject="sudoustroystvo")

        # 1. Проверяем карточки
        self.assertEqual(len(unpacked["cards"]), 1)
        self.assertIn("кассационный суд", unpacked["cards"][0]["translation"].lower())
        self.assertEqual(unpacked["cards"][0]["theme"], "Кассация")

        # 2. Проверяем наличие и структуру графа
        self.assertIn("knowledge_graph", unpacked)
        kg = unpacked["knowledge_graph"]
        self.assertIsNotNone(kg)
        self.assertEqual(len(kg["nodes"]), 3)
        self.assertEqual(len(kg["edges"]), 2)

        # 3. Проверяем наличие синтезированного дерева
        tree = kg["tree_data"]
        self.assertIsNotNone(tree)
        self.assertIn("id", tree)
        self.assertIn("children", tree)

    def test_02_unpack_minified_cards_resilience_to_malformed_graph(self):
        """Проверка устойчивости unpack_minified_cards к битому графу (висячие ребра, циклы, невалидные поля)."""
        broken_payload = {
            "c": [
                {"t": "Термин А", "d": "Определение А."}
            ],
            "graph": {
                "nodes": [
                    {"id": "node_1", "name": "Узел 1", "category": "invalid_cat", "parent_id": "non_existent"}
                ],
                "edges": [
                    {"source": "node_1", "target": "ghost_node", "relation": "invalid_rel"}, # dangling
                    {"source": "node_1", "target": "node_1", "relation": "appealed_to"}       # self-loop
                ]
            }
        }

        unpacked = unpack_minified_cards(broken_payload)
        self.assertEqual(len(unpacked["cards"]), 1)
        kg = unpacked["knowledge_graph"]
        self.assertIsNotNone(kg)
        self.assertEqual(len(kg["nodes"]), 1)
        self.assertEqual(kg["nodes"][0]["category"], "authority")  # fallback to authority
        self.assertIsNone(kg["nodes"][0]["parent_id"])             # non-existent parent cleaned
        self.assertEqual(len(kg["edges"]), 0)                      # dangling and self-loops purged

    def test_03_volume_calibration_and_pareto_limits(self):
        """Проверка калибровки объема карточек в auto режиме (2-4 карточки на чанк для ~70-90 на книгу)."""
        prompt_auto = build_granularity_prompt("atomic", "", "medium", "auto")
        self.assertIn("2 to 4 high-yield situational cards", prompt_auto)
        self.assertIn("Decision Trees", prompt_auto)
        self.assertIn("Contrast Pairs", prompt_auto)

    def test_04_worker_multi_chunk_consolidation_and_db_persistence(self):
        """Интеграционный тест: воркер консолидирует графы всех чанков и сохраняет в TopicKnowledgeGraph."""
        async def run_test():
            # 1. Создаем тестовую задачу
            raw_text = "=== СТРАНИЦА 1 ===\nТекст первой страницы.\n\n=== СТРАНИЦА 2 ===\nТекст второй страницы."
            async with AsyncSessionLocal() as db:
                job = GenerationJob(
                    user_id=self.test_user,
                    telegram_id="12345678",
                    subject=self.test_subject,
                    theme="Судебная система РФ",
                    raw_text=raw_text,
                    granularity_mode="atomic",
                    density="medium",
                    volume="auto",
                    status="pending"
                )
                db.add(job)
                await db.commit()
                await db.refresh(job)
                job_id = job.id

            # 2. Мокаем parse_raw_text и split_text_into_chunks, чтобы эмулировать 2 чанка с графами
            chunk1_output = {
                "phrase_title": "Судебная система РФ",
                "cards": [
                    {"text": "Вопрос 1", "secondary_text": "", "translation": "Ответ 1.", "example": "", "initial_difficulty_tier": "medium", "theme": "Инстанции", "content_type": "text"}
                ],
                "knowledge_graph": {
                    "nodes": [
                        {"id": "vs_rf", "name": "Верховный Суд РФ", "category": "authority", "summary": "Высший суд.", "parent_id": None, "level": 0},
                        {"id": "arbitrazh", "name": "Арбитражный суд", "category": "instance", "summary": "Экономические споры.", "parent_id": "vs_rf", "level": 1}
                    ],
                    "edges": [
                        {"source": "arbitrazh", "target": "vs_rf", "relation": "appealed_to", "label": "Кассация в ВС"}
                    ]
                }
            }

            chunk2_output = {
                "phrase_title": "Судебная система РФ",
                "cards": [
                    {"text": "Вопрос 2", "secondary_text": "", "translation": "Ответ 2.", "example": "", "initial_difficulty_tier": "hard", "theme": "Подсудность", "content_type": "text"}
                ],
                "knowledge_graph": {
                    "nodes": [
                        {"id": "arbitrazh", "name": "Арбитражный суд РФ", "category": "instance", "summary": "Суд по экономическим спорам предпринимателей.", "parent_id": "vs_rf", "level": 1},
                        {"id": "subpodvedomost", "name": "Спор юрлиц", "category": "condition", "summary": "Условие арбитражной юрисдикции.", "parent_id": "arbitrazh", "level": 2}
                    ],
                    "edges": [
                        {"source": "subpodvedomost", "target": "arbitrazh", "relation": "subject_to_jurisdiction", "label": "Подведомственность"}
                    ]
                }
            }

            call_count = 0
            async def mock_parse_raw_text(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                return chunk1_output if call_count % 2 == 1 else chunk2_output

            with patch("app.services.generation_worker.split_text_into_chunks", return_value=["Часть 1 довольно длинного текста", "Часть 2 довольно длинного текста"]), \
                 patch("app.services.generation_worker.parse_raw_text", side_effect=mock_parse_raw_text), \
                 patch("app.services.generation_worker.send_worker_telegram_push", new_callable=AsyncMock):
                await process_generation_job(job_id=job_id, is_offpeak=False)

            # 3. Проверяем статус задачи в БД
            async with AsyncSessionLocal() as db:
                res = await db.execute(select(GenerationJob).where(GenerationJob.id == job_id))
                updated_job = res.scalar_one()
                self.assertEqual(updated_job.status, "ready_for_review")
                self.assertEqual(updated_job.cards_count, 2)

                # 4. Проверяем TopicKnowledgeGraph
                res_kg = await db.execute(
                    select(TopicKnowledgeGraph).where(
                        TopicKnowledgeGraph.user_id == self.test_user,
                        TopicKnowledgeGraph.subject == self.test_subject
                    )
                )
                kg_record = res_kg.scalar_one_or_none()
                self.assertIsNotNone(kg_record, "TopicKnowledgeGraph запись должна быть создана воркером!")

                graph_data = kg_record.graph_data
                nodes = graph_data.get("nodes", [])
                edges = graph_data.get("edges", [])

                # Арбитражный суд должен быть дедуплицирован (всего 3 уникальных узла: vs_rf, arbitrazh, subpodvedomost)
                self.assertEqual(len(nodes), 3)
                # Самое подробное summary должно быть сохранено для Арбитражного суда
                arb_node = next(n for n in nodes if "arbitrazh" in n["id"])
                self.assertIn("предпринимателей", arb_node["summary"])

                # Ребра должны быть сохранены
                self.assertEqual(len(edges), 2)

                # Дерево должно быть построено
                self.assertIsNotNone(kg_record.tree_data)
                self.assertIn("children", kg_record.tree_data)

        asyncio.run(run_test())

    def test_05_incremental_merge_with_existing_graph(self):
        """Проверка инкрементального слияния: при повторной генерации в ту же тему граф дополняется, а не перезаписывается."""
        async def run_test():
            # 1. Предварительно создаем существующий граф с 1 узлом
            async with AsyncSessionLocal() as db:
                initial_graph = TopicKnowledgeGraph(
                    user_id=self.test_user,
                    subject=self.test_subject,
                    graph_data={
                        "nodes": [
                            {"id": "constitutional_court", "name": "Конституционный Суд РФ", "category": "authority", "summary": "Контроль конституционности законов.", "parent_id": None, "level": 0}
                        ],
                        "edges": []
                    },
                    tree_data={"id": "constitutional_court", "name": "КС РФ", "children": []}
                )
                db.add(initial_graph)
                await db.commit()

            # 2. Создаем задачу генерации
            async with AsyncSessionLocal() as db:
                job = GenerationJob(
                    user_id=self.test_user,
                    telegram_id="12345678",
                    subject=self.test_subject,
                    theme="Суды",
                    raw_text="Достаточно длинный текст лекции о судебной системе Российской Федерации для нарезки карточек.",
                    granularity_mode="atomic",
                    density="medium",
                    volume="auto",
                    status="pending"
                )
                db.add(job)
                await db.commit()
                await db.refresh(job)
                job_id = job.id

            new_chunk_output = {
                "phrase_title": "Суды",
                "cards": [
                    {"text": "Вопрос", "secondary_text": "", "translation": "Ответ.", "example": "", "initial_difficulty_tier": "easy", "theme": "Суды", "content_type": "text"}
                ],
                "knowledge_graph": {
                    "nodes": [
                        {"id": "general_jurisdiction", "name": "Суды общей юрисдикции", "category": "authority", "summary": "Система СОЮ.", "parent_id": None, "level": 0}
                    ],
                    "edges": []
                }
            }

            with patch("app.services.generation_worker.parse_raw_text", new_callable=AsyncMock, return_value=new_chunk_output), \
                 patch("app.services.generation_worker.send_worker_telegram_push", new_callable=AsyncMock):
                await process_generation_job(job_id=job_id, is_offpeak=False)

            # 3. Проверяем, что в TopicKnowledgeGraph теперь ОБА узла
            async with AsyncSessionLocal() as db:
                res = await db.execute(
                    select(TopicKnowledgeGraph).where(
                        TopicKnowledgeGraph.user_id == self.test_user,
                        TopicKnowledgeGraph.subject == self.test_subject
                    )
                )
                kg = res.scalar_one()
                node_ids = [n["id"] for n in kg.graph_data["nodes"]]
                self.assertIn("constitutional_court", node_ids)
                self.assertIn("general_jurisdiction", node_ids)
                self.assertEqual(len(node_ids), 2)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
