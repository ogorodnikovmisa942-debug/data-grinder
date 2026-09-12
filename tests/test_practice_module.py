# tests/test_practice_module.py
"""
Unit and Integration Tests for Milestone 3: Autonomous Practice Module (Requirement R5).
Validates:
1. GET /api/practice/session returns structured interactive items (no leaked answers).
2. POST /api/practice/verify validates answers and returns explanations + gold standards.
3. Fallback to preset high-yield seeds when user has zero or few cards.
4. Dynamic synthesis of situational, contrast-pair, and slot-filling exercises from real cards.
"""

import unittest
import asyncio
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy import select, delete

from main import app
from app.database.session import AsyncSessionLocal
from app.database.models import PracticeItem, Phrase, Card, Category, PracticeSessionLog


class TestPracticeModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()
        cls.test_user = "test_practice_user"
        cls.test_subject = "sudoustroystvo"

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def setUp(self):
        async def cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(PracticeItem).where(PracticeItem.user_id == self.test_user))
                await db.execute(delete(PracticeSessionLog).where(PracticeSessionLog.user_id == self.test_user))
                await db.commit()
        asyncio.run(cleanup())

    def tearDown(self):
        async def cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(PracticeItem).where(PracticeItem.user_id == self.test_user))
                await db.execute(delete(PracticeSessionLog).where(PracticeSessionLog.user_id == self.test_user))
                await db.commit()
        asyncio.run(cleanup())

    def test_01_get_practice_session_seed_fallback(self):
        """Проверка получения сессии практики при отсутствии карт (fallback на high-yield seed)."""
        headers = {"X-User-Id": self.test_user}
        res = self.client.get(f"/api/practice/session?subject={self.test_subject}&count=5", headers=headers)
        self.assertEqual(res.status_code, 200)
        items = res.json()
        self.assertIsInstance(items, list)
        self.assertGreaterEqual(len(items), 3)

        for item in items:
            self.assertIn("id", item)
            self.assertIn("type", item)
            self.assertIn(item["type"], ("situational", "contrast_pair", "slot_filling"))
            self.assertIn("prompt", item)
            self.assertIn("options", item)
            self.assertGreaterEqual(len(item["options"]), 2)
            self.assertEqual(item["subject"], self.test_subject)
            # Правильный ответ не должен раскрываться клиенту в списке заданий
            self.assertNotIn("correct_answer", item)

    def test_02_verify_practice_answer_correct_and_incorrect(self):
        """Проверка верификации ответов: корректный ответ, некорректный ответ, объяснение и золотой стандарт."""
        headers = {"X-User-Id": self.test_user}
        session_res = self.client.get(f"/api/practice/session?subject={self.test_subject}&count=3", headers=headers)
        self.assertEqual(session_res.status_code, 200)
        items = session_res.json()
        target_item = items[0]
        item_id = target_item["id"]

        # 1. Отправляем заведомо неверный ответ
        verify_wrong = self.client.post(
            "/api/practice/verify",
            headers=headers,
            json={"item_id": item_id, "selected_answer": "Неверный случайный ответ 12345"}
        )
        self.assertEqual(verify_wrong.status_code, 200)
        data_wrong = verify_wrong.json()
        self.assertFalse(data_wrong["correct"])
        self.assertIsNotNone(data_wrong["correct_answer"])
        self.assertIsNotNone(data_wrong["explanation"])

        # 2. Отправляем истинный правильный ответ, полученный из первого запроса
        real_correct_answer = data_wrong["correct_answer"]
        verify_correct = self.client.post(
            "/api/practice/verify",
            headers=headers,
            json={"item_id": item_id, "selected_answer": real_correct_answer}
        )
        self.assertEqual(verify_correct.status_code, 200)
        data_correct = verify_correct.json()
        self.assertTrue(data_correct["correct"])
        self.assertEqual(data_correct["selected"], real_correct_answer)
        self.assertIsNotNone(data_correct["gold_standard"])

    def test_03_dynamic_practice_generation_from_user_cards(self):
        """Проверка синтеза практических заданий из реальных карточек пользователя."""
        custom_sub = "civil_law_practice_test"
        
        async def populate_cards():
            async with AsyncSessionLocal() as db:
                cat = Category(name="Гражданское право", user_id=self.test_user)
                db.add(cat)
                await db.flush()

                phrase = Phrase(text="Обязательства", subject=custom_sub, user_id=self.test_user)
                db.add(phrase)
                await db.flush()

                now_ts = datetime.utcnow()
                c1 = Card(
                    phrase_id=phrase.id,
                    category_id=cat.id,
                    user_id=self.test_user,
                    subject=custom_sub,
                    next_review=now_ts,
                    text="Срок исковой давности по общему правилу составляет [...] года со дня нарушения права.",
                    translation="3 года",
                    example="ст. 196 ГК РФ",
                    secondary_text="Общий срок исковой давности",
                    state=0
                )
                c2 = Card(
                    phrase_id=phrase.id,
                    category_id=cat.id,
                    user_id=self.test_user,
                    subject=custom_sub,
                    next_review=now_ts,
                    text="Чем ничтожная сделка отличается от оспоримой?",
                    translation="Ничтожная сделка недействительна с момента совершения независимо от признания ее судом",
                    example="ст. 166 ГК РФ",
                    secondary_text="Виды недействительных сделок",
                    state=0
                )
                c3 = Card(
                    phrase_id=phrase.id,
                    category_id=cat.id,
                    user_id=self.test_user,
                    subject=custom_sub,
                    next_review=now_ts,
                    text="Должник не исполнил обязательство из-за непреодолимой силы (форс-мажор). Освобождается ли он от возмещения убытков?",
                    translation="Да, лицо освобождается от ответственности, если докажет непреодолимую силу",
                    example="п. 3 ст. 401 ГК РФ",
                    secondary_text="Ответственность за нарушение обязательств",
                    state=0
                )
                c4 = Card(
                    phrase_id=phrase.id,
                    category_id=cat.id,
                    user_id=self.test_user,
                    subject=custom_sub,
                    next_review=now_ts,
                    text="Каков предельный срок исковой давности при любых обстоятельствах?",
                    translation="10 лет со дня нарушения права",
                    example="п. 2 ст. 196 ГК РФ",
                    secondary_text="Пресекательный срок",
                    state=0
                )
                db.add_all([c1, c2, c3, c4])
                await db.commit()

        asyncio.run(populate_cards())

        try:
            headers = {"X-User-Id": self.test_user}
            res = self.client.get(f"/api/practice/session?subject={custom_sub}&count=4", headers=headers)
            self.assertEqual(res.status_code, 200)
            items = res.json()
            self.assertGreaterEqual(len(items), 3)

            # Проверяем, что синтезировались задания по нашим карточкам
            prompts = " ".join(item["prompt"] for item in items)
            self.assertTrue("исковой давности" in prompts or "ничтожная сделка" in prompts)

            types = {item["type"] for item in items}
            self.assertTrue(len(types.intersection({"situational", "contrast_pair", "slot_filling"})) >= 1)

        finally:
            async def cleanup_cards():
                async with AsyncSessionLocal() as db:
                    await db.execute(delete(Card).where(Card.phrase_id.in_(
                        select(Phrase.id).where(Phrase.user_id == self.test_user)
                    )))
                    await db.execute(delete(Phrase).where(Phrase.user_id == self.test_user))
                    await db.execute(delete(Category).where(Category.user_id == self.test_user))
                    await db.commit()
            asyncio.run(cleanup_cards())

    def test_04_practice_completion_and_stats(self):
        """Проверка сохранения сессии практики и получения статистики прогресса."""
        headers = {"X-User-Id": self.test_user}
        subject = "sudoustroystvo"

        # 1. Проверяем начальное состояние
        res_initial = self.client.get(f"/api/practice/stats?subject={subject}", headers=headers)
        self.assertEqual(res_initial.status_code, 200)
        data_initial = res_initial.json()
        self.assertFalse(data_initial["today_completed"])
        self.assertEqual(data_initial["today_count"], 0)

        # 2. Фиксируем завершение сессии с результатом 8 из 10
        payload = {
            "subject": subject,
            "score": 8,
            "total": 10
        }
        res_complete = self.client.post("/api/practice/complete", headers=headers, json=payload)
        self.assertEqual(res_complete.status_code, 200)
        data_complete = res_complete.json()
        self.assertTrue(data_complete["success"])
        self.assertEqual(data_complete["score"], 8)
        self.assertEqual(data_complete["total"], 10)
        self.assertEqual(data_complete["percentage"], 80.0)

        # 3. Проверяем статистику после сохранения
        res_after = self.client.get(f"/api/practice/stats?subject={subject}", headers=headers)
        self.assertEqual(res_after.status_code, 200)
        data_after = res_after.json()
        self.assertTrue(data_after["today_completed"])
        self.assertEqual(data_after["today_count"], 1)
        self.assertEqual(data_after["last_score"], 8)
        self.assertEqual(data_after["last_total"], 10)
        self.assertEqual(data_after["last_percentage"], 80.0)


if __name__ == "__main__":
    unittest.main()
