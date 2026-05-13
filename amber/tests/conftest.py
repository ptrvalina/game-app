"""Pytest: окружение до импорта приложения."""
from __future__ import annotations

import os

# Без API-ключа в тестах (иначе 401 на /analyze)
os.environ.pop("AMBER_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.setdefault("AMBER_ENV", "test")

from app.core.config import get_settings

get_settings.cache_clear()
