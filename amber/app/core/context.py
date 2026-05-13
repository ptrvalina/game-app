"""Контекст запроса (без персистентности — только на время обработки HTTP)."""
from __future__ import annotations

import contextvars
from typing import Any

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("amber_request_id", default=None)


def set_request_id(rid: str | None) -> contextvars.Token[Any]:
    return _request_id.set(rid)


def reset_request_id(token: contextvars.Token[Any]) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    return _request_id.get()
