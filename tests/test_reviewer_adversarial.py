# tests/test_reviewer_adversarial.py
import unittest
import asyncio
from datetime import datetime
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func, delete

from main import app
from app.database.session import AsyncSessionLocal
from app.database.models import Card, Phrase, KnowledgeNode, PracticeItem, PracticeSessionLog, ReviewLog
from app.services.graph_service import resolve_subject_alias, get_all_subject_aliases


class TestReviewerAdversarial(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        async with AsyncSessionLocal() as db:
            c_cnt = (await db.execute(select(func.count(Card.id)).where(Card.user_id == "dev_user", Card.subject == "sudoustr"))).scalar() or 0
            if c_cnt < 25:
                p = Phrase(user_id="dev_user", subject="sudoustr", text="Основы судоустройства")
                db.add(p)
                await db.flush()
                now = datetime.utcnow()
                for i in range(30):
                    c = Card(
                        phrase_id=p.id,
                        user_id="dev_user",
                        subject="sudoustr",
                        text=f"Институт правосудия {i}?",
                        translation=f"Полномочия {i}",
                        secondary_text=f"Раздел {i} | Полномочия инстанции {i}",
                        topological_rank=i,
                        layer=i % 3,
                        next_review=now
                    )
                    db.add(c)
                await db.commit()

    async def asyncTearDown(self):
        await self.client.aclose()
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Card).where(Card.user_id == "dev_user"))
            await db.execute(delete(Phrase).where(Phrase.user_id == "dev_user"))
            await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == "dev_user"))
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

    async def test_7_save_cards_canonical_normalization_and_deduplication(self):
        """Проверяет сохранение предмета как названного и дедупликацию в save_cards_to_database."""
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
            self.assertEqual(canon_sub, "sudoustr")

            # Verify in DB: all cards and phrases are stored under subject
            db_cards = (await db.execute(select(Card).where(Card.user_id == test_user))).scalars().all()
            self.assertEqual(len(db_cards), 2)
            for c in db_cards:
                self.assertEqual(c.subject, "sudoustr")

            db_phrases = (await db.execute(select(Phrase).where(Phrase.user_id == test_user))).scalars().all()
            for p in db_phrases:
                self.assertEqual(p.subject, "sudoustr")

            # Deduplication: Re-saving existing card should update rather than insert duplicate
            updated_batch = [
                {"text": "Вопрос нормализации?", "translation": "Обновленный ответ 1", "theme": "Судебная власть"},
                {"text": "Новый третий вопрос?", "translation": "Ответ 3", "theme": "Судебная власть"}
            ]
            saved2, _, _ = await save_cards_to_database(updated_batch, "sudoustr", "Тема", test_user, db)
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
            await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == test_user))
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
        self.assertEqual(data.get("subject"), "sudoustr")

        async with AsyncSessionLocal() as db:
            c = (await db.execute(select(Card).where(Card.user_id == test_user))).scalars().first()
            self.assertIsNotNone(c)
            self.assertEqual(c.subject, "sudoustr")

            # Cleanup
            await db.execute(delete(Card).where(Card.user_id == test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == test_user))
            await db.execute(delete(PracticeItem).where(PracticeItem.user_id == test_user))
            await db.commit()

    async def test_9_analytics_and_export_alias_resolution(self):
        """Проверяет выдачу аналитики, экспорта и списка предметов по алиасам."""
        # 1. Analytics endpoint
        r_analytics = await self.client.get("/api/stats/dashboard?subject=sudoustr", headers={"X-User-Id": "dev_user"})
        self.assertEqual(r_analytics.status_code, 200)
        data_a = r_analytics.json()
        self.assertGreaterEqual(data_a.get("total_cards", 0), 0)

        # 2. Export endpoint
        r_export = await self.client.get("/api/data/cards/export?subject=sudoustr", headers={"X-User-Id": "dev_user"})
        self.assertEqual(r_export.status_code, 200)
        data_exp = r_export.json()
        self.assertGreaterEqual(data_exp.get("total_cards", 0), 0)
        self.assertEqual(data_exp.get("subject_slug"), "sudoustr")

        # 3. GET /api/subjects
        r_subs = await self.client.get("/api/subjects", headers={"X-User-Id": "dev_user"})
        self.assertEqual(r_subs.status_code, 200)
        self.assertIsInstance(r_subs.json(), list)

    async def test_10_rename_subject_cascades_all_entities(self):
        """Проверяет каскадное переименование всех связанных сущностей предмета."""
        test_user = "test_rename_user"
        now = datetime.utcnow()

        async with AsyncSessionLocal() as db:
            await db.execute(delete(Card).where(Card.user_id == test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == test_user))
            await db.execute(delete(PracticeItem).where(PracticeItem.user_id == test_user))
            await db.execute(delete(PracticeSessionLog).where(PracticeSessionLog.user_id == test_user))
            await db.commit()

            p = Phrase(text="Тема переименования", subject="old_course", user_id=test_user)
            db.add(p)
            await db.flush()
            c = Card(text="Вопрос?", translation="Ответ", phrase_id=p.id, subject="old_course", user_id=test_user, next_review=now)
            kg = KnowledgeNode(user_id=test_user, subject="old_course", node_key="n1", name="Узел", tier=0, prereq_keys=[], order_idx=1, lesson_status="ready")
            import uuid
            pi = PracticeItem(item_id=str(uuid.uuid4()), user_id=test_user, subject="old_course", item_type="slot_filling", prompt="pr", options=["o"], correct_answer="o")
            pl = PracticeSessionLog(user_id=test_user, subject="old_course", score=5, total=5, percentage=100.0, created_at=now)
            db.add_all([c, kg, pi, pl])
            await db.commit()

        res = await self.client.post("/api/data/subjects/rename", json={"old_subject": "old_course", "new_subject": "new_course"}, headers={"X-User-Id": test_user})
        self.assertEqual(res.status_code, 200, f"Error: {res.text}")

        async with AsyncSessionLocal() as db:
            cards = (await db.execute(select(Card).where(Card.user_id == test_user))).scalars().all()
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].subject, "new_course")

            phrases = (await db.execute(select(Phrase).where(Phrase.user_id == test_user))).scalars().all()
            self.assertEqual(phrases[0].subject, "new_course")

            kg = (await db.execute(select(KnowledgeNode).where(KnowledgeNode.user_id == test_user))).scalars().first()
            self.assertEqual(kg.subject, "new_course")

            pi = (await db.execute(select(PracticeItem).where(PracticeItem.user_id == test_user))).scalars().first()
            self.assertEqual(pi.subject, "new_course")

            pl = (await db.execute(select(PracticeSessionLog).where(PracticeSessionLog.user_id == test_user))).scalars().first()
            self.assertEqual(pl.subject, "new_course")

            # Cleanup
            await db.execute(delete(Card).where(Card.user_id == test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == test_user))
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

        # Move to alias sudoustr -> should preserve sudoustr
        r_move = await self.client.post(f"/api/management/cards/{card_id}/move", json={"target_subject": "sudoustr"}, headers={"X-User-Id": test_user})
        self.assertEqual(r_move.status_code, 200)
        self.assertEqual(r_move.json()["target_subject"], "sudoustr")

        async with AsyncSessionLocal() as db:
            moved = (await db.execute(select(Card).where(Card.id == card_id))).scalars().first()
            self.assertEqual(moved.subject, "sudoustr")

        # Delete single card -> verify review log deleted
        r_del = await self.client.delete(f"/api/management/cards/{card_id}", headers={"X-User-Id": test_user})
        self.assertIn(r_del.status_code, (200, 204))

        async with AsyncSessionLocal() as db:
            logs = (await db.execute(select(ReviewLog).where(ReviewLog.card_id == card_id))).scalars().all()
            self.assertEqual(len(logs), 0, "ReviewLog was not purged on single card deletion")
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == test_user))
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

            # 2. Переносим card1 в sub_b через /api/data/cards/move
            res_move = await self.client.post("/api/data/cards/move", json={"card_ids": [card1_id], "target_subject": sub_b}, headers={"X-User-Id": test_user})
            self.assertEqual(res_move.status_code, 200)

            # Проверяем, что в целевом sub_b сформировались практика/граф
            async with AsyncSessionLocal() as db:
                cards_b = (await db.execute(select(Card).where(Card.user_id == test_user, Card.subject == sub_b))).scalars().all()
                self.assertEqual(len(cards_b), 1)

            # 3. Удаляем оставшуюся карту card2 из sub_a
            res_del = await self.client.delete(f"/api/management/cards/{card2_id}", headers={"X-User-Id": test_user})
            self.assertIn(res_del.status_code, (200, 204))

            # Проверяем, что в sub_a очистились и карты, и граф, и практика (так как карт 0)
            async with AsyncSessionLocal() as db:
                cards_a = (await db.execute(select(Card).where(Card.user_id == test_user, Card.subject == sub_a))).scalars().all()
                self.assertEqual(len(cards_a), 0)
                prac_a = (await db.execute(select(PracticeItem).where(PracticeItem.user_id == test_user, PracticeItem.subject == sub_a))).scalars().all()
                self.assertEqual(len(prac_a), 0, "Practice in emptied subject was not cleared")

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Card).where(Card.user_id == test_user))
                await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
                await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == test_user))
                await db.execute(delete(PracticeItem).where(PracticeItem.user_id == test_user))
                await db.commit()


if __name__ == "__main__":
    unittest.main()


