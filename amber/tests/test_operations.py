"""Operational workflow, governance, integrations."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.operations import CaseWorkflowUpdateBody
from app.models.schemas import AnalyzeRequest, CaseWorkflowRequest, TransactionRecord
from app.services.audit_log import append_audit_event, verify_audit_chain
from app.services.rbac import RbacDenied, assert_export, assert_workflow_action
from app.services.scheduled_import import ScheduledImportService
from app.services.workflow import apply_workflow_action, seed_workflow, validate_disposition
from app.xai.engine import XAIEngine
from app.core.config import get_settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _sample_request() -> AnalyzeRequest:
    return AnalyzeRequest(
        mode="fiat",
        jurisdiction="BY",
        alert_id="OPS-001",
        focus_transactions=[
            TransactionRecord(ts=datetime(2026, 5, 10, 12, 0, 0), amount="1000", direction="in", counterparty="A")
        ],
    )


def test_audit_chain_is_deterministic() -> None:
    events = []
    append_audit_event(events, event_type="upload", actor_id="ops", details={"file": "a.csv"})
    append_audit_event(events, event_type="analyze", actor_id="ops", details={"score": 42})
    assert verify_audit_chain(events)
    assert events[1].prev_hash == events[0].event_hash


def test_rbac_denies_readonly_close() -> None:
    with pytest.raises(RbacDenied):
        assert_workflow_action("readonly", "close")


def test_disposition_requires_notes() -> None:
    with pytest.raises(ValueError):
        validate_disposition("escalate_internal", review_notes=None, actor_id="analyst@bank")


def test_workflow_assignment_and_close_requires_supervisor() -> None:
    engine = XAIEngine(get_settings())
    req = _sample_request()
    analysis = engine.analyze_deterministic(req, request_id="wf-1")
    seed_workflow(source=req, analysis=analysis, request_id="wf-1")
    updated = apply_workflow_action(
        source=req,
        analysis=analysis,
        action="assign",
        actor_role="analyst",
        actor_id="analyst@bank",
        assignee="reviewer@bank",
    )
    assert updated.meta.workflow.assigned_to == "reviewer@bank"
    with pytest.raises((ValueError, RbacDenied)):
        apply_workflow_action(
            source=req,
            analysis=updated,
            action="close",
            actor_role="analyst",
            actor_id="analyst@bank",
        )


def test_queue_summary_endpoint(client: TestClient) -> None:
    payload = {
        "cases": [
            {"review_status": "pending"},
            {"review_status": "escalated"},
            {"review_status": "escalated"},
        ]
    }
    res = client.post("/api/v1/case/queue/summary", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 3
    assert data["escalated"] == 2


def test_webhook_ingest(client: TestClient) -> None:
    body = {
        "mode": "fiat",
        "jurisdiction": "BY",
        "transactions": [
            {
                "timestamp": "2026-05-10T10:00:00",
                "amount": "1200",
                "direction": "in",
                "counterparty": "ACME",
            }
        ],
    }
    res = client.post("/api/v1/ingest/webhook", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data["normalized_request"]["focus_transactions"]


def test_xlsx_ingest(client: TestClient) -> None:
    pytest.importorskip("openpyxl")
    frame = pd.DataFrame(
        {
            "timestamp": ["2026-05-10T10:00:00"],
            "amount": ["500"],
            "direction": ["in"],
            "counterparty": ["ACME"],
        }
    )
    buf = io.BytesIO()
    frame.to_excel(buf, index=False)
    buf.seek(0)
    res = client.post(
        "/api/v1/ingest/xlsx",
        data={"mode": "fiat", "jurisdiction": "BY", "focus_last_n": "1"},
        files={"file": ("sample.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert res.status_code == 200
    assert res.json()["sheets"]


def test_schedule_preview(client: TestClient) -> None:
    res = client.post(
        "/api/v1/imports/schedule/preview",
        json={"connector_name": "core-banking", "interval_hours": 24, "mode": "fiat", "jurisdiction": "BY"},
    )
    assert res.status_code == 200
    assert "next_run_preview" in res.json()


def test_workflow_api(client: TestClient) -> None:
    engine = XAIEngine(get_settings())
    req = _sample_request()
    analysis = engine.analyze_deterministic(req, request_id="api-wf")
    payload = CaseWorkflowRequest(
        source_request=req,
        analysis=analysis,
        action="assign",
        actor_id="analyst@bank",
        actor_role="analyst",
        assignee="reviewer@bank",
    ).model_dump(mode="json")
    res = client.post("/api/v1/case/workflow", json=payload, headers={"X-Amber-Role": "analyst"})
    assert res.status_code == 200
    assert res.json()["meta"]["workflow"]["assigned_to"] == "reviewer@bank"


def test_console_has_queue_hooks() -> None:
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
    assert "caseQueuePanel" in html
    assert "queueCounters" in html
    assert "amberRole" in html
    assert "governanceAuditPanel" in html
