# tests/test_auth_security.py
import hmac
import time
import hashlib
import urllib.parse
from fastapi.testclient import TestClient
import pytest

from main import app
from app.core.config import settings


def generate_valid_init_data(bot_token: str, user_id: int = 12345678, auth_ts: int | None = None) -> str:
    user_json = f'{{"id":{user_id},"first_name":"SecurityTest","username":"sectest"}}'
    auth_date = str(int(time.time()) if auth_ts is None else auth_ts)
    params = {
        "auth_date": auth_date,
        "query_id": "AAHdF6IQAAAAAN0XohD123",
        "user": user_json,
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    hash_val = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = hash_val
    return urllib.parse.urlencode(params)


def test_production_mode_blocks_unauthorized_x_user_id():
    """In production (DEBUG=False, TESTING=False, real token), X-User-Id header MUST be rejected with 401."""
    orig_debug = settings.DEBUG
    orig_testing = settings.TESTING
    orig_token = settings.TELEGRAM_BOT_TOKEN

    try:
        settings.DEBUG = False
        settings.TESTING = False
        settings.TELEGRAM_BOT_TOKEN = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"

        with TestClient(app) as client:
            # 1. Attacker attempts to impersonate via X-User-Id without valid TMA initData
            res = client.get("/api/practice/session?subject=sudoustr", headers={"X-User-Id": "victim_user_999"})
            assert res.status_code == 401
            assert "Telegram WebApp" in res.json().get("detail", "")

            # 2. Attacker passes invalid / forged initData
            res_forged = client.get(
                "/api/practice/session?subject=sudoustr",
                headers={"X-Telegram-Init-Data": "auth_date=1&user={}&hash=fakehash"}
            )
            assert res_forged.status_code == 401

            # 3. Legitimate user passes cryptographically verified initData
            valid_init_data = generate_valid_init_data(settings.TELEGRAM_BOT_TOKEN, user_id=98765432)
            res_valid = client.get(
                "/api/practice/session?subject=sudoustr",
                headers={"X-Telegram-Init-Data": valid_init_data}
            )
            # Should succeed or return normal domain response (e.g. 200), NOT 401
            assert res_valid.status_code != 401

    finally:
        settings.DEBUG = orig_debug
        settings.TESTING = orig_testing
        settings.TELEGRAM_BOT_TOKEN = orig_token


def test_dev_mode_permits_x_user_id():
    """In development/test mode, X-User-Id header is allowed."""
    orig_debug = settings.DEBUG
    orig_testing = settings.TESTING
    try:
        settings.DEBUG = True
        settings.TESTING = True
        with TestClient(app) as client:
            res = client.get("/api/practice/session?subject=sudoustr", headers={"X-User-Id": "dev_test_user"})
            assert res.status_code != 401
    finally:
        settings.DEBUG = orig_debug
        settings.TESTING = orig_testing


def test_telegram_init_data_with_signature_accepted():
    """Telegram Bot API 7.0+ attaches a 'signature' query parameter which must not break HMAC validation."""
    orig_debug = settings.DEBUG
    orig_testing = settings.TESTING
    orig_token = settings.TELEGRAM_BOT_TOKEN

    try:
        settings.DEBUG = False
        settings.TESTING = False
        settings.TELEGRAM_BOT_TOKEN = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"

        valid_init_data = generate_valid_init_data(settings.TELEGRAM_BOT_TOKEN, user_id=98765432)
        # Modern Telegram clients attach signature parameter
        init_data_with_sig = f"{valid_init_data}&signature=third_party_bot_api_7_signature"

        with TestClient(app) as client:
            res = client.get(
                "/api/practice/session?subject=sudoustr",
                headers={"Authorization": f"tma {init_data_with_sig}"}
            )
            assert res.status_code != 401
    finally:
        settings.DEBUG = orig_debug
        settings.TESTING = orig_testing
        settings.TELEGRAM_BOT_TOKEN = orig_token


PROD_TOKEN = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"


def _prod(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "TESTING", False)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", PROD_TOKEN)
    monkeypatch.setattr(settings, "ADMIN_TELEGRAM_ID", "777")
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "s3cret-admin")


URL = "/api/practice/session?subject=sudoustr"


def test_prod_rejects_every_unsigned_identity(monkeypatch):
    """Числовой id, default_user, tg_id и id админа без подписи — 401 (раньше принимались)."""
    _prod(monkeypatch)
    with TestClient(app) as client:
        for headers, qs in [
            ({"X-User-Id": "1222282942"}, ""),
            ({"X-User-Id": "default_user"}, ""),
            ({"X-User-Id": "777"}, ""),
            ({}, "&tg_id=12345"),
        ]:
            assert client.get(URL + qs, headers=headers).status_code == 401, (headers, qs)


def test_prod_rejects_unverified_initdata_payload(monkeypatch):
    """initData с неверным hash не даёт личность, даже если payload содержит числовой id."""
    _prod(monkeypatch)
    with TestClient(app) as client:
        res = client.get(URL, headers={
            "Authorization": "tma auth_date=1727000000&user=%7B%22id%22%3A1222282942%7D&hash=mismatched_hash",
            "X-User-Id": "1222282942",
        })
        assert res.status_code == 401


def test_prod_rejects_stale_signed_initdata(monkeypatch):
    """Валидная подпись, но auth_date старше срока годности — 401 (защита от повторного использования)."""
    _prod(monkeypatch)
    stale = generate_valid_init_data(PROD_TOKEN, user_id=555, auth_ts=int(time.time()) - 30 * 24 * 3600)
    with TestClient(app) as client:
        assert client.get(URL, headers={"X-Telegram-Init-Data": stale}).status_code == 401


def test_signed_identity_wins_over_spoofed_header(monkeypatch):
    """Личность берётся только из подписи: X-User-Id админа не подменяет подписанного пользователя."""
    _prod(monkeypatch)
    from app.core.auth import get_current_user_id
    from starlette.requests import Request as R
    import asyncio

    class FakeDB:
        async def execute(self, *a, **k):
            class Res:
                def scalar_one_or_none(self_inner): return object()
            return Res()
        def add(self, *a): pass
        async def commit(self): pass
        async def rollback(self): pass

    init = generate_valid_init_data(PROD_TOKEN, user_id=4242)
    req = R({"type": "http", "query_string": b"", "headers": [
        (b"x-telegram-init-data", init.encode()), (b"x-user-id", b"777")]})
    assert asyncio.run(get_current_user_id(req, FakeDB())) == "4242"


def test_service_ids_are_not_admin_in_prod(monkeypatch):
    from app.services.card_db_sync import is_admin_or_dev
    _prod(monkeypatch)
    assert not is_admin_or_dev("default_user") and not is_admin_or_dev("dev_user")
    assert is_admin_or_dev("777")


def test_admin_web_page_is_removed(monkeypatch):
    """Веб-страницы /admin больше нет: токен не может утечь через HTML (админ-API защищено токеном)."""
    _prod(monkeypatch)
    with TestClient(app) as client:
        for url in ("/admin", "/admin?token=s3cret-admin"):
            r = client.get(url)
            assert r.status_code == 404 and "s3cret-admin" not in r.text


def test_admin_api_rejects_empty_configured_token(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "")
    with TestClient(app) as client:
        assert client.get("/api/admin/users", headers={"X-Admin-Token": ""}).status_code == 403
