"""Проброс X-Request-ID и привязка к контексту (логи / meta)."""
from __future__ import annotations

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import reset_request_id, set_request_id

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        client_rid = (request.headers.get("x-request-id") or "").strip()
        rid = client_rid if _SAFE_REQUEST_ID.match(client_rid) else str(uuid.uuid4())
        request.state.request_id = rid
        token = set_request_id(rid)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers["X-Request-ID"] = rid
        ver = getattr(request.app, "version", None)
        if ver:
            response.headers.setdefault("X-Amber-Version", str(ver))
        return response
