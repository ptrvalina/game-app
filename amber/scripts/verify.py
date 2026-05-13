"""
Быстрая проверка: сервер поднимается, health, консоль, analyze (Emergency без ключей LLM).

Запуск из каталога amber:
    python scripts/verify.py

Полный набор тестов:
    pip install -r requirements-dev.txt
    python -m pytest tests -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Изолированная проверка без обязательного API-ключа
os.environ.pop("AMBER_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.setdefault("AMBER_ENV", "test")

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from starlette.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def main() -> None:
    with TestClient(app, raise_server_exceptions=True) as client:
        h = client.get("/health")
        assert h.status_code == 200, h.text
        assert h.json().get("status") == "ok"

        r = client.get("/ready")
        assert r.status_code == 200, r.text

        c = client.get("/console")
        assert c.status_code == 200, c.text
        assert b"Amber" in c.content

        sample = {
            "mode": "fiat",
            "jurisdiction": "BY",
            "alert_id": "VERIFY-1",
            "historical_transactions": [],
            "focus_transactions": [
                {"amount": 100, "direction": "in", "asset_type": "fiat"},
            ],
        }
        a = client.post("/api/v1/analyze", json=sample)
        assert a.status_code == 200, a.text
        body = a.json()
        assert "anomaly" in body and "meta" in body and "reporter" in body
        assert body["anomaly"]["anomaly_score"] >= 0
        assert body["jurisdiction"] == "BY"

    print("verify.py: OK (health, ready, console, POST /api/v1/analyze)")


if __name__ == "__main__":
    main()
