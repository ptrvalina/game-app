"""Опциональная защита API ключом (интеграции банка)."""
from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings, get_settings
from app.core.errors import build_error_response


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Если задан AMBER_API_KEY — проверяем X-Api-Key или Authorization: Bearer."""

    _PUBLIC_PREFIXES = (
        "/health",
        "/favicon.ico",
        "/api/v1/health",
    )

    async def dispatch(self, request: Request, call_next) -> Response:
        settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
        expected = (settings.api_key or "").strip()
        if not expected:
            return await call_next(request)
        path = request.url.path
        if path in self._PUBLIC_PREFIXES:
            return await call_next(request)

        key = request.headers.get("x-api-key") or request.headers.get("X-Api-Key")
        if not key:
            auth = request.headers.get("authorization") or ""
            if auth.lower().startswith("bearer "):
                key = auth.split(" ", 1)[1].strip()
        if not key or not hmac.compare_digest(key, expected):
            rid = getattr(request.state, "request_id", None)
            response = JSONResponse(
                status_code=401,
                content=build_error_response(
                    code="unauthorized",
                    message="Неверный или отсутствующий API-ключ (X-Api-Key или Authorization: Bearer).",
                    request_id=rid,
                ).model_dump(mode="json"),
            )
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            if rid:
                response.headers.setdefault("X-Request-ID", rid)
            return response
        return await call_next(request)
