# tests/test_13_deepseek_prompt_caching_and_atomic_rules.py
"""
Test Suite for Prompt Caching, Atomic Rules, Xiaomi MiMo Integration, and Admin Switcher.
Validates:
1. Integrity of DEEPSEEK_CACHED_SYSTEM_PROMPT and CURRICULUM_SKELETON_SYSTEM_PROMPT (static, >1024 tokens, atomic rules).
2. Xiaomi MiMo client call_mimo with mocked OpenAI-compatible endpoint, URL normalizer, and usage telemetry.
3. Admin AI provider switcher (GET /api/admin/ai-provider, POST /api/admin/switch-ai-provider, set_active_ai_provider).
4. Bot admin panel keyboard and dashboard rendering with AI provider indicators.
5. Routing of parse_raw_text and extract_curriculum_skeleton based on active provider.
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
    get_mimo_chat_url,
    call_mimo,
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
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()
        cls.orig_provider = getattr(settings, "AI_PROVIDER", "deepseek")

    @classmethod
    def tearDownClass(cls):
        settings.AI_PROVIDER = cls.orig_provider
        cls.client_cm.__exit__(None, None, None)

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

    def test_02_mimo_url_normalization(self):
        """Проверка корректной нормализации URL для endpoint chat/completions Xiaomi MiMo."""
        orig_base = settings.MIMO_BASE_URL
        try:
            settings.MIMO_BASE_URL = "https://api.xiaomimimo.com"
            self.assertEqual(get_mimo_chat_url(), "https://api.xiaomimimo.com/v1/chat/completions")

            settings.MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
            self.assertEqual(get_mimo_chat_url(), "https://api.xiaomimimo.com/v1/chat/completions")

            settings.MIMO_BASE_URL = "https://api.xiaomimimo.com/v1/"
            self.assertEqual(get_mimo_chat_url(), "https://api.xiaomimimo.com/v1/chat/completions")

            settings.MIMO_BASE_URL = "https://token-plan-sgp.xiaomimimo.com/v1/chat/completions"
            self.assertEqual(get_mimo_chat_url(), "https://token-plan-sgp.xiaomimimo.com/v1/chat/completions")
        finally:
            settings.MIMO_BASE_URL = orig_base

    def test_03_call_mimo_mocked_success(self):
        """Проверка вызова call_mimo с эмуляцией OpenAI-совместимого ответа Xiaomi MiMo."""
        orig_key = settings.MIMO_API_KEY
        settings.MIMO_API_KEY = "tp-test-mimo-key-12345"
        try:
            mock_response_json = {
                "id": "chatcmpl-mimo-test-01",
                "object": "chat.completion",
                "model": "mimo-v2.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"domain":"law","slug":"test_sub","title":"Тестовый блок","c":[{"t":"Что проверяет суд кассационной инстанции?","s":"ГПК РФ | Полномочия кассации","d":"Законность вступивших в силу судебных актов.","e":"Кассация не переоценивает доказательства, а проверяет соблюдение норм права.","l":"easy","h":"Кассация"}]}'
                        },
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": 1250,
                    "completion_tokens": 180,
                    "prompt_tokens_details": {
                        "cached_tokens": 1024
                    },
                    "prompt_cache_hit_tokens": 1024,
                    "prompt_cache_miss_tokens": 226
                }
            }

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response_json

            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
                unpacked, meta = self.run_async(call_mimo(
                    user_prompt="Тестовый вопрос",
                    fallback_subject="test_sub"
                ))

                # Проверяем вызов httpx
                mock_post.assert_called_once()
                call_args = mock_post.call_args
                url = call_args[0][0]
                headers = call_args[1]["headers"]
                json_payload = call_args[1]["json"]

                self.assertIn("/chat/completions", url)
                self.assertEqual(headers["Authorization"], "Bearer tp-test-mimo-key-12345")
                self.assertEqual(headers["api-key"], "tp-test-mimo-key-12345")
                self.assertEqual(json_payload["model"], "mimo-v2.5")
                self.assertEqual(json_payload["response_format"], {"type": "json_object"})

                # Проверяем распаковку карточек
                self.assertEqual(len(unpacked["cards"]), 1)
                self.assertEqual(unpacked["cards"][0]["text"], "Что проверяет суд кассационной инстанции?")
                self.assertEqual(unpacked["cards"][0]["translation"], "Законность вступивших в силу судебных актов.")

                # Проверяем метаданные кэширования
                self.assertTrue(meta["cache_hit"])
                self.assertEqual(meta["prompt_tokens"], 1250)
                self.assertEqual(meta["completion_tokens"], 180)
        finally:
            settings.MIMO_API_KEY = orig_key

    def test_04_call_mimo_fallback_on_404(self):
        """Проверка автоматического fallback на базовую модель 'mimo-v2.5' при коде 404/400."""
        orig_key = settings.MIMO_API_KEY
        orig_model = settings.MIMO_MODEL
        settings.MIMO_API_KEY = "tp-test-key"
        settings.MIMO_MODEL = "mimo-v2.5-pro-experimental"
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
                unpacked, meta = self.run_async(call_mimo("Запрос"))
                self.assertEqual(mock_post.call_count, 2)
                # Второй вызов должен использовать fallback модель "mimo-v2.5"
                second_call_model = mock_post.call_args_list[1][1]["json"]["model"]
                self.assertEqual(second_call_model, "mimo-v2.5")
                self.assertEqual(meta["model_resolved"], "mimo-v2.5")
        finally:
            settings.MIMO_API_KEY = orig_key
            settings.MIMO_MODEL = orig_model

    def test_05_admin_switch_ai_provider_api(self):
        """Проверка REST эндпоинтов администратора для получения статуса и переключения ИИ-провайдера."""
        headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

        # 1. Проверка GET /api/admin/ai-provider без токена -> 403
        r_unauth = self.client.get("/api/admin/ai-provider")
        self.assertEqual(r_unauth.status_code, 403)

        # 2. Проверка GET /api/admin/ai-provider с токеном -> 200
        r_status = self.client.get("/api/admin/ai-provider", headers=headers)
        self.assertEqual(r_status.status_code, 200)
        data = r_status.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("provider", data)
        self.assertIn("available_providers", data)
        self.assertIn("deepseek", data)
        self.assertIn("mimo", data)

        # 3. Переключение на mimo через POST /api/admin/switch-ai-provider
        r_switch_mimo = self.client.post(
            "/api/admin/switch-ai-provider",
            headers=headers,
            json={"provider": "mimo"}
        )
        self.assertEqual(r_switch_mimo.status_code, 200)
        resp_mimo = r_switch_mimo.json()
        self.assertEqual(resp_mimo["provider"], "mimo")
        self.assertEqual(settings.AI_PROVIDER, "mimo")

        # 4. Проверка GET статуса после переключения
        r_status_after = self.client.get("/api/admin/ai-provider", headers=headers)
        self.assertEqual(r_status_after.json()["provider"], "mimo")

        # 5. Переключение обратно на deepseek
        r_switch_ds = self.client.post(
            "/api/admin/switch-ai-provider",
            headers=headers,
            json={"provider": "deepseek"}
        )
        self.assertEqual(r_switch_ds.status_code, 200)
        self.assertEqual(settings.AI_PROVIDER, "deepseek")

        # 6. Ошибка при передаче невалидного провайдера
        r_invalid = self.client.post(
            "/api/admin/switch-ai-provider",
            headers=headers,
            json={"provider": "unknown_ai_provider"}
        )
        self.assertEqual(r_invalid.status_code, 400)

    def test_06_parse_raw_text_routes_to_active_provider(self):
        """Проверка динамической маршрутизации parse_raw_text в зависимости от active provider."""
        # 1. При settings.AI_PROVIDER == "deepseek"
        settings.AI_PROVIDER = "deepseek"
        mock_unpacked = {"cards": [{"text": "Тест DeepSeek", "translation": "Ответ DS"}]}
        mock_meta = {"model_resolved": "deepseek-chat", "prompt_tokens": 100, "completion_tokens": 50}

        with patch("app.services.ai_gateway.call_deepseek", new_callable=AsyncMock, return_value=(mock_unpacked, mock_meta)) as mock_ds, \
             patch("app.services.ai_gateway.call_mimo", new_callable=AsyncMock) as mock_mimo:
            res = self.run_async(parse_raw_text("Какой-то исходный учебный текст", target_subject="law"))
            mock_ds.assert_called_once()
            mock_mimo.assert_not_called()

        # 2. При settings.AI_PROVIDER == "mimo"
        settings.AI_PROVIDER = "mimo"
        mock_unpacked_mimo = {"cards": [{"text": "Тест MiMo", "translation": "Ответ MiMo"}]}
        mock_meta_mimo = {"model_resolved": "mimo-v2.5", "prompt_tokens": 200, "completion_tokens": 80}

        with patch("app.services.ai_gateway.call_deepseek", new_callable=AsyncMock) as mock_ds, \
             patch("app.services.ai_gateway.call_mimo", new_callable=AsyncMock, return_value=(mock_unpacked_mimo, mock_meta_mimo)) as mock_mimo:
            res_mimo = self.run_async(parse_raw_text("Какой-то исходный учебный текст", target_subject="law"))
            mock_mimo.assert_called_once()
            mock_ds.assert_not_called()

        # Возвращаем deepseek
        settings.AI_PROVIDER = "deepseek"

    def test_07_extract_curriculum_skeleton_routes_to_active_provider(self):
        """Проверка динамической маршрутизации Прохода 1 (Curriculum Skeleton) на активного провайдера."""
        mock_res = {"modules": [], "phrase_title": "Каркас"}
        mock_meta = {"model_resolved": "mimo-v2.5", "prompt_tokens": 300, "completion_tokens": 50}

        # Проверяем вызов MiMo
        settings.AI_PROVIDER = "mimo"
        with patch("app.services.ai_gateway.call_mimo", new_callable=AsyncMock, return_value=(mock_res, mock_meta)) as mock_mimo, \
             patch("app.services.ai_gateway.call_deepseek", new_callable=AsyncMock) as mock_ds:
            skeleton = self.run_async(extract_curriculum_skeleton("Оглавление учебника...", target_subject="law"))
            mock_mimo.assert_called_once()
            mock_ds.assert_not_called()

        # Возвращаем deepseek
        settings.AI_PROVIDER = "deepseek"

    def test_08_telegram_bot_admin_keyboard_and_dashboard(self):
        """Проверка отображения кнопки переключения ИИ в клавиатуре бота и текста в дашборде."""
        # 1. При deepseek
        kb_ds = build_admin_keyboard(phase=1, ai_provider="deepseek")
        kb_texts_ds = [btn.text for row in kb_ds.inline_keyboard for btn in row]
        self.assertTrue(any("Xiaomi MiMo" in t for t in kb_texts_ds), "Должна быть кнопка переключения на MiMo")

        dash_ds = render_admin_dashboard_text({
            "phase": 1, "participants": 5, "invites_active": 3, "cards": 120,
            "reviews": 340, "outliers": 2, "pending_jobs": 0,
            "ai_provider": "deepseek", "ai_model": "deepseek-chat"
        })
        self.assertIn("DeepSeek", dash_ds)

        # 2. При mimo
        kb_mimo = build_admin_keyboard(phase=2, ai_provider="mimo")
        kb_texts_mimo = [btn.text for row in kb_mimo.inline_keyboard for btn in row]
        self.assertTrue(any("DeepSeek" in t for t in kb_texts_mimo), "Должна быть кнопка переключения на DeepSeek")

        dash_mimo = render_admin_dashboard_text({
            "phase": 2, "participants": 5, "invites_active": 3, "cards": 120,
            "reviews": 340, "outliers": 2, "pending_jobs": 0,
            "ai_provider": "mimo", "ai_model": "mimo-v2.5"
        })
        self.assertIn("Xiaomi MiMo", dash_mimo)

    def test_09_mimo_url_normalization_anthropic_alias(self):
        """Проверка нормализации URL если задан Anthropic alias endpoint."""
        orig_base = settings.MIMO_BASE_URL
        try:
            settings.MIMO_BASE_URL = "https://token-plan-sgp.xiaomimimo.com/anthropic"
            self.assertEqual(get_mimo_chat_url(), "https://token-plan-sgp.xiaomimimo.com/v1/chat/completions")
        finally:
            settings.MIMO_BASE_URL = orig_base

    def test_10_call_mimo_missing_api_key(self):
        """Проверка генерации ValueError при отсутствующем ключе MIMO_API_KEY."""
        orig_key = settings.MIMO_API_KEY
        settings.MIMO_API_KEY = ""
        try:
            with self.assertRaises(ValueError) as ctx:
                self.run_async(call_mimo("тест"))
            self.assertIn("MIMO_API_KEY", str(ctx.exception))
        finally:
            settings.MIMO_API_KEY = orig_key

    def test_11_call_mimo_safe_usage_none_and_empty_choices(self):
        """Проверка call_mimo на устойчивость к usage=None и пустому списку choices."""
        orig_key = settings.MIMO_API_KEY
        settings.MIMO_API_KEY = "tp-test-key"
        try:
            # 1. usage=None не должен вызывать AttributeError
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": '{"cards":[{"text":"Что проверяет суд первой инстанции?","translation":"Суд первой инстанции исследует доказательства и устанавливает факты по делу."}]}'}}],
                "usage": None
            }
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
                unpacked, meta = self.run_async(call_mimo("тест"))
                self.assertEqual(len(unpacked["cards"]), 1)
                self.assertEqual(meta["prompt_tokens"], 0)

            # 2. choices=[] должен вызывать понятный ValueError
            mock_empty_choices = MagicMock()
            mock_empty_choices.status_code = 200
            mock_empty_choices.json.return_value = {"choices": []}
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_empty_choices):
                with self.assertRaises(ValueError):
                    self.run_async(call_mimo("тест"))
        finally:
            settings.MIMO_API_KEY = orig_key

    def test_12_set_active_ai_provider_syncs_os_environ(self):
        """Проверка синхронизации settings.AI_PROVIDER и os.environ при переключении."""
        import os
        orig = settings.AI_PROVIDER
        try:
            set_active_ai_provider("mimo")
            self.assertEqual(settings.AI_PROVIDER, "mimo")
            self.assertEqual(os.environ.get("AI_PROVIDER"), "mimo")

            set_active_ai_provider("deepseek")
            self.assertEqual(settings.AI_PROVIDER, "deepseek")
            self.assertEqual(os.environ.get("AI_PROVIDER"), "deepseek")
        finally:
            set_active_ai_provider(orig)

    def test_13_build_admin_keyboard_defaults_to_active_settings(self):
        """Проверка автоматического сохранения провайдера при вызове build_admin_keyboard без явного указания."""
        orig = settings.AI_PROVIDER
        try:
            settings.AI_PROVIDER = "mimo"
            kb = build_admin_keyboard(1)
            kb_texts = [btn.text for row in kb.inline_keyboard for btn in row]
            self.assertTrue(any("MiMo ➔ DeepSeek" in t for t in kb_texts))

            settings.AI_PROVIDER = "deepseek"
            kb_ds = build_admin_keyboard(1)
            kb_texts_ds = [btn.text for row in kb_ds.inline_keyboard for btn in row]
            self.assertTrue(any("DeepSeek ➔ Xiaomi MiMo" in t for t in kb_texts_ds))
        finally:
            settings.AI_PROVIDER = orig

    def test_14_extract_curriculum_skeleton_mimo_large_context(self):
        """Проверка расширенного контекста (>60k символов) для Xiaomi MiMo в extract_curriculum_skeleton."""
        settings.AI_PROVIDER = "mimo"
        large_text = "Глава " * 15000  # ~90 000 символов
        mock_res = {"modules": [], "phrase_title": "Большая книга"}
        mock_meta = {"model_resolved": "mimo-v2.5", "prompt_tokens": 500, "completion_tokens": 50}

        try:
            with patch("app.services.ai_gateway.call_mimo", new_callable=AsyncMock, return_value=(mock_res, mock_meta)) as mock_call:
                self.run_async(extract_curriculum_skeleton(large_text, target_subject="law"))
                mock_call.assert_called_once()
                user_prompt_passed = mock_call.call_args[0][0]
                # Для MiMo длина переданного текста должна превышать 60 000 знаков
                self.assertGreater(len(user_prompt_passed), 60000)
        finally:
            settings.AI_PROVIDER = "deepseek"


if __name__ == "__main__":
    unittest.main()
