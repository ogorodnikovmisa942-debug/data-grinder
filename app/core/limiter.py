"""
app/core/limiter.py
Rate limiting configuration for FastAPI.
Provides graceful fallback if slowapi is not installed, preventing service downtime
while enabling production-grade rate limiting when slowapi is present.
"""

import logging
from typing import Callable, Any

logger = logging.getLogger("grinder.limiter")


class FallbackLimiter:
    """Fallback Limiter that acts as a no-op decorator when slowapi is absent."""
    def __init__(self, key_func: Callable = None, default_limits: Any = None, **kwargs: Any):
        self.key_func = key_func

    def limit(self, limit_value: str, **kwargs: Any):
        def decorator(func: Callable) -> Callable:
            return func
        return decorator


class FallbackRateLimitExceeded(Exception):
    """Fallback exception for rate limit exceeded."""
    pass


def fallback_rate_limit_exceeded_handler(request: Any, exc: Any):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})


def fallback_get_remote_address(request: Any = None) -> str:
    if request and hasattr(request, "client") and request.client:
        return request.client.host
    return "127.0.0.1"


try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded

    HAS_SLOWAPI = True
except ImportError:
    HAS_SLOWAPI = False
    logger.warning("[Limiter Warning] slowapi is not installed. Running with fallback limiter.")

    Limiter = FallbackLimiter
    RateLimitExceeded = FallbackRateLimitExceeded
    _rate_limit_exceeded_handler = fallback_rate_limit_exceeded_handler
    get_remote_address = fallback_get_remote_address

# Shared limiter instance across the application
limiter = Limiter(key_func=get_remote_address)

__all__ = [
    "limiter",
    "Limiter",
    "FallbackLimiter",
    "RateLimitExceeded",
    "_rate_limit_exceeded_handler",
    "get_remote_address",
    "HAS_SLOWAPI",
]
