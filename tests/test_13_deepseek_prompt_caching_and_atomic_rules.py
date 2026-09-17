# tests/test_13_deepseek_prompt_caching_and_atomic_rules.py
"""
Test Suite for Prompt Caching, Atomic Rules, DeepSeek V4.1 Flash Integration, and Admin Switcher.
Validates:
1. Integrity of DEEPSEEK_CACHED_SYSTEM_PROMPT and CURRICULUM_SKELETON_SYSTEM_PROMPT (static, >1024 tokens, atomic rules).
2. DeepSeek client call_deepseek with mocked OpenAI-compatible endpoint and usage telemetry.
3. Admin AI provider and model switcher (GET /api/admin/ai-provider, POST /api/admin/switch-ai-provider, set_active_ai_provider).
4. Bot admin panel keyboard and dashboard rendering with DeepSeek model indicators.
5. Routing of parse_raw_text and extract_curriculum_skeleton through DeepSeek.
"""

import unittest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from main import app
from app.core.config import settings
from app.services.ai_gateway import (
    DEEPSEEK_CACHED_SYSTEM_PROMPT,
    CURRICULUM_SKELETON_SYSTEM_PROMPT,
    call_deepseek,
    parse_raw_text,
    extract_curriculum_skeleton,
    is_blacklisted_card,
    unpack_minified_cards
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

    def test_01_deepseek_prompt_caching_contract(self):
        """Проверка контракта Prompt Caching DeepSeek: промпт статичен, длинее 1024 токенов и содержит атомарные законы."""
        # Длина промпта должна существенно превышать 1024 токена (~4000 символов) для активации кэша
        self.assertGreater(len(DEEPSEEK_CACHED_SYSTEM_PROMPT), 5000, "Системный промпт должен превышать порог кэширования (>1024 токенов)")
        
        # Ключевые когнитивные и архитектурные правила
        self.assertIn("Minimum Information Principle", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("Absolute Prohibition of Lists & Enumerations", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("Zero-Spoiler Law", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("Pareto 80/20 Law", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("organ_slug or module_slug", DEEPSEEK_CACHED_SYSTEM_PROMPT)

        # Контракт схемы JSON
        self.assertIn('"graph":', DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn('"nodes":', DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn('"edges":', DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn('"c":', DEEPSEEK_CACHED_SYSTEM_PROMPT)

        # Контракт скелета курса
        self.assertIn("CHIEF EDUCATIONAL ARCHITECT", CURRICULUM_SKELETON_SYSTEM_PROMPT.upper())
        self.assertIn("quota", CURRICULUM_SKELETON_SYSTEM_PROMPT)
        self.assertIn("modules", CURRICULUM_SKELETON_SYSTEM_PROMPT)

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
                    fallback_subject="test_sub"
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

                self.assertEqual(len(unpacked["cards"]), 1)
                self.assertEqual(unpacked["cards"][0]["text"], "Что проверяет суд кассационной инстанции?")
                self.assertEqual(unpacked["cards"][0]["translation"], "Законность вступивших в силу судебных актов.")

                self.assertTrue(meta["cache_hit"])
                self.assertEqual(meta["prompt_tokens"], 1250)
                self.assertEqual(meta["completion_tokens"], 180)
        finally:
            settings.DEEPSEEK_API_KEY = orig_key

    def test_03_call_deepseek_fallback_on_404(self):
        """Проверка автоматического fallback на 'deepseek-chat' при ошибке 404/400 неизвестной модели."""
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
                unpacked, meta = self.run_async(call_deepseek("Запрос"))
                self.assertEqual(mock_post.call_count, 2)
                second_call_model = mock_post.call_args_list[1][1]["json"]["model"]
                self.assertEqual(second_call_model, "deepseek-chat")
                self.assertEqual(meta["model_resolved"], "deepseek-chat")
        finally:
            settings.DEEPSEEK_API_KEY = orig_key
            settings.DEEPSEEK_MODEL = orig_model

    def test_04_call_deepseek_missing_api_key(self):
        """Проверка генерации ValueError при отсутствующем DEEPSEEK_API_KEY."""
        orig_key = settings.DEEPSEEK_API_KEY
        settings.DEEPSEEK_API_KEY = ""
        try:
            with self.assertRaises(ValueError) as ctx:
                self.run_async(call_deepseek("тест"))
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

    def test_06_parse_raw_text_calls_deepseek(self):
        """Проверка вызова parse_raw_text через call_deepseek."""
        mock_unpacked = {"cards": [{"text": "Тест DeepSeek", "translation": "Ответ DS"}]}
        mock_meta = {"model_resolved": "deepseek-flash", "prompt_tokens": 100, "completion_tokens": 50}

        with patch("app.services.ai_gateway.call_deepseek", new_callable=AsyncMock, return_value=(mock_unpacked, mock_meta)) as mock_ds:
            res = self.run_async(parse_raw_text("Какой-то исходный учебный текст", target_subject="law"))
            mock_ds.assert_called_once()
            self.assertEqual(len(res["cards"]), 1)
            self.assertEqual(res["cards"][0]["text"], "Тест DeepSeek")

    def test_07_extract_curriculum_skeleton_calls_deepseek(self):
        """Проверка выполнения Прохода 1 (Curriculum Skeleton) через call_deepseek."""
        mock_res = {"modules": [{"slug": "mod_1", "name": "Введение", "quota": 10}], "phrase_title": "Каркас"}
        mock_meta = {"model_resolved": "deepseek-flash", "prompt_tokens": 300, "completion_tokens": 50}

        with patch("app.services.ai_gateway.call_deepseek", new_callable=AsyncMock, return_value=(mock_res, mock_meta)) as mock_ds:
            skeleton = self.run_async(extract_curriculum_skeleton("Оглавление учебника...", target_subject="law"))
            mock_ds.assert_called_once()
            self.assertEqual(len(skeleton["modules"]), 1)

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
                unpacked, meta = self.run_async(call_deepseek("тест"))
                self.assertEqual(len(unpacked["cards"]), 1)
                self.assertEqual(meta["prompt_tokens"], 0)

            # 2. choices=[] должен вызывать понятный ValueError
            mock_empty_choices = MagicMock()
            mock_empty_choices.status_code = 200
            mock_empty_choices.json.return_value = {"choices": []}
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_empty_choices):
                with self.assertRaises(ValueError):
                    self.run_async(call_deepseek("тест"))
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
                unpacked, meta = self.run_async(call_deepseek("Исходный учебный материал для Flash"))
                mock_post.assert_called_once()
                call_args = mock_post.call_args
                json_payload = call_args[1]["json"]

                self.assertEqual(json_payload["model"], "deepseek-flash")
                self.assertEqual(json_payload["max_tokens"], 32768)
                self.assertEqual(json_payload["thinking"], {"type": "disabled"})
                self.assertTrue(meta["cache_hit"])
                self.assertEqual(meta["prompt_tokens"], 1600)

            # Проверяем расширенный контекст (>60k) для DeepSeek в extract_curriculum_skeleton
            large_text = "Раздел курса " * 10000  # ~130 000 символов
            mock_skel_res = {"modules": [], "phrase_title": "Большой каркас курса"}
            mock_skel_meta = {"model_resolved": "deepseek-flash", "prompt_tokens": 800, "completion_tokens": 60}
            with patch("app.services.ai_gateway.call_deepseek", new_callable=AsyncMock, return_value=(mock_skel_res, mock_skel_meta)) as mock_ds_skel:
                self.run_async(extract_curriculum_skeleton(large_text, target_subject="law"))
                mock_ds_skel.assert_called_once()
                prompt_sent = mock_ds_skel.call_args[0][0]
                self.assertGreater(len(prompt_sent), 60000)
        finally:
            settings.DEEPSEEK_API_KEY = orig_key


if __name__ == "__main__":
    unittest.main()

