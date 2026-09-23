# tests/test_train_session_flow.py
import unittest
import asyncio
from datetime import datetime, timedelta
from app.database.session import AsyncSessionLocal
from app.database.models import Card, Phrase
from app.api.endpoints.train import get_session_cards

class TestTrainSessionFlow(unittest.IsolatedAsyncioTestCase):

    async def test_01_card_enriched_metadata_and_reasons(self):
        """Проверка обогащения карточек метаданными reason_type, reason_label, subject_title и interval_days."""
        test_user = "test_flow_user_01"
        test_subject = "constitutional_law"
        now = datetime.utcnow()

        async with AsyncSessionLocal() as db:
            phrase = Phrase(text="Основы конституционного строя", subject=test_subject, user_id=test_user)
            db.add(phrase)
            await db.flush()

            # 1. Новая карточка
            card_new = Card(
                phrase_id=phrase.id,
                user_id=test_user,
                subject=test_subject,
                text="Что такое суверенитет государства?",
                translation="Верховенство и независимость государственной власти внутри страны и вне ее.",
                topological_rank=1,
                organ_slug="sovereignty",
                layer=0,
                state=0,
                next_review=now
            )
            # 2. Карточка на повторении (due review)
            card_review = Card(
                phrase_id=phrase.id,
                user_id=test_user,
                subject=test_subject,
                text="Каковы признаки правового государства?",
                translation="Разделение властей, верховенство права, гарантия прав и свобод.",
                topological_rank=2,
                organ_slug="rule_of_law",
                layer=1,
                state=2, # Review
                last_review=now - timedelta(days=5),
                next_review=now - timedelta(hours=2)
            )
            db.add_all([card_new, card_review])
            await db.commit()

        try:
            # Проверяем режим "new"
            async with AsyncSessionLocal() as db:
                cards = await get_session_cards(subject=test_subject, mode="new", current_user=test_user, db=db)
                self.assertEqual(len(cards), 1)
                self.assertEqual(cards[0]["reason_type"], "new")
                self.assertEqual(cards[0]["reason_label"], "Новое понятие")
                self.assertEqual(cards[0]["reason_icon"], "school")
                self.assertEqual(cards[0]["subject_title"], "Конституционное право")

            # Проверяем режим "review"
            async with AsyncSessionLocal() as db:
                cards = await get_session_cards(subject=test_subject, mode="review", current_user=test_user, db=db)
                self.assertEqual(len(cards), 1)
                self.assertEqual(cards[0]["reason_type"], "review")
                self.assertIn("Повторение", cards[0]["reason_label"])
                self.assertEqual(cards[0]["reason_icon"], "history")
                self.assertGreaterEqual(cards[0]["interval_days"], 5)
                self.assertEqual(cards[0]["subject_title"], "Конституционное право")

        finally:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import delete
                await db.execute(delete(Card).where(Card.user_id == test_user))
                await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
                await db.commit()

    async def test_02_didactic_sequence_strict_ordering(self):
        """Проверка строгого дидактического порядка: слой (layer) -> topological_rank -> phrase_id."""
        test_user = "test_flow_user_02"
        test_subject = "sudoustr"
        now = datetime.utcnow()

        async with AsyncSessionLocal() as db:
            phrase = Phrase(text="Судебные инстанции", subject=test_subject, user_id=test_user)
            db.add(phrase)
            await db.flush()

            # Создаем намеренно в случайном порядке
            c_high = Card(
                phrase_id=phrase.id,
                user_id=test_user,
                subject=test_subject,
                text="Кассационная инстанция",
                translation="Проверка законности вступивших в силу судебных актов.",
                layer=2,
                topological_rank=10,
                state=0,
                next_review=now
            )
            c_root = Card(
                phrase_id=phrase.id,
                user_id=test_user,
                subject=test_subject,
                text="Судебная система",
                translation="Совокупность всех действующих в РФ судов.",
                layer=0,
                topological_rank=1,
                state=0,
                next_review=now
            )
            c_mid = Card(
                phrase_id=phrase.id,
                user_id=test_user,
                subject=test_subject,
                text="Апелляционная инстанция",
                translation="Повторное рассмотрение не вступивших в силу решений по существу.",
                layer=1,
                topological_rank=5,
                state=0,
                next_review=now
            )
            db.add_all([c_high, c_root, c_mid])
            await db.commit()

        try:
            async with AsyncSessionLocal() as db:
                cards = await get_session_cards(subject=test_subject, mode="new", current_user=test_user, db=db)
                self.assertEqual(len(cards), 3)
                # Карточки обязаны идти: слой 0 (Корень) -> слой 1 (Апелляция) -> слой 2 (Кассация)
                self.assertEqual(cards[0]["layer"], 0)
                self.assertEqual(cards[0]["text"], "Судебная система")
                self.assertEqual(cards[1]["layer"], 1)
                self.assertEqual(cards[1]["text"], "Апелляционная инстанция")
                self.assertEqual(cards[2]["layer"], 2)
                self.assertEqual(cards[2]["text"], "Кассационная инстанция")
        finally:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import delete
                await db.execute(delete(Card).where(Card.user_id == test_user))
                await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
                await db.commit()

    def test_03_frontend_static_contracts(self):
        """Проверка наличия компонентов интерфейса: кнопки перехода в граф, дебрифа и отключения перемешивания."""
        with open("app/static/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('id="session-debrief-container"', html)
        self.assertIn('id="card-front-graph-btn"', html)
        self.assertIn('id="card-intro-graph-btn"', html)
        self.assertIn('id="card-back-graph-btn"', html)
        self.assertIn('id="card-front-subject-badge"', html)
        self.assertIn('id="card-back-subject-badge"', html)
        self.assertIn('window.teleportCurrentCardToGraph()', html)
        self.assertIn('window.continueWithNextChunk()', html)

        with open("app/static/js/app.js", "r", encoding="utf-8") as f:
            js = f.read()

        self.assertIn("window.showSessionDebrief =", js)
        self.assertIn("window.continueWithNextChunk =", js)
        self.assertIn("window.teleportCurrentCardToGraph =", js)
        self.assertIn("window.openPracticeFromDebrief =", js)
        self.assertIn("window.openGraphFromDebrief =", js)
        # Убеждаемся, что при mode === 'new' карточки больше не перемешиваются вслепую
        self.assertIn("if (mode === 'cram')", js)

    async def test_04_session_cards_sudoust_alias_matching(self):
        """Проверка того, что карточки с subject='sudoustr' корректно находятся при запросе subject='sudoust'."""
        test_user = "test_alias_user_04"
        now = datetime.utcnow()
        async with AsyncSessionLocal() as db:
            phrase = Phrase(text="Органы правосудия", subject="sudoustr", user_id=test_user)
            db.add(phrase)
            await db.flush()

            card = Card(
                phrase_id=phrase.id,
                user_id=test_user,
                subject="sudoustr",
                text="Что такое подсудность?",
                translation="Распределение дел между судами определенной компетенции.",
                topological_rank=1,
                layer=0,
                state=0,
                next_review=now
            )
            db.add(card)
            await db.commit()

        try:
            async with AsyncSessionLocal() as db:
                cards = await get_session_cards(subject="sudoust", mode="new", current_user=test_user, db=db)
                self.assertEqual(len(cards), 1)
                self.assertEqual(cards[0]["text"], "Что такое подсудность?")
                self.assertEqual(cards[0]["subject_title"], "Судоустройство РФ")
        finally:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import delete
                await db.execute(delete(Card).where(Card.user_id == test_user))
                await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
                await db.commit()

if __name__ == "__main__":
    unittest.main()
