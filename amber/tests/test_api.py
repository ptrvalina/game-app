"""Смоук-тесты HTTP API Amber."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "X-Request-ID" in r.headers
    assert r.headers.get("X-Amber-Version")


def test_ready(client: TestClient) -> None:
    r = client.get("/ready")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in {"ready", "degraded", "emergency-only"}
    assert "llm" in data
    assert "usable_provider_count" in data["llm"]
    assert "providers" not in data["llm"]
    assert "runtime_guard" in data
    assert "demo_mode" in data


def test_telemetry(client: TestClient) -> None:
    r = client.get("/telemetry")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "telemetry" in data


def test_console(client: TestClient) -> None:
    r = client.get("/console")
    assert r.status_code == 200
    assert b"Amber" in r.content


def test_favicon_no_body(client: TestClient) -> None:
    r = client.get("/favicon.ico")
    assert r.status_code == 204


def test_openapi(client: TestClient) -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "openapi" in r.json()


def test_v1_health(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200


def test_analyze_v1_minimal(client: TestClient) -> None:
    body = {
        "mode": "fiat",
        "jurisdiction": "BY",
        "historical_transactions": [],
        "focus_transactions": [{"amount": 50, "direction": "in", "asset_type": "fiat"}],
    }
    r = client.post("/api/v1/analyze", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "fiat"
    assert data["jurisdiction"] == "BY"
    assert "anomaly" in data
    assert "reporter" in data
    assert "sar_body" in data["reporter"]
    assert "meta" in data
    assert "stage_traces" in data["meta"]
    assert data["meta"].get("human_review_required") is True
    assert data["reporter"].get("human_review_required") is True


def test_analyze_legacy_deprecated(client: TestClient) -> None:
    body = {
        "mode": "fiat",
        "jurisdiction": "RU",
        "historical_transactions": [],
        "focus_transactions": [{"amount": 10, "direction": "in", "asset_type": "fiat"}],
    }
    r = client.post("/analyze", json=body)
    assert r.status_code == 200
    assert r.json()["jurisdiction"] == "RU"


def test_root_redirects_to_console(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/console" in (r.headers.get("location") or "")


def test_analyze_validation_empty_focus(client: TestClient) -> None:
    """Пустой focus — 422 и единый формат error."""
    body = {
        "mode": "fiat",
        "jurisdiction": "BY",
        "historical_transactions": [],
        "focus_transactions": [],
    }
    r = client.post("/api/v1/analyze", json=body)
    assert r.status_code == 422
    data = r.json()
    assert "error" in data
    assert data["error"]["code"] == "validation_error"
    assert all("input" not in item for item in data["error"].get("details") or [])


def test_analyze_validation_unknown_field_forbidden(client: TestClient) -> None:
    body = {
        "mode": "fiat",
        "jurisdiction": "BY",
        "historical_transactions": [],
        "focus_transactions": [{"amount": 10, "direction": "in", "asset_type": "fiat"}],
        "unexpected": "field",
    }
    r = client.post("/api/v1/analyze", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
