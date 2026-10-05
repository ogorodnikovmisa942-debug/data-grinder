# tests/test_curriculum_and_plain_language.py
import unittest
import asyncio
from datetime import datetime
from app.database.session import AsyncSessionLocal
from app.database.models import Card, Phrase, UserSession
from app.api.endpoints.train import get_session_cards

class TestCurriculumAndPlainLanguage(unittest.IsolatedAsyncioTestCase):

    async def test_05_study_session_topological_rank_ordering(self):
        """Интеграционный тест: проверка выдачи новых карточек строго по возрастанию topological_rank."""
        test_user = "test_topological_user_42"
        test_subject = "test_curriculum_law"
        now = datetime.utcnow()

        async with AsyncSessionLocal() as db:
            phrase = Phrase(text="Тестовый каркас", subject=test_subject, user_id=test_user)
            db.add(phrase)
            await db.flush()

            # Создаем 3 карточки с перемешанными рангами (3, 1, 2)
            c3 = Card(
                phrase_id=phrase.id,
                user_id=test_user,
                subject=test_subject,
                text="Карточка третьего уровня (Обжалование)",
                translation="Определение областного суда.",
                topological_rank=3,
                organ_slug="district_court",
                layer=2,
                state=0,
                next_review=now
            )
            c1 = Card(
                phrase_id=phrase.id,
                user_id=test_user,
                subject=test_subject,
                text="Карточка первого уровня (Определение органа)",
                translation="Районный суд — основное звено.",
                topological_rank=1,
                organ_slug="district_court",
                layer=0,
                state=0,
                next_review=now
            )
            c2 = Card(
                phrase_id=phrase.id,
                user_id=test_user,
                subject=test_subject,
                text="Карточка второго уровня (Состав суда)",
                translation="Судья и 2 народных заседателя.",
                topological_rank=2,
                organ_slug="district_court",
                layer=1,
                state=0,
                next_review=now
            )
            db.add_all([c3, c1, c2])
            await db.commit()

        # Вызываем endpoint сессии обучения
        async with AsyncSessionLocal() as db:
            session_cards = await get_session_cards(
                subject=test_subject,
                mode="new",
                current_user=test_user,
                db=db
            )

        # Очищаем тестовые данные
        async with AsyncSessionLocal() as db:
            from sqlalchemy import delete
            await db.execute(delete(Card).where(Card.user_id == test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.commit()

        # Проверяем, что карты вернулись строго 1 -> 2 -> 3
        self.assertEqual(len(session_cards), 3)
        self.assertEqual(session_cards[0]["topological_rank"], 1)
        self.assertEqual(session_cards[0]["text"], "Карточка первого уровня (Определение органа)")
        self.assertEqual(session_cards[1]["topological_rank"], 2)
        self.assertEqual(session_cards[1]["text"], "Карточка второго уровня (Состав суда)")
        self.assertEqual(session_cards[2]["topological_rank"], 3)
        self.assertEqual(session_cards[2]["text"], "Карточка третьего уровня (Обжалование)")

if __name__ == "__main__":
    unittest.main()
