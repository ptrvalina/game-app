"""Зависимости FastAPI (движок из state приложения)."""
from __future__ import annotations

from fastapi import Request

from app.xai.engine import XAIEngine


def get_engine(request: Request) -> XAIEngine:
    eng = getattr(request.app.state, "engine", None)
    if eng is None:
        raise RuntimeError("XAIEngine не инициализирован (lifespan)")
    return eng
