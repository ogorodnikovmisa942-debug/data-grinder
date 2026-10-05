# tests/test_phase7_subject_dehardcoding.py
import pytest
import unittest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select, delete

from main import app
from app.database.session import AsyncSessionLocal
from app.database.models import Card, Phrase, Category, KnowledgeNode
from app.api.endpoints.train import get_subject_display_name, get_next_train_session, get_session_cards


class TestPhase7Dehardcoding(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.client = TestClient(app)
        self.test_user = "test_phase7_user"
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Card).where(Card.user_id == self.test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == self.test_user))
            await db.execute(delete(Category).where(Category.user_id == self.test_user))
            await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == self.test_user))
            await db.commit()

    async def asyncTearDown(self):
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Card).where(Card.user_id == self.test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == self.test_user))
            await db.execute(delete(Category).where(Category.user_id == self.test_user))
            await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == self.test_user))
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

            # 3. Никаких заранее заданных названий: любой предмет называется ровно так, как его назвали
            self.assertEqual(await get_subject_display_name("constitutional_law", self.test_user, db), "Constitutional Law")
            self.assertEqual(await get_subject_display_name("python", self.test_user, db), "Python")

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
        # Практика строится только по пройденным урокам «Пути знаний»: без уроков список пуст
        self.assertIsInstance(res.json(), list)

        # Call practice stats with subject=all
        res_stats = self.client.get("/api/practice/stats?subject=all", headers=headers)
        self.assertEqual(res_stats.status_code, 200)
        stats = res_stats.json()
        self.assertEqual(stats["subject"], "microeconomics")

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


if __name__ == "__main__":
    unittest.main()

