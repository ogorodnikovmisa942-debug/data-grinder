# tests/test_reviewer_adversarial.py
import unittest
import asyncio
from datetime import datetime
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func, delete

from main import app
from app.database.session import AsyncSessionLocal
from app.database.models import Card, Phrase, TopicKnowledgeGraph, PracticeItem, PracticeSessionLog, ReviewLog
from app.services.graph_service import synthesize_graph_from_cards, resolve_subject_alias, get_all_subject_aliases


class TestReviewerAdversarial(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_1_knowledge_graph_endpoints_and_node_count(self):
        """Проверяет эндпоинты GET и POST /rebuild: каркас 25-45 узлов и канонический алиас."""
        for sub in ("sudoustr", "sudoustroystvo"):
            res = await self.client.get(f"/api/knowledge-graph?subject={sub}", headers={"X-User-Id": "dev_user"})
            self.assertEqual(res.status_code, 200, f"Failed for subject {sub}")
            data = res.json()
            nodes = data.get("graph_data", {}).get("nodes", [])
            edges = data.get("graph_data", {}).get("edges", [])
            self.assertIn(data.get("subject"), ("sudoustr", "sudoustroystvo"))
            self.assertGreaterEqual(len(nodes), 25, f"Nodes count {len(nodes)} is under 25")
            self.assertLessEqual(len(nodes), 45, f"Nodes count {len(nodes)} exceeds 45")
            self.assertGreater(len(edges), 20)
            self.assertIsNotNone(data.get("tree_data"))

        # Rebuild endpoint
        res_rebuild = await self.client.post("/api/knowledge-graph/rebuild?subject=sudoustr", headers={"X-User-Id": "dev_user"})
        self.assertEqual(res_rebuild.status_code, 200)
        rebuild_data = res_rebuild.json()
        rebuild_nodes = rebuild_data.get("graph_data", {}).get("nodes", [])
        self.assertGreaterEqual(len(rebuild_nodes), 25)
        self.assertLessEqual(len(rebuild_nodes), 45)
        self.assertIn(rebuild_data.get("subject"), ("sudoustr", "sudoustroystvo"))

    async def test_2_manual_card_addition_syncs_graph_and_practice(self):
        """Проверяет, что ручное добавление карты обновляет граф знаний и практику."""
        test_user = "test_manual_sync_user"
        # 1. Create a card
        payload = {
            "text": "Тестовая статья о судах?",
            "secondary_text": "Раздел 1 | Основы судоустройства",
            "translation": "Судебная власть осуществляется только судами.",
            "phrase_title": "Судоустройство",
            "subject": "sudoustr",
            "example": "Пример нормы",
            "difficulty": 5.0,
            "mnemonic_keyword": "",
            "mnemonic_cue": ""
        }
        res = await self.client.post("/api/management/cards", json=payload, headers={"X-User-Id": test_user})
        self.assertEqual(res.status_code, 200)

        # 2. Verify KG and Practice items were generated and saved
        async with AsyncSessionLocal() as db:
            kg = (await db.execute(select(TopicKnowledgeGraph).where(
                TopicKnowledgeGraph.user_id == test_user,
                TopicKnowledgeGraph.subject == "sudoustroystvo"
            ))).scalars().first()
            self.assertIsNotNone(kg, "Knowledge graph was not created on manual card add")
            self.assertTrue(len(kg.graph_data.get("nodes", [])) > 0)

            practice = (await db.execute(select(PracticeItem).where(
                PracticeItem.user_id == test_user,
                PracticeItem.subject == "sudoustroystvo"
            ))).scalars().all()
            self.assertGreaterEqual(len(practice), 1, "Practice items not synchronized")

            # Cleanup
            await db.execute(delete(Card).where(Card.user_id == test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.execute(delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user))
            await db.execute(delete(PracticeItem).where(PracticeItem.user_id == test_user))
            await db.commit()

    async def test_3_cascading_deletion_and_cross_links(self):
        """Проверяет чистое каскадное удаление всех сущностей и межпредметных связей."""
        test_user = "test_cascade_user"
        now = datetime.utcnow()

        async with AsyncSessionLocal() as db:
            await db.execute(delete(Card).where(Card.user_id == test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.execute(delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user))
            await db.execute(delete(PracticeItem).where(PracticeItem.user_id == test_user))
            await db.execute(delete(PracticeSessionLog).where(PracticeSessionLog.user_id == test_user))
            await db.execute(delete(ReviewLog).where(ReviewLog.user_id == test_user))
            await db.commit()

        async with AsyncSessionLocal() as db:
            p1 = Phrase(text="Тема 1", subject="sudoustr", user_id=test_user)
            p2 = Phrase(text="Тема 2", subject="sudoustroystvo", user_id=test_user)
            db.add_all([p1, p2])
            await db.flush()

            c1 = Card(text="Вопрос 1", translation="Ответ 1", phrase_id=p1.id, subject="sudoustr", user_id=test_user, next_review=now)
            c2 = Card(text="Вопрос 2", translation="Ответ 2", phrase_id=p2.id, subject="sudoustroystvo", user_id=test_user, next_review=now)
            db.add_all([c1, c2])
            await db.flush()

            # Review log
            rl1 = ReviewLog(card_id=c1.id, user_id=test_user, rating=3, review_time=now)
            rl2 = ReviewLog(card_id=c2.id, user_id=test_user, rating=4, review_time=now)
            db.add_all([rl1, rl2])

            kg = TopicKnowledgeGraph(user_id=test_user, subject="sudoustroystvo", graph_data={"nodes": [{"id": "root"}], "edges": []})
            import uuid
            pi = PracticeItem(item_id=str(uuid.uuid4()), user_id=test_user, subject="sudoustr", item_type="slot_filling", prompt="p", options=["a"], correct_answer="a")
            pl = PracticeSessionLog(user_id=test_user, subject="sudoustr", score=10, total=10, percentage=100.0, created_at=now)

            # Other subject with cross-links
            other_kg = TopicKnowledgeGraph(
                user_id=test_user,
                subject="criminal_law",
                graph_data={
                    "nodes": [
                        {"id": "cl_root", "name": "Уголовное право", "category": "authority", "level": 0},
                        {"id": "sudoustroystvo_ref", "name": "Судоустройство РФ", "category": "authority", "level": 1}
                    ],
                    "edges": [
                        {"source": "sudoustroystvo_ref", "target": "cl_root", "relation": "demarcated_from", "label": "связь с судоустройством"}
                    ]
                },
                tree_data={}
            )
            db.add_all([kg, pi, pl, other_kg])
            await db.commit()

        # Execute cascading delete via alias
        res = await self.client.delete("/api/data/subjects/sudoustr", headers={"X-User-Id": test_user})
        self.assertEqual(res.status_code, 200)

        # Verify nothing remains under any alias
        async with AsyncSessionLocal() as db:
            aliases = get_all_subject_aliases("sudoustr")
            cards = (await db.execute(select(Card).where(Card.user_id == test_user, Card.subject.in_(aliases)))).scalars().all()
            phrases = (await db.execute(select(Phrase).where(Phrase.user_id == test_user, Phrase.subject.in_(aliases)))).scalars().all()
            kgs = (await db.execute(select(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user, TopicKnowledgeGraph.subject.in_(aliases)))).scalars().all()
            pis = (await db.execute(select(PracticeItem).where(PracticeItem.user_id == test_user, PracticeItem.subject.in_(aliases)))).scalars().all()
            pls = (await db.execute(select(PracticeSessionLog).where(PracticeSessionLog.user_id == test_user, PracticeSessionLog.subject.in_(aliases)))).scalars().all()
            logs = (await db.execute(select(ReviewLog).where(ReviewLog.user_id == test_user))).scalars().all()

            self.assertEqual(len(cards), 0)
            self.assertEqual(len(phrases), 0)
            self.assertEqual(len(kgs), 0)
            self.assertEqual(len(pis), 0)
            self.assertEqual(len(pls), 0)
            self.assertEqual(len(logs), 0)

            # Check other subject KG has cleaned cross edges & nodes
            other = (await db.execute(select(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user, TopicKnowledgeGraph.subject == "criminal_law"))).scalars().first()
            self.assertIsNotNone(other)
            other_edges = other.graph_data.get("edges", [])
            other_nodes = other.graph_data.get("nodes", [])
            self.assertEqual(len(other_edges), 0, "Cross-subject edges were not pruned")
            self.assertFalse(any(n["id"] == "sudoustroystvo_ref" for n in other_nodes), "Cross-subject node not pruned")
            self.assertIsNotNone(other.tree_data)

            # Clean other_kg
            await db.delete(other)
            await db.commit()

    async def test_4_bulk_delete_cards_removes_review_logs(self):
        """Проверяет, что при bulk_delete_cards удаляются и ReviewLog."""
        test_user = "test_bulk_del_user"
        now = datetime.utcnow()
        card_id = None

        async with AsyncSessionLocal() as db:
            p = Phrase(text="Тема", subject="test_del", user_id=test_user)
            db.add(p)
            await db.flush()
            c = Card(text="В", translation="О", phrase_id=p.id, subject="test_del", user_id=test_user, next_review=now)
            db.add(c)
            await db.flush()
            card_id = c.id
            rl = ReviewLog(card_id=card_id, user_id=test_user, rating=3, review_time=now)
            db.add(rl)
            await db.commit()

        del_res = await self.client.post("/api/data/cards/delete", json={"card_ids": [card_id]}, headers={"X-User-Id": test_user})
        self.assertEqual(del_res.status_code, 200)

        async with AsyncSessionLocal() as db:
            logs = (await db.execute(select(ReviewLog).where(ReviewLog.card_id == card_id))).scalars().all()
            self.assertEqual(len(logs), 0, "Orphaned ReviewLog remained after bulk_delete_cards")
            cards = (await db.execute(select(Card).where(Card.id == card_id))).scalars().all()
            self.assertEqual(len(cards), 0)
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.commit()

    async def test_5_card_archive_alias_lookup(self):
        """Проверяет, что архив карт GET /api/data/cards находит карты по обоим алиасам."""
        r1 = await self.client.get("/api/data/cards?subject=sudoustr", headers={"X-User-Id": "dev_user"})
        r2 = await self.client.get("/api/data/cards?subject=sudoustroystvo", headers={"X-User-Id": "dev_user"})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json()["total"], r2.json()["total"])
        self.assertGreater(r1.json()["total"], 300)

    async def test_6_subjects_details_aggregates_canonical(self):
        """Проверяет, что список предметов не двоит алиасы."""
        res = await self.client.get("/api/data/subjects/details", headers={"X-User-Id": "dev_user"})
        self.assertEqual(res.status_code, 200)
        slugs = [s["slug"] for s in res.json().get("subjects", [])]
        self.assertTrue("sudoustr" in slugs or "sudoustroystvo" in slugs)

    async def test_7_save_cards_canonical_normalization_and_deduplication(self):
        """Проверяет нормализацию алиаса к каноническому и дедупликацию в save_cards_to_database."""
        from app.api.endpoints.management import save_cards_to_database
        test_user = "test_save_cards_user"

        async with AsyncSessionLocal() as db:
            await db.execute(delete(Card).where(Card.user_id == test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.commit()

            cards_batch = [
                {"text": "Вопрос нормализации?", "translation": "Ответ 1", "theme": "Судебная власть"},
                {"text": "Второй вопрос?", "translation": "Ответ 2", "theme": "Судебная власть"}
            ]
            saved, canon_sub, title = await save_cards_to_database(cards_batch, "sudoustr", "Тема", test_user, db)
            await db.commit()

            self.assertEqual(saved, 2)
            self.assertEqual(canon_sub, "sudoustroystvo")

            # Verify in DB: all cards and phrases are stored under canonical subject
            db_cards = (await db.execute(select(Card).where(Card.user_id == test_user))).scalars().all()
            self.assertEqual(len(db_cards), 2)
            for c in db_cards:
                self.assertEqual(c.subject, "sudoustroystvo")

            db_phrases = (await db.execute(select(Phrase).where(Phrase.user_id == test_user))).scalars().all()
            for p in db_phrases:
                self.assertEqual(p.subject, "sudoustroystvo")

            # Deduplication: Re-saving existing card should update rather than insert duplicate
            updated_batch = [
                {"text": "Вопрос нормализации?", "translation": "Обновленный ответ 1", "theme": "Судебная власть"},
                {"text": "Новый третий вопрос?", "translation": "Ответ 3", "theme": "Судебная власть"}
            ]
            saved2, _, _ = await save_cards_to_database(updated_batch, "sudoustroystvo", "Тема", test_user, db)
            await db.commit()

            self.assertEqual(saved2, 2)
            all_cards_now = (await db.execute(select(Card).where(Card.user_id == test_user))).scalars().all()
            self.assertEqual(len(all_cards_now), 3, "Deduplication failed; duplicates were created")
            q1_card = next(c for c in all_cards_now if c.text == "Вопрос нормализации?")
            self.assertEqual(q1_card.translation, "Обновленный ответ 1")

            # Cleanup
            await db.execute(delete(Card).where(Card.user_id == test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.commit()

    async def test_8_staging_commit_syncs_canonical_entities(self):
        """Проверяет фиксацию из песочницы с не-каноническим алиасом."""
        test_user = "test_staging_commit_user"
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Card).where(Card.user_id == test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.execute(delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user))
            await db.execute(delete(PracticeItem).where(PracticeItem.user_id == test_user))
            await db.commit()

        payload = {
            "subject": "sudoustr",
            "theme": "Основы правосудия",
            "cards": [
                {
                    "text": "Каковы признаки судебной власти?",
                    "translation": "Самостоятельность, исключительность, подзаконность.",
                    "secondary_text": "Раздел 1 | Основы судебной власти",
                    "example": "Пример нормы",
                    "theme": "Основы правосудия"
                }
            ]
        }
        res = await self.client.post("/api/config/import/commit", json=payload, headers={"X-User-Id": test_user})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("subject"), "sudoustroystvo")

        async with AsyncSessionLocal() as db:
            c = (await db.execute(select(Card).where(Card.user_id == test_user))).scalars().first()
            self.assertIsNotNone(c)
            self.assertEqual(c.subject, "sudoustroystvo")

            kg = (await db.execute(select(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user))).scalars().first()
            self.assertIsNotNone(kg)
            self.assertEqual(kg.subject, "sudoustroystvo")

            # Cleanup
            await db.execute(delete(Card).where(Card.user_id == test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.execute(delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user))
            await db.execute(delete(PracticeItem).where(PracticeItem.user_id == test_user))
            await db.commit()

    async def test_9_analytics_and_export_alias_resolution(self):
        """Проверяет выдачу аналитики, экспорта и списка предметов по алиасам."""
        # 1. Analytics endpoint
        r_analytics = await self.client.get("/api/stats/dashboard?subject=sudoustr", headers={"X-User-Id": "dev_user"})
        self.assertEqual(r_analytics.status_code, 200)
        data_a = r_analytics.json()
        self.assertGreater(data_a.get("total_cards", 0), 300)

        # 2. Export endpoint
        r_export = await self.client.get("/api/data/cards/export?subject=sudoustr", headers={"X-User-Id": "dev_user"})
        self.assertEqual(r_export.status_code, 200)
        data_exp = r_export.json()
        self.assertGreater(data_exp.get("total_cards", 0), 300)
        self.assertIn(data_exp.get("subject_slug"), ("sudoustr", "sudoustroystvo"))

        # 3. GET /api/subjects
        r_subs = await self.client.get("/api/subjects", headers={"X-User-Id": "dev_user"})
        self.assertEqual(r_subs.status_code, 200)
        subs = r_subs.json()
        self.assertTrue("sudoustr" in subs or "sudoustroystvo" in subs)

    async def test_10_rename_subject_cascades_all_entities(self):
        """Проверяет каскадное переименование всех связанных сущностей предмета."""
        test_user = "test_rename_user"
        now = datetime.utcnow()

        async with AsyncSessionLocal() as db:
            p = Phrase(text="Тема переименования", subject="old_course", user_id=test_user)
            db.add(p)
            await db.flush()
            c = Card(text="Вопрос?", translation="Ответ", phrase_id=p.id, subject="old_course", user_id=test_user, next_review=now)
            kg = TopicKnowledgeGraph(user_id=test_user, subject="old_course", graph_data={"nodes": [{"id": "n1"}], "edges": []})
            import uuid
            pi = PracticeItem(item_id=str(uuid.uuid4()), user_id=test_user, subject="old_course", item_type="slot_filling", prompt="pr", options=["o"], correct_answer="o")
            pl = PracticeSessionLog(user_id=test_user, subject="old_course", score=5, total=5, percentage=100.0, created_at=now)
            db.add_all([c, kg, pi, pl])
            await db.commit()

        res = await self.client.post("/api/data/subjects/rename", json={"old_subject": "old_course", "new_subject": "new_course"}, headers={"X-User-Id": test_user})
        self.assertEqual(res.status_code, 200)

        async with AsyncSessionLocal() as db:
            cards = (await db.execute(select(Card).where(Card.user_id == test_user))).scalars().all()
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].subject, "new_course")

            phrases = (await db.execute(select(Phrase).where(Phrase.user_id == test_user))).scalars().all()
            self.assertEqual(phrases[0].subject, "new_course")

            kg = (await db.execute(select(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user))).scalars().first()
            self.assertEqual(kg.subject, "new_course")

            pi = (await db.execute(select(PracticeItem).where(PracticeItem.user_id == test_user))).scalars().first()
            self.assertEqual(pi.subject, "new_course")

            pl = (await db.execute(select(PracticeSessionLog).where(PracticeSessionLog.user_id == test_user))).scalars().first()
            self.assertEqual(pl.subject, "new_course")

            # Cleanup
            await db.execute(delete(Card).where(Card.user_id == test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.execute(delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user))
            await db.execute(delete(PracticeItem).where(PracticeItem.user_id == test_user))
            await db.execute(delete(PracticeSessionLog).where(PracticeSessionLog.user_id == test_user))
            await db.commit()

    async def test_11_single_card_delete_and_move_canonical_sync(self):
        """Проверяет удаление карты вместе с ReviewLog и перенос карты с авто-синхронизацией."""
        test_user = "test_move_del_user"
        now = datetime.utcnow()
        card_id = None

        async with AsyncSessionLocal() as db:
            p = Phrase(text="Исходная тема", subject="source_sub", user_id=test_user)
            db.add(p)
            await db.flush()
            c = Card(text="Карта для переноса", translation="Ответ", phrase_id=p.id, subject="source_sub", user_id=test_user, next_review=now)
            db.add(c)
            await db.flush()
            card_id = c.id
            rl = ReviewLog(card_id=card_id, user_id=test_user, rating=3, review_time=now)
            db.add(rl)
            await db.commit()

        # Move to alias sudoustr -> should normalize to sudoustroystvo
        r_move = await self.client.post(f"/api/management/cards/{card_id}/move", json={"target_subject": "sudoustr"}, headers={"X-User-Id": test_user})
        self.assertEqual(r_move.status_code, 200)
        self.assertEqual(r_move.json()["target_subject"], "sudoustroystvo")

        async with AsyncSessionLocal() as db:
            moved = (await db.execute(select(Card).where(Card.id == card_id))).scalars().first()
            self.assertEqual(moved.subject, "sudoustroystvo")

        # Delete single card -> verify review log deleted
        r_del = await self.client.delete(f"/api/management/cards/{card_id}", headers={"X-User-Id": test_user})
        self.assertIn(r_del.status_code, (200, 204))

        async with AsyncSessionLocal() as db:
            logs = (await db.execute(select(ReviewLog).where(ReviewLog.card_id == card_id))).scalars().all()
            self.assertEqual(len(logs), 0, "ReviewLog was not purged on single card deletion")
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.execute(delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user))
            await db.execute(delete(PracticeItem).where(PracticeItem.user_id == test_user))
            await db.commit()

    async def test_12_practice_generation_with_fewer_than_3_cards(self):
        """Проверяет генерацию практики для колоды с 1-2 картами без утечки пресетов судоустройства."""
        test_user = "test_few_cards_user"
        custom_sub = "astrophysics_small_deck"
        now = datetime.utcnow()

        async with AsyncSessionLocal() as db:
            p = Phrase(text="Астрофизика", subject=custom_sub, user_id=test_user)
            db.add(p)
            await db.flush()
            c1 = Card(
                phrase_id=p.id,
                user_id=test_user,
                subject=custom_sub,
                text="Что такое пульсар?",
                translation="Быстро вращающаяся нейтронная звезда, испускающая импульсы излучения",
                secondary_text="Релятивистская астрофизика | Компактные объекты",
                example="Открыт Джоселин Белл в 1967 году",
                next_review=now
            )
            db.add(c1)
            await db.commit()

        try:
            res = await self.client.get(f"/api/practice/session?subject={custom_sub}&count=5", headers={"X-User-Id": test_user})
            self.assertEqual(res.status_code, 200)
            items = res.json()
            self.assertGreaterEqual(len(items), 1, "Practice items were not generated for small deck (< 3 cards)")

            # Проверяем, что задания сформированы строго по нашей астрофизической карте
            all_prompts = " ".join(item.get("prompt", "") for item in items)
            self.assertTrue("пульсар" in all_prompts.lower() or "нейтронная" in all_prompts.lower(),
                            "Questions do not contain card concepts")
            # Проверяем отсутствие чужеродных судебных пресетов
            self.assertFalse("судебный акт" in all_prompts.lower() or "кассационн" in all_prompts.lower(),
                             "Court presets leaked into astrophysics subject")

            # Проверяем верификацию одного из ответов
            target_item = items[0]
            self.assertEqual(len(target_item["options"]), 4, "Options count must be exactly 4")
            # Отправка неверного ответа
            res_wrong = await self.client.post("/api/practice/verify", json={
                "item_id": target_item["id"],
                "selected_answer": "Неверный вариант ответа 999"
            }, headers={"X-User-Id": test_user})
            self.assertEqual(res_wrong.status_code, 200)
            data_wrong = res_wrong.json()
            self.assertFalse(data_wrong["correct"])
            correct_ans = data_wrong["correct_answer"]

            # Отправка верного ответа
            res_correct = await self.client.post("/api/practice/verify", json={
                "item_id": target_item["id"],
                "selected_answer": correct_ans
            }, headers={"X-User-Id": test_user})
            self.assertEqual(res_correct.status_code, 200)
            self.assertTrue(res_correct.json()["correct"])

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Card).where(Card.user_id == test_user))
                await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
                await db.execute(delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user))
                await db.execute(delete(PracticeItem).where(PracticeItem.user_id == test_user))
                await db.commit()

    async def test_13_empty_custom_subject_returns_zero_practice_items(self):
        """Проверяет, что пустой кастомный предмет возвращает пустой список заданий без навязывания чужих пресетов."""
        test_user = "test_empty_subj_user"
        res = await self.client.get("/api/practice/session?subject=empty_chemistry_course&count=5", headers={"X-User-Id": test_user})
        self.assertEqual(res.status_code, 200)
        items = res.json()
        self.assertEqual(len(items), 0, "Empty custom subject must return 0 items rather than court presets")

    async def test_14_lifecycle_sync_on_card_deletion_and_bulk_operations(self):
        """Проверяет синхронизацию графа и практики при удалении и перемещении карт между предметами."""
        test_user = "test_lifecycle_sync_user"
        sub_a = "biology_course"
        sub_b = "zoology_course"
        now = datetime.utcnow()

        async with AsyncSessionLocal() as db:
            p_a = Phrase(text="Биология", subject=sub_a, user_id=test_user)
            db.add(p_a)
            await db.flush()
            c1 = Card(phrase_id=p_a.id, user_id=test_user, subject=sub_a, text="Митохондрия", translation="Синтез АТФ", next_review=now)
            c2 = Card(phrase_id=p_a.id, user_id=test_user, subject=sub_a, text="Рибосома", translation="Биосинтез белка", next_review=now)
            db.add_all([c1, c2])
            await db.commit()
            card1_id = c1.id
            card2_id = c2.id

        try:
            # 1. Запускаем практику по sub_a, формируя PracticeItem
            res_prac = await self.client.get(f"/api/practice/session?subject={sub_a}&count=5", headers={"X-User-Id": test_user})
            self.assertEqual(res_prac.status_code, 200)
            self.assertGreaterEqual(len(res_prac.json()), 1)

            # 2. Переносим card1 в sub_b через /api/data/cards/move
            res_move = await self.client.post("/api/data/cards/move", json={"card_ids": [card1_id], "target_subject": sub_b}, headers={"X-User-Id": test_user})
            self.assertEqual(res_move.status_code, 200)

            # Проверяем, что в целевом sub_b сформировались практика/граф
            async with AsyncSessionLocal() as db:
                cards_b = (await db.execute(select(Card).where(Card.user_id == test_user, Card.subject == sub_b))).scalars().all()
                self.assertEqual(len(cards_b), 1)
                prac_b = (await db.execute(select(PracticeItem).where(PracticeItem.user_id == test_user, PracticeItem.subject == sub_b))).scalars().all()
                self.assertGreaterEqual(len(prac_b), 1, "Practice in target subject not synced on bulk move")

            # 3. Удаляем оставшуюся карту card2 из sub_a
            res_del = await self.client.delete(f"/api/management/cards/{card2_id}", headers={"X-User-Id": test_user})
            self.assertIn(res_del.status_code, (200, 204))

            # Проверяем, что в sub_a очистились и карты, и граф, и практика (так как карт 0)
            async with AsyncSessionLocal() as db:
                cards_a = (await db.execute(select(Card).where(Card.user_id == test_user, Card.subject == sub_a))).scalars().all()
                self.assertEqual(len(cards_a), 0)
                kg_a = (await db.execute(select(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user, TopicKnowledgeGraph.subject == sub_a))).scalars().all()
                self.assertEqual(len(kg_a), 0, "KnowledgeGraph in emptied subject was not cleared")
                prac_a = (await db.execute(select(PracticeItem).where(PracticeItem.user_id == test_user, PracticeItem.subject == sub_a))).scalars().all()
                self.assertEqual(len(prac_a), 0, "Practice in emptied subject was not cleared")

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Card).where(Card.user_id == test_user))
                await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
                await db.execute(delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == test_user))
                await db.execute(delete(PracticeItem).where(PracticeItem.user_id == test_user))
                await db.commit()


if __name__ == "__main__":
    unittest.main()


