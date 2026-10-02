# tests/test_13_deepseek_prompt_caching_and_atomic_rules.py
"""
DeepSeek-клиент «Пути знаний», контракт кэшируемого промпта и админский переключатель модели.
1. PATH_BUILDER_SYSTEM_PROMPT: статичный, длиннее порога кэширования, содержит атомарные правила карточек.
2. call_deepseek: JSON Mode, сырой JSON, метрики кэша и стоимости, fallback модели, обрезанный ответ.
3. Админский переключатель модели и клавиатура бота.
"""

import unittest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from main import app
from app.core.config import settings
from app.services.ai_gateway import (
    PATH_BUILDER_SYSTEM_PROMPT,
    LLMOutputTruncated,
    call_deepseek,
)
from app.api.endpoints.admin import set_active_ai_provider
from bot import build_admin_keyboard, render_admin_dashboard_text, get_admin_dashboard_data


class TestPromptCachingAndAtomicRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker_patcher = patch("app.services.generation_worker.claim_next_pending_job", return_value=None)
        cls.worker_patcher.start()
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()
        cls.orig_provider = getattr(settings, "AI_PROVIDER", "deepseek")
        cls.orig_model = getattr(settings, "DEEPSEEK_MODEL", "deepseek-flash")

    @classmethod
    def tearDownClass(cls):
        settings.AI_PROVIDER = cls.orig_provider
        settings.DEEPSEEK_MODEL = cls.orig_model
        cls.client_cm.__exit__(None, None, None)
        cls.worker_patcher.stop()

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_01_path_builder_prompt_caching_contract(self):
        """Промпт статичен, длиннее порога кэширования DeepSeek и сохраняет атомарные правила карточек."""
        self.assertGreater(len(PATH_BUILDER_SYSTEM_PROMPT), 5000)
        self.assertNotIn("{", PATH_BUILDER_SYSTEM_PROMPT.split("PART A")[0])
        for rule in (
            "Minimum Information Principle",
            "Absolute Prohibition of Lists & Enumerations",
            "Zero-Spoiler Law",
            "No binary Yes/No cards",
            "Anti-giveaway",
            'TASK "MAP"',
            'TASK "NODE_PACK"',
            "B3. DISTRACTORS",
        ):
            self.assertIn(rule, PATH_BUILDER_SYSTEM_PROMPT)

    def test_02_call_deepseek_mocked_success(self):
        """Проверка вызова call_deepseek с эмуляцией ответа DeepSeek API."""
        orig_key = settings.DEEPSEEK_API_KEY
        settings.DEEPSEEK_API_KEY = "sk-test-deepseek-key-12345"
        try:
            mock_response_json = {
                "id": "chatcmpl-ds-test-01",
                "object": "chat.completion",
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"domain":"law","slug":"test_sub","title":"Тестовый блок","c":[{"t":"Что проверяет суд кассационной инстанции?","s":"ГПК РФ | Полномочия кассации","d":"Законность вступивших в силу судебных актов.","e":"Кассация проверяет правильность применения норм права.","l":"easy","h":"Кассация"}]}'
                        },
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": 1250,
                    "completion_tokens": 180,
                    "prompt_cache_hit_tokens": 1024,
                    "prompt_cache_miss_tokens": 226
                }
            }

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response_json

            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
                unpacked, meta = self.run_async(call_deepseek(
                    user_prompt="Тестовый вопрос",
                    system_instruction=PATH_BUILDER_SYSTEM_PROMPT
                ))

                mock_post.assert_called_once()
                call_args = mock_post.call_args
                url = call_args[0][0]
                headers = call_args[1]["headers"]
                json_payload = call_args[1]["json"]

                self.assertIn("/chat/completions", url)
                self.assertEqual(headers["Authorization"], "Bearer sk-test-deepseek-key-12345")
                self.assertEqual(json_payload["model"], "deepseek-flash")
                self.assertEqual(json_payload["response_format"], {"type": "json_object"})

                self.assertEqual(json_payload["messages"][0]["content"], PATH_BUILDER_SYSTEM_PROMPT)
                self.assertEqual(unpacked["c"][0]["d"], "Законность вступивших в силу судебных актов.")
                self.assertEqual(meta["cache_hit_tokens"], 1024)
                self.assertGreater(meta["cost_usd"], 0)

                self.assertTrue(meta["cache_hit"])
                self.assertEqual(meta["prompt_tokens"], 1250)
                self.assertEqual(meta["completion_tokens"], 180)
        finally:
            settings.DEEPSEEK_API_KEY = orig_key

    def test_03_call_deepseek_fallback_on_404(self):
        """Проверка автоматического fallback на 'deepseek-v4-pro' при ошибке 404/400 неизвестной модели."""
        orig_key = settings.DEEPSEEK_API_KEY
        orig_model = settings.DEEPSEEK_MODEL
        settings.DEEPSEEK_API_KEY = "sk-test-key"
        settings.DEEPSEEK_MODEL = "deepseek-experimental-custom"
        try:
            resp_404 = MagicMock()
            resp_404.status_code = 404

            resp_200 = MagicMock()
            resp_200.status_code = 200
            resp_200.json.return_value = {
                "choices": [{"message": {"content": '{"cards":[{"text":"Вопрос","translation":"Ответ"}]}'}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}
            }

            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=[resp_404, resp_200]) as mock_post:
                unpacked, meta = self.run_async(call_deepseek("Запрос", system_instruction="SYS"))
                self.assertEqual(mock_post.call_count, 2)
                second_call_model = mock_post.call_args_list[1][1]["json"]["model"]
                self.assertEqual(second_call_model, "deepseek-v4-pro")
                self.assertEqual(meta["model_resolved"], "deepseek-v4-pro")
        finally:
            settings.DEEPSEEK_API_KEY = orig_key
            settings.DEEPSEEK_MODEL = orig_model

    def test_04_call_deepseek_missing_api_key(self):
        """Проверка генерации ValueError при отсутствующем DEEPSEEK_API_KEY."""
        orig_key = settings.DEEPSEEK_API_KEY
        settings.DEEPSEEK_API_KEY = ""
        try:
            with self.assertRaises(ValueError) as ctx:
                self.run_async(call_deepseek("тест", system_instruction="SYS"))
            self.assertIn("DEEPSEEK_API_KEY", str(ctx.exception))
        finally:
            settings.DEEPSEEK_API_KEY = orig_key

    def test_05_admin_switch_ai_provider_api(self):
        """Проверка REST эндпоинтов администратора для получения статуса и переключения модели DeepSeek."""
        headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

        # 1. Проверка GET /api/admin/ai-provider без токена -> 403
        r_unauth = self.client.get("/api/admin/ai-provider")
        self.assertEqual(r_unauth.status_code, 403)

        # 2. Проверка GET /api/admin/ai-provider с токеном -> 200
        r_status = self.client.get("/api/admin/ai-provider", headers=headers)
        self.assertEqual(r_status.status_code, 200)
        data = r_status.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["provider"], "deepseek")
        self.assertIn("deepseek", data)

        # 3. Переключение модели на deepseek-chat через POST /api/admin/switch-ai-provider
        r_switch = self.client.post(
            "/api/admin/switch-ai-provider",
            headers=headers,
            json={"provider": "deepseek", "model": "deepseek-chat"}
        )
        self.assertEqual(r_switch.status_code, 200)
        resp_data = r_switch.json()
        self.assertEqual(resp_data["model"], "deepseek-chat")
        self.assertEqual(settings.DEEPSEEK_MODEL, "deepseek-chat")

        # 4. Переключение обратно на deepseek-flash
        r_switch_flash = self.client.post(
            "/api/admin/switch-ai-provider",
            headers=headers,
            json={"provider": "deepseek", "model": "deepseek-flash"}
        )
        self.assertEqual(r_switch_flash.status_code, 200)
        self.assertEqual(settings.DEEPSEEK_MODEL, "deepseek-flash")

        # 5. Ошибка при передаче невалидного провайдера
        r_invalid = self.client.post(
            "/api/admin/switch-ai-provider",
            headers=headers,
            json={"provider": "unknown_provider"}
        )
        self.assertEqual(r_invalid.status_code, 400)

    def test_08_telegram_bot_admin_keyboard_and_dashboard(self):
        """Проверка отображения кнопки смены модели в клавиатуре бота и текста в дашборде."""
        kb_ds = build_admin_keyboard(phase=1, ai_model="deepseek-flash")
        kb_texts_ds = [btn.text for row in kb_ds.inline_keyboard for btn in row]
        self.assertTrue(any("deepseek-flash" in t for t in kb_texts_ds), "Должна быть кнопка с текущей моделью")

        dash_ds = render_admin_dashboard_text({
            "phase": 1, "participants": 5, "invites_active": 3, "cards": 120,
            "reviews": 340, "outliers": 2, "pending_jobs": 0,
            "ai_provider": "deepseek", "ai_model": "deepseek-flash", "has_key": True
        })
        self.assertIn("DeepSeek", dash_ds)
        self.assertIn("deepseek-flash", dash_ds)
        self.assertIn("🔑 Ключ: OK", dash_ds)

    def test_09_call_deepseek_safe_usage_none_and_empty_choices(self):
        """Проверка call_deepseek на устойчивость к usage=None и пустому списку choices."""
        orig_key = settings.DEEPSEEK_API_KEY
        settings.DEEPSEEK_API_KEY = "sk-test-key"
        try:
            # 1. usage=None не должен вызывать AttributeError
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": '{"cards":[{"text":"Что проверяет суд первой инстанции?","translation":"Суд первой инстанции исследует доказательства и устанавливает факты по делу."}]}'}}],
                "usage": None
            }
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
                unpacked, meta = self.run_async(call_deepseek("тест", system_instruction="SYS"))
                self.assertEqual(len(unpacked["cards"]), 1)
                self.assertEqual(meta["prompt_tokens"], 0)
                self.assertEqual(meta["cost_usd"], 0)

            # 2. choices=[] должен вызывать понятный ValueError
            mock_empty_choices = MagicMock()
            mock_empty_choices.status_code = 200
            mock_empty_choices.json.return_value = {"choices": []}
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_empty_choices):
                with self.assertRaises(ValueError):
                    self.run_async(call_deepseek("тест", system_instruction="SYS"))
        finally:
            settings.DEEPSEEK_API_KEY = orig_key

    def test_10_set_active_ai_provider_syncs_os_environ(self):
        """Проверка синхронизации settings.DEEPSEEK_MODEL и os.environ при смене модели."""
        import os
        orig_model = settings.DEEPSEEK_MODEL
        try:
            set_active_ai_provider("deepseek", "deepseek-chat")
            self.assertEqual(settings.DEEPSEEK_MODEL, "deepseek-chat")
            self.assertEqual(os.environ.get("DEEPSEEK_MODEL"), "deepseek-chat")

            set_active_ai_provider("deepseek", "deepseek-flash")
            self.assertEqual(settings.DEEPSEEK_MODEL, "deepseek-flash")
            self.assertEqual(os.environ.get("DEEPSEEK_MODEL"), "deepseek-flash")
        finally:
            set_active_ai_provider("deepseek", orig_model)

    def test_11_build_admin_keyboard_defaults_to_active_settings(self):
        """Проверка автоматического подтягивания модели при вызове build_admin_keyboard."""
        orig_model = settings.DEEPSEEK_MODEL
        try:
            settings.DEEPSEEK_MODEL = "deepseek-chat"
            kb = build_admin_keyboard(1)
            kb_texts = [btn.text for row in kb.inline_keyboard for btn in row]
            self.assertTrue(any("deepseek-chat" in t for t in kb_texts))

            settings.DEEPSEEK_MODEL = "deepseek-flash"
            kb_flash = build_admin_keyboard(1)
            kb_flash_texts = [btn.text for row in kb_flash.inline_keyboard for btn in row]
            self.assertTrue(any("deepseek-flash" in t for t in kb_flash_texts))
        finally:
            settings.DEEPSEEK_MODEL = orig_model

    def test_12_call_deepseek_flash_payload_and_large_context(self):
        """Проверка отправки параметров V4.1 Flash (32k tokens, disabled thinking, large context) в call_deepseek."""
        settings.DEEPSEEK_MODEL = "deepseek-flash"
        orig_key = settings.DEEPSEEK_API_KEY
        settings.DEEPSEEK_API_KEY = "sk-test-deepseek-flash-key"
        try:
            mock_response_json = {
                "id": "chatcmpl-ds-flash-01",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"domain":"law","slug":"test_sub","title":"Flash блок","c":[{"t":"Что такое преюдиция?","s":"ГПК РФ | Преюдиция","d":"Обязательность фактов, установленных вступившим в силу судебным постановлением.","e":"Факты не доказываются вновь.","l":"easy"}]}'
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_cache_hit_tokens": 1400,
                    "prompt_cache_miss_tokens": 200,
                    "completion_tokens": 150
                }
            }

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response_json

            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
                unpacked, meta = self.run_async(call_deepseek("Исходный учебный материал для Flash", system_instruction="SYS"))
                mock_post.assert_called_once()
                call_args = mock_post.call_args
                json_payload = call_args[1]["json"]

                self.assertEqual(json_payload["model"], "deepseek-flash")
                self.assertEqual(json_payload["max_tokens"], 32768)
                self.assertEqual(json_payload["thinking"], {"type": "disabled"})
                self.assertTrue(meta["cache_hit"])
                self.assertEqual(meta["prompt_tokens"], 1600)

            # Ответ, обрезанный на лимите, не парсится вслепую: исключение несёт стоимость для учёта
            truncated = MagicMock()
            truncated.status_code = 200
            truncated.json.return_value = {
                "choices": [{"message": {"content": '{"nodes":[{"key":"a"'}, "finish_reason": "length"}],
                "usage": {"prompt_cache_miss_tokens": 1000, "completion_tokens": 500}
            }
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=truncated):
                with self.assertRaises(LLMOutputTruncated) as ctx:
                    self.run_async(call_deepseek("Книга", system_instruction="SYS", max_tokens=500))
                self.assertGreater(ctx.exception.meta["cost_usd"], 0)
        finally:
            settings.DEEPSEEK_API_KEY = orig_key


if __name__ == "__main__":
    unittest.main()

