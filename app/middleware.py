# ═══════════════════════════════════════════════════════════
# StreamForge — Middleware (Rate Limiting + API Key Auth)
# ═══════════════════════════════════════════════════════════

import logging
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .config import config

logger = logging.getLogger("streamforge")

# ─── Rate Limiter ───────────────────────────────────────────
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[config.RATE_LIMIT],
    storage_uri="memory://",
)


def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Custom rate limit exceeded response"""
    logger.warning(f"Rate limit exceeded: {get_remote_address(request)}")
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "detail": str(exc.detail),
            "retry_after": exc.detail,
        },
    )


# ─── API Key Authentication ─────────────────────────────────
async def api_key_middleware(request: Request, call_next):
    """Optional API key authentication middleware"""
    # Skip auth if no API key is configured
    if not config.has_api_key():
        return await call_next(request)

    # Skip auth for static files, docs, health check, and root
    skip_paths = ("/", "/health", "/docs", "/redoc", "/openapi.json",
                  "/static/", "/output/")
    path = request.url.path
    if any(path.startswith(p) for p in skip_paths):
        return await call_next(request)

    # Check API key in header or query parameter
    api_key = (
        request.headers.get("X-API-Key")
        or request.headers.get("Authorization", "").removeprefix("Bearer ")
        or request.query_params.get("api_key")
    )

    if api_key != config.API_KEY:
        logger.warning(f"Unauthorized API access attempt from {get_remote_address(request)}")
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized", "detail": "Invalid or missing API key"},
        )

    return await call_next(request)
