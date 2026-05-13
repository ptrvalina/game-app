"""Единый формат ошибок API (удобно для интеграций и UI)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


def build_error_response(
    *,
    code: str,
    message: str,
    request_id: str | None,
    details: list[dict[str, Any]] | None = None,
) -> ErrorResponse:
    return ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=request_id,
            details=details,
        )
    )


def sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Не возвращаем сырые input/ctx значения клиенту: это возможная утечка ПДн.
    """
    sanitized: list[dict[str, Any]] = []
    for err in errors:
        item = {
            "type": err.get("type"),
            "loc": err.get("loc"),
            "msg": err.get("msg"),
        }
        ctx = err.get("ctx")
        if isinstance(ctx, dict):
            item["ctx_keys"] = sorted(str(k) for k in ctx.keys())
        sanitized.append(item)
    return sanitized
