import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from app.core.config import settings

@pytest.fixture
def client():
    with patch("app.services.generation_worker.claim_next_pending_job", return_value=None):
        with TestClient(app) as c:
            yield c

def test_regenerate_mnemonic_draft_card_id_zero(client):
    async def fake_mnemonic(text, translation, subject, preference="visual", calls=None):
        if calls is not None:
            calls.append({"label": "mnemonic#1", "cost_usd": 0.0001, "prompt_tokens": 100, "completion_tokens": 50, "cache_hit_tokens": 0,
                          "model": "deepseek-flash", "duration_ms": 200, "finish_reason": "stop", "error": None})
        return {"keyword": "Дерево", "verbal_cue": "Человек отдыхает под кроной дерева"}

    with patch("app.core.auth.get_current_user_id", return_value="test_user_staging"):
        with patch("app.api.endpoints.cards.regenerate_card_mnemonic", new=fake_mnemonic):
            res = client.post("/api/management/cards/0/regenerate_mnemonic", json={
                "preference": "visual",
                "text": "休",
                "translation": "Отдыхать",
                "subject": "chinese_hsk1"
            })
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["status"] == "success"
            assert data["mnemonic"]["keyword"] == "Дерево"
            assert "отдыхает" in data["mnemonic"]["verbal_cue"]
