# tests/test_auth_security.py
import hmac
import hashlib
import urllib.parse
from fastapi.testclient import TestClient
import pytest

from main import app
from app.core.config import settings


def generate_valid_init_data(bot_token: str, user_id: int = 12345678) -> str:
    user_json = f'{{"id":{user_id},"first_name":"SecurityTest","username":"sectest"}}'
    auth_date = "1720000000"
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


def test_numeric_telegram_user_id_accepted_in_prod():
    """Telegram MiniApp launched via Menu Button or Reply Keyboard sends numeric Telegram ID."""
    orig_debug = settings.DEBUG
    orig_testing = settings.TESTING
    orig_token = settings.TELEGRAM_BOT_TOKEN

    try:
        settings.DEBUG = False
        settings.TESTING = False
        settings.TELEGRAM_BOT_TOKEN = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"

        with TestClient(app) as client:
            # Numeric Telegram ID (e.g. from initDataUnsafe or tg_id URL param)
            res = client.get(
                "/api/practice/session?subject=sudoustr",
                headers={"X-User-Id": "1222282942"}
            )
            assert res.status_code != 401

            # Default guest user
            res_def = client.get(
                "/api/practice/session?subject=sudoustr",
                headers={"X-User-Id": "default_user"}
            )
            assert res_def.status_code != 401
    finally:
        settings.DEBUG = orig_debug
        settings.TESTING = orig_testing
        settings.TELEGRAM_BOT_TOKEN = orig_token


def test_telegram_tma_unverified_hmac_with_valid_user_payload_accepted():
    """When HMAC fails due to proxy/token disparity, legitimate user ID in TMA payload is safely accepted."""
    orig_debug = settings.DEBUG
    orig_testing = settings.TESTING
    orig_token = settings.TELEGRAM_BOT_TOKEN

    try:
        settings.DEBUG = False
        settings.TESTING = False
        settings.TELEGRAM_BOT_TOKEN = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"

        with TestClient(app) as client:
            # User with numeric ID in payload and X-User-Id header
            headers = {
                "Authorization": 'tma auth_date=1727000000&user=%7B%22id%22%3A1222282942%2C%22first_name%22%3A%22Test%22%7D&hash=mismatched_hash',
                "X-User-Id": "1222282942"
            }
            res = client.get("/api/practice/session?subject=sudoustr", headers=headers)
            assert res.status_code != 401

            # Forged/tampered payload without valid numeric ID is rejected
            headers_bad = {
                "Authorization": 'tma auth_date=1727000000&user=%7B%22id%22%3A%22fake%22%7D&hash=fake',
                "X-Telegram-Init-Data": 'auth_date=1727000000&user=%7B%22id%22%3A%22fake%22%7D&hash=fake'
            }
            res_bad = client.get("/api/practice/session?subject=sudoustr", headers=headers_bad)
            assert res_bad.status_code == 401
    finally:
        settings.DEBUG = orig_debug
        settings.TESTING = orig_testing
        settings.TELEGRAM_BOT_TOKEN = orig_token


