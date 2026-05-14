from __future__ import annotations

import io
import json
import zipfile

import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _minimal_body() -> dict:
    return {
        "mode": "fiat",
        "jurisdiction": "BY",
        "historical_transactions": [],
        "focus_transactions": [{"amount": 50, "direction": "in", "asset_type": "fiat"}],
    }


def test_export_case_zip_contains_required_files(client: TestClient) -> None:
    analyze = client.post("/api/v1/analyze", json=_minimal_body())
    assert analyze.status_code == 200
    analysis = analyze.json()
    analysis["meta"]["review_status"] = "analyst_confirmed"
    analysis["meta"]["review_notes"] = "Reviewed in test."
    analysis["meta"]["reviewed_by"] = "qa@example.com"

    exported = client.post(
        "/api/v1/export/case",
        json={"source_request": _minimal_body(), "analysis": analysis},
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/zip")
    assert exported.headers.get("x-amber-bundle-sha256")

    with zipfile.ZipFile(io.BytesIO(exported.content), "r") as archive:
        names = set(archive.namelist())
        assert {
            "normalized_request.json",
            "deterministic_evidence.json",
            "anomaly.json",
            "traces.json",
            "reporter.json",
            "sar.txt",
            "audit_manifest.json",
        }.issubset(names)
        manifest = json.loads(archive.read("audit_manifest.json").decode("utf-8"))
        assert analysis["meta"]["request_id"] == manifest["request_id"]
        assert manifest["review"]["reviewed_by"] == "qa@example.com"
        assert manifest["signature"]["algorithm"] == "hmac-sha256"
        assert manifest["signature"]["signature"]


def test_replay_bundle_matches_export(client: TestClient) -> None:
    analyze = client.post("/api/v1/analyze", json=_minimal_body())
    assert analyze.status_code == 200
    analysis = analyze.json()
    exported = client.post(
        "/api/v1/export/case",
        json={"source_request": _minimal_body(), "analysis": analysis},
    )
    assert exported.status_code == 200

    replay = client.post(
        "/api/v1/replay",
        files={"file": ("case.zip", exported.content, "application/zip")},
    )
    assert replay.status_code == 200
    data = replay.json()
    assert data["llm_called"] is False
    assert data["replay_status"] == "match"
    assert data["drift_detected"] is False
    assert data["hash_checks"]
    assert any(item["name"] == "manifest_signature" and item["matches"] is True for item in data["hash_checks"])


def test_replay_detects_tampered_bundle(client: TestClient) -> None:
    analyze = client.post("/api/v1/analyze", json=_minimal_body())
    analysis = analyze.json()
    exported = client.post(
        "/api/v1/export/case",
        json={"source_request": _minimal_body(), "analysis": analysis},
    )
    raw = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(exported.content), "r") as src, zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            content = src.read(name)
            if name == "sar.txt":
                content = content + b"\nTAMPERED\n"
            dst.writestr(name, content)

    replay = client.post(
        "/api/v1/replay",
        files={"file": ("case-tampered.zip", raw.getvalue(), "application/zip")},
    )
    assert replay.status_code == 200
    data = replay.json()
    assert data["drift_detected"] is True
    assert data["replay_status"] in {"drift", "invalid_bundle"}


def test_timeout_falls_back_to_emergency_response(client: TestClient) -> None:
    settings = client.app.state.settings
    engine = client.app.state.engine
    original_timeout = settings.request_timeout_seconds
    original_complete_json = engine.llm.complete_json

    async def fake_timeout(**kwargs):
        raise TimeoutError("request_deadline_exceeded")

    settings.request_timeout_seconds = 5.0
    engine.llm.complete_json = fake_timeout  # type: ignore[method-assign]
    try:
        response = client.post("/api/v1/analyze", json=_minimal_body())
    finally:
        settings.request_timeout_seconds = original_timeout
        engine.llm.complete_json = original_complete_json  # type: ignore[method-assign]

    assert response.status_code == 200
    data = response.json()
    assert data["meta"]["emergency_mode"] is True
    assert any(trace["error_code"] == "request_deadline_exceeded" for trace in data["meta"]["stage_traces"])


def test_sar_docx_export(client: TestClient) -> None:
    analyze = client.post("/api/v1/analyze", json=_minimal_body())
    analysis = analyze.json()
    exported = client.post(
        "/api/v1/export/sar?format=docx",
        json={"source_request": _minimal_body(), "analysis": analysis},
    )
    assert exported.status_code == 200
    assert "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in exported.headers["content-type"]
    with zipfile.ZipFile(io.BytesIO(exported.content), "r") as archive:
        assert "word/document.xml" in set(archive.namelist())


def test_overload_returns_deterministic_response(client: TestClient) -> None:
    guard = client.app.state.runtime_guard
    assert guard is not None
    original_try_acquire = guard.try_acquire

    async def always_reject():
        return False

    guard.try_acquire = always_reject  # type: ignore[method-assign]
    try:
        response = client.post("/api/v1/analyze", json=_minimal_body())
    finally:
        guard.try_acquire = original_try_acquire  # type: ignore[method-assign]

    assert response.status_code == 200
    data = response.json()
    assert data["meta"]["emergency_mode"] is True
    assert data["meta"]["operating_reason"] == "overload_rejected"
