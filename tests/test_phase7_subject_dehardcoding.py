# tests/test_phase7_subject_dehardcoding.py
import pytest
import unittest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select, delete

from main import app
from app.database.session import AsyncSessionLocal
from app.database.models import Card, Phrase, Category, TopicKnowledgeGraph
from app.api.endpoints.train import get_subject_display_name, get_next_train_session, get_session_cards


class TestPhase7Dehardcoding(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.client = TestClient(app)
        self.test_user = "test_phase7_user"
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Card).where(Card.user_id == self.test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == self.test_user))
            await db.execute(delete(Category).where(Category.user_id == self.test_user))
            await db.execute(delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == self.test_user))
            await db.commit()

    async def asyncTearDown(self):
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Card).where(Card.user_id == self.test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == self.test_user))
            await db.execute(delete(Category).where(Category.user_id == self.test_user))
            await db.execute(delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == self.test_user))
            await db.commit()

    async def test_01_dynamic_subject_display_name_helper(self):
        """get_subject_display_name resolves dynamically from Category, Phrase, known titles, and title-case."""
        async with AsyncSessionLocal() as db:
            # 1. Resolves from Category
            cat = Category(name="Quantum Physics (Advanced)", user_id=self.test_user)
            db.add(cat)
            await db.flush()

            name_cat = await get_subject_display_name("quantum", self.test_user, db)
            self.assertEqual(name_cat, "Quantum Physics (Advanced)")

            # 2. Resolves from Phrase if no Category match
            phrase = Phrase(text="Основы биохимии", subject="biochemistry", user_id=self.test_user)
            db.add(phrase)
            await db.commit()

            name_phrase = await get_subject_display_name("biochemistry", self.test_user, db)
            self.assertEqual(name_phrase, "Основы биохимии")

            # 3. Resolves known predefined titles
            self.assertEqual(await get_subject_display_name("sudoustr", self.test_user, db), "Sudoustr")
            self.assertEqual(await get_subject_display_name("sudoustroystvo", self.test_user, db), "Sudoustroystvo")
            self.assertEqual(await get_subject_display_name("constitutional_law", self.test_user, db), "Конституционное право")

            # 4. Fallback to Title Case for unknown slugs
            self.assertEqual(await get_subject_display_name("machine_learning_algorithms", self.test_user, db), "Machine Learning Algorithms")

    async def test_02_practice_session_and_stats_dynamic_fallback(self):
        """When subject is empty or 'all', practice queries latest active subject."""
        headers = {"X-User-Id": self.test_user}

        # User has a card in custom subject 'microeconomics'
        async with AsyncSessionLocal() as db:
            p = Phrase(text="Спрос и предложение", subject="microeconomics", user_id=self.test_user)
            db.add(p)
            await db.flush()
            c = Card(
                phrase_id=p.id,
                user_id=self.test_user,
                subject="microeconomics",
                text="Что такое эластичность спроса?",
                translation="Степень реакции величины спроса на изменение цены.",
                state=2,
                next_review=datetime.utcnow() + timedelta(days=2)
            )
            db.add(c)
            await db.commit()

        # Call practice session with subject=None or subject=all
        res = self.client.get("/api/practice/session", headers=headers)
        self.assertEqual(res.status_code, 200)
        items = res.json()
        self.assertGreater(len(items), 0)
        # Should pick active subject 'microeconomics'
        self.assertEqual(items[0]["subject"], "microeconomics")

        # Call practice stats with subject=all
        res_stats = self.client.get("/api/practice/stats?subject=all", headers=headers)
        self.assertEqual(res_stats.status_code, 200)
        stats = res_stats.json()
        self.assertEqual(stats["subject"], "microeconomics")

    async def test_03_knowledge_graph_empty_state_contract(self):
        """Empty user with no cards gets is_empty=True from get_knowledge_graph."""
        empty_uid = "empty_test_user_p7"
        headers = {"X-User-Id": empty_uid}

        res = self.client.get("/api/knowledge-graph", headers=headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("is_empty"))
        self.assertEqual(data["subject"], "")
        self.assertEqual(data["graph_data"]["nodes"], [])

    async def test_04_get_next_train_session_helper(self):
        """get_next_train_session dynamically falls back to active subject."""
        async with AsyncSessionLocal() as db:
            p = Phrase(text="Теория игр", subject="game_theory", user_id=self.test_user)
            db.add(p)
            await db.flush()
            c = Card(
                phrase_id=p.id,
                user_id=self.test_user,
                subject="game_theory",
                text="Равновесие Нэша",
                translation="Профиль стратегий, где ни один игрок не может увеличить выигрыш в одностороннем порядке.",
                state=0,
                next_review=datetime.utcnow()
            )
            db.add(c)
            await db.commit()

            cards = await get_next_train_session(subject=None, mode="new", current_user=self.test_user, db=db)
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0]["subject"], "game_theory")

    def test_05_frontend_empty_state_and_demo_button_contract(self):
        """Frontend 07_graph.js and app.js have dehardcoded empty-state and demo course button."""
        with open("app/static/js/modules/07_graph.js", "r", encoding="utf-8") as f:
            module_code = f.read()

        with open("app/static/js/app.js", "r", encoding="utf-8") as f:
            bundled_code = f.read()

        for code in (module_code, bundled_code):
            self.assertIn("У вас пока нет колод для построения графа знаний", code)
            self.assertNotIn("[ Открыть демо-курс (Судоустройство РФ) ]", code)
            self.assertIn("Сначала выберите предмет для построения графа.", code)

    async def test_06_rename_subject_cleans_legacy_phrase_text_and_no_sudoust_display(self):
        """Проверяет, что переименование очищает устаревший Phrase.text и не подставляет 'Судоустройство РФ'."""
        headers = {"X-User-Id": self.test_user}
        async with AsyncSessionLocal() as db:
            p = Phrase(text="Судоустройство: Основной курс", subject="sudoust", user_id=self.test_user)
            db.add(p)
            await db.flush()
            c = Card(
                phrase_id=p.id,
                user_id=self.test_user,
                subject="sudoust",
                text="Статус судей?",
                translation="Носители судебной власти",
                state=0,
                next_review=datetime.utcnow()
            )
            db.add(c)
            await db.commit()

        # 1. Вызов rename с тем же именем (old_subject == new_subject == 'sudoust')
        rename_res = self.client.post("/api/data/subjects/rename", json={"old_subject": "sudoust", "new_subject": "sudoust"}, headers=headers)
        self.assertEqual(rename_res.status_code, 200)

        # 2. Проверяем Phrase.text в базе: очистился от 'Судоустройство...' и стал 'sudoust'
        async with AsyncSessionLocal() as db:
            p_check = (await db.execute(select(Phrase).where(Phrase.user_id == self.test_user))).scalar_one_or_none()
            self.assertIsNotNone(p_check)
            self.assertEqual(p_check.text, "sudoust")

        # 3. GET /api/session возвращает clean title, а не 'Судоустройство РФ'
        train_res = self.client.get("/api/session?subject=sudoust&mode=new", headers=headers)
        self.assertEqual(train_res.status_code, 200)
        cards = train_res.json()
        self.assertGreater(len(cards), 0)
        self.assertNotEqual(cards[0]["subject_title"], "Судоустройство РФ")
        self.assertEqual(cards[0]["subject_title"].lower(), "sudoust")

        # 4. GET /api/stats/dashboard не содержит 'Судоустройство РФ'
        stats_res = self.client.get("/api/stats/dashboard?subject=sudoust", headers=headers)
        self.assertEqual(stats_res.status_code, 200)
        breakdown = stats_res.json().get("breakdown", [])
        self.assertGreater(len(breakdown), 0)
        self.assertEqual(breakdown[0]["label"], "sudoust")

        # 5. GET /api/data/cards/export не содержит 'Судоустройство: Основной курс'
        export_res = self.client.get("/api/data/cards/export?subject=sudoust", headers=headers)
        self.assertEqual(export_res.status_code, 200)
        exp_data = export_res.json()
        self.assertEqual(exp_data["phrase_title"], "sudoust")


if __name__ == "__main__":
    unittest.main()

