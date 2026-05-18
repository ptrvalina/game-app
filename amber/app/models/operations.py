"""Operational workflow, governance, and integration metadata (stateless, replay-safe)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Mode = Literal["fiat", "crypto", "cross"]
Jurisdiction = Literal["RU", "BY", "EU"]
SeverityBand = Literal["low", "medium", "high", "critical"]

CaseQueueStatus = Literal[
    "pending",
    "triage",
    "under_review",
    "escalated",
    "approved",
    "rejected",
    "closed",
]
DispositionCode = Literal[
    "false_positive",
    "insufficient_evidence",
    "escalate_internal",
    "monitor_activity",
    "manual_followup",
    "external_reporting_required",
]
AmberRole = Literal["analyst", "reviewer", "supervisor", "auditor", "readonly"]
QueuePriority = Literal["low", "normal", "high", "critical"]
AuditEventType = Literal[
    "upload",
    "analyze",
    "review",
    "escalated",
    "replayed",
    "exported",
    "approved",
    "closed",
    "assignment",
    "disposition",
    "validator_downgrade",
    "emergency_mode",
    "access_denied",
]
LifecycleEventName = Literal[
    "created",
    "analyzed",
    "reviewed",
    "escalated",
    "replayed",
    "exported",
    "approved",
    "closed",
]
DbConnectorType = Literal["postgresql", "mysql", "sqlite"]
WorkflowAction = Literal[
    "assign",
    "reassign",
    "set_status",
    "set_disposition",
    "escalate",
    "approve",
    "close",
    "supervisor_approve",
]


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovalRecord(StrictBaseModel):
    role: AmberRole
    actor: str = Field(..., max_length=128)
    action: str = Field(..., max_length=64)
    at: datetime
    note: str | None = Field(default=None, max_length=512)


class CaseWorkflowState(StrictBaseModel):
    case_id: str = Field(..., max_length=128)
    created_at: datetime
    updated_at: datetime
    assigned_to: str | None = Field(default=None, max_length=128)
    review_status: CaseQueueStatus = "pending"
    severity: SeverityBand = "low"
    queue_priority: QueuePriority = "normal"
    escalation_reason: str | None = Field(default=None, max_length=512)
    disposition_code: DispositionCode | None = None
    jurisdiction: Jurisdiction
    mode: Mode
    supervisor_id: str | None = Field(default=None, max_length=128)
    sla_hours_remaining: int | None = Field(default=None, ge=0, le=720)
    approval_chain: list[ApprovalRecord] = Field(default_factory=list, max_length=32)


class AuditEvent(StrictBaseModel):
    sequence: int = Field(..., ge=1, le=10_000)
    event_type: AuditEventType
    occurred_at: datetime
    actor_role: AmberRole | None = None
    actor_id: str | None = Field(default=None, max_length=128)
    details: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str = Field(..., max_length=128)
    event_hash: str = Field(..., max_length=128)


class LifecycleEvent(StrictBaseModel):
    event: LifecycleEventName
    occurred_at: datetime
    actor_id: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=512)


class GovernanceMetadata(StrictBaseModel):
    retention_class: str = Field(default="pilot_90d", max_length=64)
    data_classification: str = Field(default="internal", max_length=64)
    pii_present: bool = True
    export_restrictions: list[str] = Field(default_factory=lambda: ["human_review_required"], max_length=16)
    regulator_visibility: str = Field(default="supervised_pilot", max_length=64)
    jurisdiction: Jurisdiction


class ConnectorProvenance(StrictBaseModel):
    source_type: str = Field(..., max_length=64)
    connector_name: str = Field(..., max_length=128)
    imported_by: str | None = Field(default=None, max_length=128)
    import_timestamp: datetime
    normalization_report_sha256: str | None = Field(default=None, max_length=128)
    malformed_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    deterministic_hash: str = Field(..., max_length=128)


class ExportAccessLogEntry(StrictBaseModel):
    export_type: str = Field(..., max_length=64)
    actor_role: AmberRole | None = None
    actor_id: str | None = Field(default=None, max_length=128)
    occurred_at: datetime
    artifact_digest: str | None = Field(default=None, max_length=128)


class ScheduledImportMetadata(StrictBaseModel):
    schedule_id: str = Field(..., max_length=128)
    connector_name: str = Field(..., max_length=128)
    interval_hours: int = Field(..., ge=1, le=24 * 30)
    next_run_preview: datetime
    last_run_preview: datetime | None = None
    deterministic_fingerprint: str = Field(..., max_length=128)


class WebhookIngestRequest(StrictBaseModel):
    mode: Mode
    jurisdiction: Jurisdiction
    alert_id: str | None = Field(default=None, max_length=128)
    client_id_external: str | None = Field(default=None, max_length=128)
    transactions: list[dict[str, Any]] = Field(..., min_length=1, max_length=500)
    webhook_signature: str | None = Field(default=None, max_length=256)
    imported_by: str | None = Field(default=None, max_length=128)


class DbImportPreviewRequest(StrictBaseModel):
    connector_type: DbConnectorType
    connection_uri: str = Field(..., max_length=2048)
    query: str = Field(..., max_length=4000)
    mode: Mode
    jurisdiction: Jurisdiction
    focus_last_n: int = Field(default=12, ge=1, le=500)
    table_name: str | None = Field(default=None, max_length=128)
    imported_by: str | None = Field(default=None, max_length=128)


class ScheduledImportPreviewRequest(StrictBaseModel):
    connector_name: str = Field(..., max_length=128)
    interval_hours: int = Field(..., ge=1, le=24 * 30)
    mode: Mode
    jurisdiction: Jurisdiction


class XlsxSheetPreview(StrictBaseModel):
    sheet_name: str = Field(..., max_length=128)
    row_count: int = Field(default=0, ge=0)
    columns: list[str] = Field(default_factory=list, max_length=64)


class CaseWorkflowUpdateBody(StrictBaseModel):
    """Workflow mutation request; analysis payloads validated in router."""

    action: WorkflowAction
    actor_id: str = Field(..., max_length=128)
    actor_role: AmberRole
    assignee: str | None = Field(default=None, max_length=128)
    review_status: CaseQueueStatus | None = None
    disposition_code: DispositionCode | None = None
    escalation_reason: str | None = Field(default=None, max_length=512)
    review_notes: str | None = Field(default=None, max_length=4000)
