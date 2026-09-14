# tests/test_staging_deletion.py
import json
import unittest
import asyncio
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy import select, delete

from main import app
from app.database.session import AsyncSessionLocal
from app.database.models import GenerationJob, Card

class TestStagingDeletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()
        cls.user_id = "test_staging_del_user"
        cls.other_user = "other_staging_del_user"

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def run_async(self, coro):
        return asyncio.run(coro)

    def setUp(self):
        async def cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(GenerationJob).filter(GenerationJob.user_id.in_([self.user_id, self.other_user])))
                await db.execute(delete(Card).filter(Card.user_id.in_([self.user_id, self.other_user])))
                await db.commit()
        self.run_async(cleanup())

    def tearDown(self):
        async def cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(GenerationJob).filter(GenerationJob.user_id.in_([self.user_id, self.other_user])))
                await db.execute(delete(Card).filter(Card.user_id.in_([self.user_id, self.other_user])))
                await db.commit()
        self.run_async(cleanup())

    def _create_job(self, user_id, status="ready_for_review", cards=None):
        if cards is None:
            cards = [
                {"text": "Вопрос 1", "translation": "Ответ 1", "secondary_text": "Подсказка 1"},
                {"text": "Вопрос 2", "translation": "Ответ 2", "secondary_text": "Подсказка 2"}
            ]
        async def create():
            async with AsyncSessionLocal() as db:
                job = GenerationJob(
                    user_id=user_id,
                    subject="law",
                    theme="Конституционное право",
                    raw_text="Исходный текст лекции",
                    status=status,
                    cards_count=len(cards),
                    result_cards_json=json.dumps(cards, ensure_ascii=False)
                )
                db.add(job)
                await db.commit()
                await db.refresh(job)
                return job.id
        return self.run_async(create())

    def test_01_delete_staging_job_success(self):
        """Проверка успешного удаления колоды из песочницы через DELETE /api/config/import/staging/job/{job_id}."""
        job_id = self._create_job(self.user_id, status="ready_for_review")

        # Проверяем, что карточки доступны
        get_res = self.client.get(
            f"/api/config/import/staging/job/{job_id}",
            headers={"X-User-Id": self.user_id}
        )
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(len(get_res.json()["cards"]), 2)

        # Удаляем колоду из песочницы
        del_res = self.client.delete(
            f"/api/config/import/staging/job/{job_id}",
            headers={"X-User-Id": self.user_id}
        )
        self.assertEqual(del_res.status_code, 200)
        data = del_res.json()
        self.assertEqual(data["status"], "success")

        # Проверяем, что в БД задача физически удалена
        async def check_db():
            async with AsyncSessionLocal() as db:
                stmt = select(GenerationJob).filter(GenerationJob.id == job_id)
                res = await db.execute(stmt)
                return res.scalar_one_or_none()

        job_in_db = self.run_async(check_db())
        self.assertIsNone(job_in_db)

        # Повторный запрос должен вернуть 404
        del_res_again = self.client.delete(
            f"/api/config/import/staging/job/{job_id}",
            headers={"X-User-Id": self.user_id}
        )
        self.assertEqual(del_res_again.status_code, 404)

    def test_02_delete_staging_job_not_found_and_permissions(self):
        """Проверка 404 при попытке удалить несуществующую задачу или задачу другого пользователя."""
        # Несуществующий ID
        res = self.client.delete(
            "/api/config/import/staging/job/999999",
            headers={"X-User-Id": self.user_id}
        )
        self.assertEqual(res.status_code, 404)

        # Задача другого пользователя
        other_job_id = self._create_job(self.other_user, status="ready_for_review")
        res_other = self.client.delete(
            f"/api/config/import/staging/job/{other_job_id}",
            headers={"X-User-Id": self.user_id}
        )
        self.assertEqual(res_other.status_code, 404)

    def test_03_queue_cancel_ready_for_review_job(self):
        """Проверка отмены/удаления задачи со статусом ready_for_review через DELETE /api/config/import/queue/{job_id}."""
        job_id = self._create_job(self.user_id, status="ready_for_review")

        # Отменяем задачу
        del_res = self.client.delete(
            f"/api/config/import/queue/{job_id}",
            headers={"X-User-Id": self.user_id}
        )
        self.assertEqual(del_res.status_code, 200)
        data = del_res.json()
        self.assertEqual(data["status"], "success")

        # Проверяем в БД: статус должен стать 'cancelled'
        async def check_db():
            async with AsyncSessionLocal() as db:
                stmt = select(GenerationJob).filter(GenerationJob.id == job_id)
                res = await db.execute(stmt)
                return res.scalar_one_or_none()

        job = self.run_async(check_db())
        self.assertIsNotNone(job)
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.error_message, "Отменено пользователем")

        # Повторная отмена уже отмененной задачи возвращает 400
        del_res_again = self.client.delete(
            f"/api/config/import/queue/{job_id}",
            headers={"X-User-Id": self.user_id}
        )
        self.assertEqual(del_res_again.status_code, 400)

    def test_04_queue_cancel_completed_or_failed_job_fails(self):
        """Задачи в статусе completed или failed не могут быть отменены через очередь."""
        comp_id = self._create_job(self.user_id, status="completed")
        res_comp = self.client.delete(
            f"/api/config/import/queue/{comp_id}",
            headers={"X-User-Id": self.user_id}
        )
        self.assertEqual(res_comp.status_code, 400)

        failed_id = self._create_job(self.user_id, status="failed")
        res_failed = self.client.delete(
            f"/api/config/import/queue/{failed_id}",
            headers={"X-User-Id": self.user_id}
        )
        self.assertEqual(res_failed.status_code, 400)

    def test_05_commit_staging_cards_with_alternate_field_names(self):
        """Проверка фиксации карточек с альтернативными именами полей (front/back/hint/question/answer)."""
        payload = {
            "subject": "law",
            "theme": "Тестовый блок",
            "cards": [
                {
                    "front": "Что такое конституция?",
                    "back": "Основной закон государства",
                    "hint": "Высшая юридическая сила"
                },
                {
                    "question": "Что такое презумпция невиновности?",
                    "answer": "Принцип судопроизводства",
                    "secondary": "Бремя доказывания на обвинителе"
                }
            ]
        }
        res = self.client.post(
            "/api/config/import/commit",
            json=payload,
            headers={"X-User-Id": self.user_id}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["cards_count"], 2)

        # Проверяем, что карточки в БД имеют корректные text, translation и secondary_text
        async def verify_cards():
            async with AsyncSessionLocal() as db:
                stmt = select(Card).filter(Card.user_id == self.user_id).order_by(Card.id.asc())
                res_db = await db.execute(stmt)
                return res_db.scalars().all()

        saved_cards = self.run_async(verify_cards())
        self.assertEqual(len(saved_cards), 2)
        self.assertEqual(saved_cards[0].text, "Что такое конституция?")
        self.assertEqual(saved_cards[0].translation, "Основной закон государства")
        self.assertEqual(saved_cards[0].secondary_text, "Высшая юридическая сила")
        self.assertEqual(saved_cards[1].text, "Что такое презумпция невиновности?")
        self.assertEqual(saved_cards[1].translation, "Принцип судопроизводства")
        self.assertEqual(saved_cards[1].secondary_text, "Бремя доказывания на обвинителе")

    def test_06_staging_job_deletion_removes_from_queue(self):
        """Проверка, что удаление колоды из песочницы убирает задачу из очереди GET /api/config/import/queue."""
        job_id = self._create_job(self.user_id, status="ready_for_review")

        # Проверяем, что задача есть в очереди
        q_res = self.client.get("/api/config/import/queue", headers={"X-User-Id": self.user_id})
        self.assertEqual(q_res.status_code, 200)
        job_ids = [j["id"] for j in q_res.json()["jobs"]]
        self.assertIn(job_id, job_ids)

        # Удаляем задачу из песочницы
        del_res = self.client.delete(f"/api/config/import/staging/job/{job_id}", headers={"X-User-Id": self.user_id})
        self.assertEqual(del_res.status_code, 200)

        # Проверяем, что задачи больше нет в очереди
        q_res2 = self.client.get("/api/config/import/queue", headers={"X-User-Id": self.user_id})
        self.assertEqual(q_res2.status_code, 200)
        job_ids2 = [j["id"] for j in q_res2.json()["jobs"]]
        self.assertNotIn(job_id, job_ids2)
