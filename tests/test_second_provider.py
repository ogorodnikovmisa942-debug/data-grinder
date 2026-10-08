"""Второй поставщик ИИ: если DeepSeek не отвечает или отдаёт 5xx, тот же запрос уходит второму (если он настроен)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.config import settings
from app.services.ai_gateway.client import call_deepseek


def _ok():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    return r


def _call():
    return asyncio.run(call_deepseek("запрос", system_instruction="SYS"))


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-main")
    monkeypatch.setattr(settings, "FALLBACK_AI_BASE_URL", "https://second.example/v1")
    monkeypatch.setattr(settings, "FALLBACK_AI_API_KEY", "sk-second")
    monkeypatch.setattr(settings, "FALLBACK_AI_MODEL", "second-model")


def test_network_failure_goes_to_the_second_provider(keys):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=[httpx.ConnectError("нет связи"), _ok()]) as post:
        data, meta = _call()
    assert data == {"ok": True} and meta["model_resolved"] == "second-model" and post.call_count == 2
    url, kwargs = post.call_args_list[1][0][0], post.call_args_list[1][1]
    assert url == "https://second.example/v1/chat/completions" and kwargs["headers"]["Authorization"] == "Bearer sk-second"
    assert kwargs["json"]["model"] == "second-model" and "thinking" not in kwargs["json"]
    assert kwargs["json"]["messages"][1]["content"] == "запрос"                                  # тот же запрос


def test_server_error_goes_to_the_second_provider(keys):
    bad = MagicMock()
    bad.status_code = 503
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=[bad, _ok()]) as post:
        _, meta = _call()
    assert post.call_count == 2 and meta["model_resolved"] == "second-model"


def test_without_a_second_provider_the_error_stays(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-main")
    monkeypatch.setattr(settings, "FALLBACK_AI_BASE_URL", "")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=httpx.ConnectError("нет связи")):
        with pytest.raises(httpx.ConnectError):
            _call()
    bad = MagicMock()
    bad.status_code = 503
    bad.text = "overloaded"
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=bad) as post:
        with pytest.raises(RuntimeError):
            _call()
    assert post.call_count == 1                                                                     # второго нет — повторять не к кому


def test_a_good_main_answer_never_touches_the_second_provider(keys):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_ok()) as post:
        _, meta = _call()
    assert post.call_count == 1 and meta["model_resolved"] != "second-model"
