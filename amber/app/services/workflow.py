"""Deterministic case queue workflow (embedded in analysis artifact, stateless)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.operations import (
    AmberRole,
    ApprovalRecord,
    CaseQueueStatus,
    CaseWorkflowState,
    DispositionCode,
    QueuePriority,
    WorkflowAction,
)
from app.models.schemas import AnalyzeRequest, AnalyzeResponse, SeverityBand
from app.services.audit_log import append_audit_event, append_lifecycle
from app.services.rbac import assert_workflow_action

_STATUS_TRANSITIONS: dict[CaseQueueStatus, set[CaseQueueStatus]] = {
    "pending": {"triage", "under_review", "escalated", "rejected"},
    "triage": {"under_review", "escalated", "rejected", "pending"},
    "under_review": {"escalated", "approved", "rejected", "closed"},
    "escalated": {"under_review", "approved", "rejected", "closed"},
    "approved": {"closed"},
    "rejected": {"closed", "triage"},
    "closed": set(),
}

_DISPOSITION_REQUIRES_NOTES = {
    "escalate_internal",
    "external_reporting_required",
    "manual_followup",
}

_CONTRADICTORY_DISPOSITIONS = {
    frozenset({"false_positive", "external_reporting_required"}),
    frozenset({"false_positive", "escalate_internal"}),
    frozenset({"insufficient_evidence", "external_reporting_required"}),
}


def _severity_to_priority(severity: SeverityBand) -> QueuePriority:
    if severity == "critical":
        return "critical"
    if severity == "high":
        return "high"
    if severity == "medium":
        return "normal"
    return "low"


def _sla_hours(severity: SeverityBand) -> int:
    return {"critical": 4, "high": 24, "medium": 72, "low": 168}.get(severity, 72)


def seed_workflow(
    *,
    source: AnalyzeRequest,
    analysis: AnalyzeResponse,
    request_id: str | None,
) -> None:
    now = datetime.now(timezone.utc)
    case_id = source.alert_id or request_id or "case-unknown"
    severity = analysis.anomaly.severity or "low"
    workflow = CaseWorkflowState(
        case_id=case_id,
        created_at=now,
        updated_at=now,
        review_status="pending",
        severity=severity,
        queue_priority=_severity_to_priority(severity),
        jurisdiction=source.jurisdiction,
        mode=source.mode,
        sla_hours_remaining=_sla_hours(severity),
    )
    analysis.meta.workflow = workflow
    if analysis.meta.governance is None:
        from app.models.operations import GovernanceMetadata

        analysis.meta.governance = GovernanceMetadata(jurisdiction=source.jurisdiction)
    append_lifecycle(analysis.meta.lifecycle_events, event="created", note="Case artifact initialized")
    append_lifecycle(analysis.meta.lifecycle_events, event="analyzed", note="Deterministic analysis completed")
    append_audit_event(
        analysis.meta.audit_events,
        event_type="analyze",
        details={
            "case_id": case_id,
            "anomaly_score": analysis.anomaly.anomaly_score,
            "severity": severity,
            "evidence_count": len(analysis.anomaly.evidence),
        },
    )
    analysis.meta.review_status = workflow.review_status  # type: ignore[assignment]


def validate_disposition(
    disposition: DispositionCode | None,
    *,
    review_notes: str | None,
    actor_id: str | None,
) -> None:
    if disposition is None:
        return
    if not actor_id or not actor_id.strip():
        raise ValueError("Disposition requires analyst attribution (actor_id).")
    if disposition in _DISPOSITION_REQUIRES_NOTES and not (review_notes or "").strip():
        raise ValueError(f"Disposition '{disposition}' requires review notes.")


def apply_workflow_action(
    *,
    source: AnalyzeRequest,
    analysis: AnalyzeResponse,
    action: WorkflowAction,
    actor_role: AmberRole,
    actor_id: str,
    assignee: str | None = None,
    review_status: CaseQueueStatus | None = None,
    disposition_code: DispositionCode | None = None,
    escalation_reason: str | None = None,
    review_notes: str | None = None,
) -> AnalyzeResponse:
    assert_workflow_action(actor_role, action)
    if analysis.meta.workflow is None:
        seed_workflow(source=source, analysis=analysis, request_id=analysis.meta.request_id)
    workflow = analysis.meta.workflow
    assert workflow is not None
    now = datetime.now(timezone.utc)

    if action in {"assign", "reassign"}:
        if not assignee or not assignee.strip():
            raise ValueError("assignee is required for assignment actions.")
        workflow.assigned_to = assignee.strip()
        workflow.updated_at = now
        append_audit_event(
            analysis.meta.audit_events,
            event_type="assignment",
            actor_role=actor_role,
            actor_id=actor_id,
            details={"action": action, "assignee": workflow.assigned_to},
        )
        append_lifecycle(
            analysis.meta.lifecycle_events,
            event="reviewed",
            actor_id=actor_id,
            note=f"{action} -> {workflow.assigned_to}",
        )

    if action == "set_disposition":
        validate_disposition(disposition_code, review_notes=review_notes, actor_id=actor_id)
        if disposition_code and workflow.disposition_code and disposition_code != workflow.disposition_code:
            pair = frozenset({workflow.disposition_code, disposition_code})
            if pair in _CONTRADICTORY_DISPOSITIONS:
                raise ValueError("Contradictory disposition transition is not allowed.")
        workflow.disposition_code = disposition_code
        workflow.updated_at = now
        append_audit_event(
            analysis.meta.audit_events,
            event_type="disposition",
            actor_role=actor_role,
            actor_id=actor_id,
            details={"disposition_code": disposition_code},
        )

    if action == "escalate":
        workflow.review_status = "escalated"
        workflow.escalation_reason = escalation_reason or "Elevated review requirement (analyst-initiated)."
        workflow.queue_priority = "high"
        workflow.updated_at = now
        append_audit_event(
            analysis.meta.audit_events,
            event_type="escalated",
            actor_role=actor_role,
            actor_id=actor_id,
            details={"reason": workflow.escalation_reason},
        )
        append_lifecycle(analysis.meta.lifecycle_events, event="escalated", actor_id=actor_id, note=workflow.escalation_reason)

    if action in {"set_status", "approve", "supervisor_approve", "close"}:
        target = review_status
        if action == "approve":
            target = "approved"
        if action == "supervisor_approve":
            target = "approved"
            workflow.supervisor_id = actor_id
            workflow.approval_chain.append(
                ApprovalRecord(role=actor_role, actor=actor_id, action="supervisor_approve", at=now, note=review_notes)
            )
        if action == "close":
            target = "closed"
            if actor_role != "supervisor":
                raise ValueError("Only supervisor may close a case.")
        if target:
            allowed = _STATUS_TRANSITIONS.get(workflow.review_status, set())
            if target not in allowed and target != workflow.review_status:
                raise ValueError(f"Invalid status transition: {workflow.review_status} -> {target}")
            workflow.review_status = target
            workflow.updated_at = now
            analysis.meta.review_status = target  # type: ignore[assignment]
            append_audit_event(
                analysis.meta.audit_events,
                event_type="review" if target not in {"approved", "closed"} else target,  # type: ignore[arg-type]
                actor_role=actor_role,
                actor_id=actor_id,
                details={"review_status": target},
            )
            if target == "approved":
                append_lifecycle(analysis.meta.lifecycle_events, event="approved", actor_id=actor_id)
            if target == "closed":
                append_lifecycle(analysis.meta.lifecycle_events, event="closed", actor_id=actor_id)

    if review_notes:
        analysis.meta.review_notes = review_notes
    if actor_id:
        analysis.meta.reviewed_by = actor_id
    analysis.meta.reviewed_at = now
    workflow.updated_at = now
    return analysis


def queue_summary(cases: list[dict[str, Any]]) -> dict[str, int]:
    counters = {
        "total": len(cases),
        "pending": 0,
        "triage": 0,
        "under_review": 0,
        "escalated": 0,
        "approved": 0,
        "rejected": 0,
        "closed": 0,
    }
    for item in cases:
        status = str(item.get("review_status") or "pending")
        if status in counters:
            counters[status] += 1
    return counters
