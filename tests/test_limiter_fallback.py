import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from app.core.limiter import (
    limiter,
    FallbackLimiter,
    FallbackRateLimitExceeded,
    fallback_rate_limit_exceeded_handler,
    fallback_get_remote_address,
    RateLimitExceeded,
    _rate_limit_exceeded_handler,
    HAS_SLOWAPI,
)


def test_limiter_instance_and_decorator():
    """Verify that limiter can decorate an async endpoint without throwing exceptions."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.get("/ping")
    @limiter.limit("100/minute")
    async def ping(request: Request):
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_main_app_startup_with_limiter():
    """Verify that main FastAPI app imports and mounts the limiter correctly."""
    from main import app
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200


def test_fallback_limiter_mock_execution():
    """Verify that FallbackLimiter works seamlessly when slowapi is absent."""
    fallback_limiter = FallbackLimiter(key_func=fallback_get_remote_address)
    decorator = fallback_limiter.limit("5/minute")

    @decorator
    async def sample_endpoint(request: Request = None):
        return {"data": 123}

    import asyncio
    result = asyncio.run(sample_endpoint(None))
    assert result == {"data": 123}

    handler_resp = fallback_rate_limit_exceeded_handler(None, FallbackRateLimitExceeded())
    assert handler_resp.status_code == 429
