"""Пока нет оплаты: материал нарезается только в часы скидки DeepSeek (OFFPEAK_ONLY)."""
import asyncio
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import delete

from main import app
from app.core.config import settings
from app.database.session import AsyncSessionLocal
from app.database.models import GenerationJob

USER = "test_offpeak_only_user"
TEXT = "Гражданское право регулирует имущественные и личные неимущественные отношения. " * 300


class TestOffpeakOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def setUp(self):
        for name, value in (("OFFPEAK_ONLY", True), ("MAX_ACTIVE_JOBS_PER_USER", 100), ("MAX_JOBS_PER_HOUR", 100)):
            p = patch.object(settings, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._clean)

    def _clean(self):
        async def run():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(GenerationJob).where(GenerationJob.user_id == USER))
                await db.commit()
        asyncio.run(run())

    def _import(self, user=USER, is_deferred=False):
        res = self.client.post(
            "/api/config/import",
            headers={"X-User-Id": user},
            json={"text": TEXT, "subject": "civil_law", "is_deferred": is_deferred},
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_peak_defers_even_without_request(self):
        """В пик материал ждёт скидку, даже если пользователь не просил откладывать."""
        with patch("app.api.endpoints.management.is_deepseek_offpeak", return_value=False):
            data = self._import(is_deferred=False)
        self.assertEqual(data["status"], "queued")
        self.assertFalse(data["is_immediate"])
        self.assertIn("по МСК", data["message"])

    def test_offpeak_runs_now(self):
        """В часы скидки материал берётся в работу сразу."""
        with patch("app.api.endpoints.management.is_deepseek_offpeak", return_value=True):
            data = self._import()
        self.assertTrue(data["is_immediate"])
        self.assertTrue(data["is_offpeak"])

    def test_admin_is_not_limited(self):
        """Админ проверяет нарезку в любое время."""
        with patch.object(settings, "ADMIN_TELEGRAM_ID", USER), \
                patch("app.api.endpoints.management.is_deepseek_offpeak", return_value=False):
            data = self._import()
        self.assertTrue(data["is_immediate"])

    def test_policy_off_restores_choice(self):
        """OFFPEAK_ONLY=0 — прежнее поведение: в пик без просьбы отложить берётся сразу."""
        with patch.object(settings, "OFFPEAK_ONLY", False), \
                patch("app.api.endpoints.management.is_deepseek_offpeak", return_value=False):
            data = self._import()
        self.assertTrue(data["is_immediate"])

    def test_config_tells_form_the_schedule(self):
        """Форма загрузки узнаёт, ждать ли скидку и когда она начнётся."""
        res = self.client.get("/api/config", headers={"X-User-Id": USER})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["offpeak_only"])
        self.assertIn("offpeak_now", data)
        self.assertIn("по МСК", data["offpeak_start"])


if __name__ == "__main__":
    unittest.main()
