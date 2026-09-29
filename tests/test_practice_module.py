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
import re
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
