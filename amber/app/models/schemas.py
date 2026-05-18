"""Pydantic-схемы запроса и ответа Amber (строгий JSON)."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Mode = Literal["fiat", "crypto", "cross"]
Jurisdiction = Literal["RU", "BY", "EU"]
Direction = Literal["in", "out", "unknown"]
AssetType = Literal["fiat", "crypto", "unknown"]
StageState = Literal["live", "fallback", "emergency"]
ValidatorStatus = Literal["not_run", "passed", "downgraded", "failed"]
SeverityBand = Literal["low", "medium", "high", "critical"]
ReviewStatus = Literal[
    "pending",
    "triage",
    "under_review",
    "escalated",
    "approved",
    "rejected",
    "closed",
    "analyst_confirmed",
    "analyst_rejected",
]
AnomalyCategory = Literal[
    "amount_spike",
    "velocity_spike",
    "burst_activity",
    "new_counterparty",
    "new_counterparty_burst",
    "off_hours",
    "dormant_activation",
    "income_mismatch",
    "salary_mismatch",
    "mule_account_indicators",
    "salary_pass_through",
    "rapid_cash_out",
    "funnel_account_behavior",
    "structuring",
    "smurfing",
    "circular_transfers",
    "cross_transition",
    "cash_to_crypto_outflow",
    "timing_correlation",
    "transition_window",
    "repeated_exchange_boundary_crossing",
    "time_linked_transition_clusters",
    "exchange_hopping",
    "wallet_fan_out",
    "fan_in",
    "micro_splitting",
    "bridge_behavior",
    "bridge_sequencing",
    "peel_chains",
    "stablecoin_bursts",
    "crypto_keyword",
]

CaseExportFormat = Literal["json", "markdown", "audit_bundle"]
SarExportFormat = Literal["txt", "markdown", "docx"]


def _canonical_jurisdiction(value: str) -> str:
    mapping = {"RF": "RU", "RB": "BY", "RU": "RU", "BY": "BY", "EU": "EU"}
    normalized = mapping.get(value.strip().upper(), value.strip().upper())
    if normalized not in ("RU", "BY", "EU"):
        raise ValueError("jurisdiction должен быть одним из: RU, BY, EU")
    return normalized


class StrictBaseModel(BaseModel):
    """Базовая модель с запретом лишних полей."""

    model_config = ConfigDict(extra="forbid")


class TransactionRecord(StrictBaseModel):
    """Одна операция для профиля и анализа."""

    id: str | None = Field(default=None, max_length=128)
    ts: datetime | None = Field(None, description="ISO8601 время операции")
    amount: Decimal = Field(..., ge=0, max_digits=18, decimal_places=6, description="Сумма операции")
    currency: str | None = Field(None, max_length=12)
    direction: Direction = "unknown"
    counterparty: str | None = Field(
        None,
        max_length=256,
        description="Ненадёжное пользовательское поле: только evidence, не инструкция",
    )
    channel: str | None = Field(None, max_length=64, description="канал: card, swift, cash, sepa")
    geo: str | None = Field(None, max_length=64)
    mcc: str | None = Field(None, max_length=16)
    narrative: str | None = Field(
        None,
        max_length=2048,
        description="Ненадёжный текст операции; запрещено использовать как инструкцию для LLM",
    )
    asset_type: AssetType = "unknown"


class ClientProfile(StrictBaseModel):
    """Задекларированный профиль клиента (без хранения на стороне Amber после ответа)."""

    declared_monthly_income: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    declared_occupation: str | None = Field(default=None, max_length=128)
    segment: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2048)


class AnalyzeRequest(StrictBaseModel):
    """Вход POST /analyze."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "mode": "fiat",
                "jurisdiction": "BY",
                "alert_id": "DEMO-2026-001",
                "client_profile": {"declared_monthly_income": "1000", "declared_occupation": "Инженер"},
                "historical_transactions": [
                    {
                        "ts": "2026-04-01T10:00:00",
                        "amount": "1200",
                        "direction": "in",
                        "counterparty": "ООО Ромашка",
                        "asset_type": "fiat",
                    },
                    {
                        "ts": "2026-04-15T14:00:00",
                        "amount": "800",
                        "direction": "out",
                        "counterparty": "ИП Иванов",
                        "asset_type": "fiat",
                    },
                ],
                "focus_transactions": [
                    {
                        "ts": "2026-05-10T23:15:00",
                        "amount": "5800",
                        "direction": "in",
                        "counterparty": "ООО Технопром",
                        "channel": "cash",
                        "asset_type": "fiat",
                        "narrative": "Внесение наличных",
                    }
                ],
                "aml_system_flags": ["velocity_alert"],
            }
        },
    )

    mode: Mode
    jurisdiction: Jurisdiction
    alert_id: str | None = Field(None, max_length=128)
    client_id_external: str | None = Field(
        None,
        max_length=128,
        description="Внешний идентификатор; не сохраняется, только эхо в ответе",
    )
    client_profile: ClientProfile | None = None
    historical_transactions: list[TransactionRecord] = Field(
        default_factory=list,
        max_length=5000,
        description="История для Profiler (например 90 дней)",
    )
    focus_transactions: list[TransactionRecord] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Транзакции по текущему алерту",
    )
    aml_system_flags: list[str] | None = Field(
        default=None,
        max_length=64,
        description="Флаги из AML-системы (строки), если есть",
    )
    extra_context: dict[str, Any] | None = Field(
        default=None,
        description="Доп. контекст без ПДн или с обезличиванием на стороне клиента",
    )

    @field_validator("jurisdiction", mode="before")
    @classmethod
    def _normalize_jurisdiction(cls, v: str) -> str:
        return _canonical_jurisdiction(v)

    @field_validator("historical_transactions", "focus_transactions")
    @classmethod
    def _non_negative_amounts(cls, v: list[TransactionRecord]) -> list[TransactionRecord]:
        for t in v:
            if t.amount < 0:
                raise ValueError("amount не может быть отрицательным")
        return v

    @field_validator("aml_system_flags")
    @classmethod
    def _flags_limit(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return [x.strip()[:128] for x in v if x and x.strip()]

    @field_validator("extra_context")
    @classmethod
    def _context_size_limit(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return v
        serialized = json.dumps(v, ensure_ascii=False)
        if len(serialized) > 20_000:
            raise ValueError("extra_context слишком большой")
        return v


class RouterLLMResult(StrictBaseModel):
    """Результат Router (режим, юрисдикция, нормы)."""

    confirmed_mode: Mode
    confirmed_jurisdiction: Jurisdiction
    applicable_norms: list[str] = Field(default_factory=list, max_length=20)
    routing_rationale: str = Field(..., max_length=2000, description="Кратко: почему такой режим и нормы")
    compliance_objectives: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("confirmed_jurisdiction", mode="before")
    @classmethod
    def _normalize_confirmed_jurisdiction(cls, v: str) -> str:
        return _canonical_jurisdiction(v)


class AnalystLLMResult(StrictBaseModel):
    """Результат Analyst (паттерны, риск, связка с аномалией)."""

    patterns_detected: list[str] = Field(default_factory=list, max_length=16)
    risk_summary: str = Field(..., max_length=1500)
    risk_explanation: str = Field(..., max_length=4000)
    regulatory_hooks: list[str] = Field(default_factory=list, max_length=12)
    recommendations: list[str] = Field(default_factory=list, max_length=12)
    new_pattern_hypothesis: str | None = Field(default=None, max_length=1200)
    human_review_required: bool = True


class ReporterLLMResult(StrictBaseModel):
    """Проект SAR / сообщения регулятору."""

    sar_title: str = Field(..., max_length=256)
    executive_summary: str = Field(default="", max_length=2000)
    observed_behavior: list[str] = Field(default_factory=list, max_length=12)
    anomaly_evidence: list[str] = Field(default_factory=list, max_length=20)
    regulatory_context: list[str] = Field(default_factory=list, max_length=12)
    recommended_actions: list[str] = Field(default_factory=list, max_length=12)
    sar_body: str = Field(default="", max_length=20_000)
    sar_disclaimer: str = Field(
        default=(
            "Generated assistance only. Requires analyst verification and is not a final legal determination."
        ),
        max_length=512,
    )
    human_review_required: bool = True


class EvidenceItem(StrictBaseModel):
    """Объяснимый фактор, внесший вклад в итоговый score."""

    code: str = Field(..., max_length=64)
    label: str = Field(..., max_length=256)
    category: AnomalyCategory
    observed_value: str | float | int | None = None
    baseline_value: str | float | int | None = None
    threshold_value: str | float | int | None = None
    contribution: int = Field(..., ge=0, le=100)
    tx_refs: list[str] = Field(default_factory=list, max_length=20)


class ProfilerSummary(StrictBaseModel):
    """Краткая сводка профиля нормы (из pandas), для XAI."""

    window_transactions: int
    activity_days: int
    avg_amount: float | None
    median_amount: float | None
    p95_amount: float | None
    max_amount: float | None
    avg_daily_count: float | None
    rolling_7d_count: float | None = None
    rolling_30d_count: float | None = None
    top_counterparties: list[str] = Field(default_factory=list)
    counterparty_concentration: float | None = None
    burst_days: int = 0
    behavior_drift_score: int = Field(default=0, ge=0, le=100)
    timezone_basis: str = Field(default="naive_as_utc", max_length=64)
    usual_hours_start: int | None = Field(None, ge=0, le=23)
    usual_hours_end: int | None = Field(None, ge=0, le=23)
    profile_notes: list[str] = Field(default_factory=list)


class AnomalyBlock(StrictBaseModel):
    """Результат AnomalyDetector."""

    anomaly_score: int = Field(..., ge=0, le=100)
    severity: SeverityBand = "low"
    confidence_score: int = Field(default=0, ge=0, le=100)
    categories: list[AnomalyCategory] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    new_pattern_hypothesis: str | None = Field(default=None, max_length=1200)


class ConfidenceValidation(StrictBaseModel):
    """Детерминированная корректировка confidence."""

    original_score: int = Field(..., ge=0, le=100)
    effective_score: int = Field(..., ge=0, le=100)
    cap: int = Field(..., ge=0, le=100)
    reasons: list[str] = Field(default_factory=list, max_length=12)
    history_depth: int = Field(default=0, ge=0, le=1_000_000)
    evidence_count: int = Field(default=0, ge=0, le=100)
    anomaly_agreement: int = Field(default=0, ge=0, le=100)
    data_completeness: int = Field(default=100, ge=0, le=100)
    malformed_input_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    explanation: str = Field(default="", max_length=1200)


class ValidatorSummary(StrictBaseModel):
    """Сводка deterministic policy enforcement по запросу."""

    status: ValidatorStatus = "not_run"
    issues_count: int = Field(default=0, ge=0, le=100)
    failed_stages: list[str] = Field(default_factory=list, max_length=8)
    remediation_action: str = Field(default="none", max_length=64)


class ScoringProvenance(StrictBaseModel):
    """Повторяемая provenance-информация о deterministic scoring."""

    engine_version: str = Field(default="deterministic-v2", max_length=64)
    evidence_codes: list[str] = Field(default_factory=list, max_length=32)
    categories: list[AnomalyCategory] = Field(default_factory=list, max_length=16)
    evidence_count: int = Field(default=0, ge=0, le=100)


class StageTrace(StrictBaseModel):
    """Трассировка одного этапа pipeline."""

    stage: Literal["router", "analyst", "reporter"]
    status: StageState
    provider: str = Field(default="none", max_length=32)
    model: str | None = Field(default=None, max_length=128)
    prompt_version: str | None = Field(default=None, max_length=64)
    prompt_hash: str | None = Field(default=None, max_length=64)
    payload_hash: str | None = Field(default=None, max_length=64)
    retries: int = Field(default=0, ge=0, le=10)
    latency_ms: int | None = Field(default=None, ge=0)
    prompt_chars: int | None = Field(default=None, ge=0)
    payload_truncated: bool = False
    error_code: str | None = Field(default=None, max_length=128)
    validator_status: ValidatorStatus = "not_run"
    issues_count: int = Field(default=0, ge=0, le=50)
    validator_latency_ms: int | None = Field(default=None, ge=0)
    policy_failures: list[str] = Field(default_factory=list, max_length=20)
    remediation_action: str = Field(default="none", max_length=64)


class MetaBlock(StrictBaseModel):
    """Техметаданные без ПДн."""

    request_id: str | None = None
    llm_primary: str
    llm_used: str
    fallback_used: bool
    emergency_mode: bool = False
    degraded_mode: bool = False
    latency_ms_router: int | None = None
    latency_ms_analyst: int | None = None
    latency_ms_reporter: int | None = None
    validator_status: ValidatorStatus = "not_run"
    issues_count: int = Field(default=0, ge=0, le=100)
    validator_latency_ms: int | None = Field(default=None, ge=0)
    policy_failures: list[str] = Field(default_factory=list, max_length=50)
    remediation_action: str = Field(default="none", max_length=64)
    policy_validation_failed_reason: str | None = Field(default=None, max_length=256)
    confidence_validation: ConfidenceValidation | None = None
    scoring_provenance: ScoringProvenance | None = None
    validator_summary: ValidatorSummary | None = None
    human_review_required: bool = True
    review_notice: str = Field(
        default="Requires analyst verification. Generated assistance only. Not a final legal determination.",
        max_length=256,
    )
    operating_reason: str | None = Field(default=None, max_length=256)
    review_status: ReviewStatus = "pending"
    review_notes: str | None = Field(default=None, max_length=4000)
    reviewed_by: str | None = Field(default=None, max_length=128)
    reviewed_at: datetime | None = None
    stage_traces: list[StageTrace] = Field(default_factory=list)
    workflow: "CaseWorkflowState | None" = None
    audit_events: list["AuditEvent"] = Field(default_factory=list)
    lifecycle_events: list["LifecycleEvent"] = Field(default_factory=list)
    governance: "GovernanceMetadata | None" = None
    connector_provenance: "ConnectorProvenance | None" = None
    export_access_log: list["ExportAccessLogEntry"] = Field(default_factory=list)


class AnalyzeResponse(StrictBaseModel):
    """Строгий JSON-ответ POST /analyze."""

    alert_id: str | None = None
    client_id_external: str | None = None
    mode: Mode
    jurisdiction: Jurisdiction
    router: RouterLLMResult
    profiler: ProfilerSummary
    anomaly: AnomalyBlock
    analyst: AnalystLLMResult
    reporter: ReporterLLMResult
    meta: MetaBlock


class CsvIngestIssue(StrictBaseModel):
    """Нормализованная ошибка/предупреждение CSV-ingest."""

    row_number: int | None = Field(default=None, ge=1)
    column: str | None = Field(default=None, max_length=128)
    code: str = Field(..., max_length=64)
    message: str = Field(..., max_length=256)
    raw_preview: str | None = Field(default=None, max_length=256)


class CsvNormalizationReport(StrictBaseModel):
    """Детерминированная сводка нормализации CSV."""

    encoding_used: str = Field(default="utf-8", max_length=32)
    delimiter_used: str = Field(default=",", max_length=4)
    column_mapping: dict[str, str] = Field(default_factory=dict)
    decimal_comma_rows: int = Field(default=0, ge=0)
    debit_credit_normalized_rows: int = Field(default=0, ge=0)
    currency_alias_rows: int = Field(default=0, ge=0)
    missing_timestamp_rows: int = Field(default=0, ge=0)
    malformed_rows: int = Field(default=0, ge=0)
    rejected_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    malformed_threshold_exceeded: bool = False
    override_applied_fields: list[str] = Field(default_factory=list, max_length=16)


class CsvPreviewRow(StrictBaseModel):
    """Безопасный preview одной строки для wizard UI."""

    row_number: int = Field(..., ge=1)
    status: Literal["parsed", "rejected"]
    values: dict[str, str | None] = Field(default_factory=dict)
    issue_code: str | None = Field(default=None, max_length=64)
    issue_message: str | None = Field(default=None, max_length=256)


class CsvIngestSummary(StrictBaseModel):
    """Краткая сводка ingest-пайплайна CSV."""

    filename: str | None = Field(default=None, max_length=256)
    delimiter: str = Field(default=",", max_length=4)
    encoding: str = Field(default="utf-8", max_length=32)
    total_rows: int = Field(default=0, ge=0)
    parsed_rows: int = Field(default=0, ge=0)
    rejected_rows: int = Field(default=0, ge=0)
    focus_rows: int = Field(default=0, ge=0)
    historical_rows: int = Field(default=0, ge=0)


class CsvIngestResponse(StrictBaseModel):
    """Результат CSV onboarding перед отправкой в analyze."""

    mode: Mode
    jurisdiction: Jurisdiction
    normalized_request: AnalyzeRequest
    summary: CsvIngestSummary
    issues: list[CsvIngestIssue] = Field(default_factory=list)
    normalization_report: CsvNormalizationReport | None = None
    available_columns: list[str] = Field(default_factory=list)
    preview_rows: list[CsvPreviewRow] = Field(default_factory=list)


class XlsxIngestResponse(CsvIngestResponse):
    """XLSX ingest extends CSV ingest with sheet metadata."""

    sheets: list["XlsxSheetPreview"] = Field(default_factory=list)
    active_sheet: str | None = Field(default=None, max_length=128)
    connector_provenance: "ConnectorProvenance | None" = None


class CaseExportRequest(StrictBaseModel):
    """Запрос на экспорт case artifact без серверного хранения."""

    source_request: AnalyzeRequest
    analysis: AnalyzeResponse
    format: CaseExportFormat = "json"


class CaseWorkflowRequest(StrictBaseModel):
    """Stateless workflow mutation against an in-memory case artifact."""

    source_request: AnalyzeRequest
    analysis: AnalyzeResponse
    action: str = Field(..., max_length=64)
    actor_id: str = Field(..., max_length=128)
    actor_role: str = Field(default="analyst", max_length=32)
    assignee: str | None = Field(default=None, max_length=128)
    review_status: str | None = Field(default=None, max_length=32)
    disposition_code: str | None = Field(default=None, max_length=64)
    escalation_reason: str | None = Field(default=None, max_length=512)
    review_notes: str | None = Field(default=None, max_length=4000)


class CaseQueueSummaryRequest(StrictBaseModel):
    """Client-side queue snapshot for deterministic counters."""

    cases: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


class CaseExportArtifact(StrictBaseModel):
    """Сериализованный case artifact для скачивания или supervised review."""

    format: CaseExportFormat
    filename: str = Field(..., max_length=256)
    media_type: str = Field(..., max_length=128)
    content: str
    sha256: str = Field(..., max_length=128)


class ReplayHashCheck(StrictBaseModel):
    """Сверка хэшей файлов и вычисленных артефактов."""

    name: str = Field(..., max_length=128)
    expected_sha256: str | None = Field(default=None, max_length=128)
    actual_sha256: str | None = Field(default=None, max_length=128)
    matches: bool = False
    algorithm: str = Field(default="sha256", max_length=32)


class ReplayDiff(StrictBaseModel):
    """Детерминированный drift item."""

    section: str = Field(..., max_length=64)
    field_name: str = Field(..., max_length=128)
    expected: str | None = Field(default=None, max_length=512)
    actual: str | None = Field(default=None, max_length=512)


class ReplayResponse(StrictBaseModel):
    """Результат forensic replay без вызова LLM."""

    request_id: str | None = None
    replay_status: Literal["match", "drift", "invalid_bundle"]
    llm_called: bool = False
    evidence_revalidated: bool = True
    drift_detected: bool = False
    hash_checks: list[ReplayHashCheck] = Field(default_factory=list)
    drift_report: list[ReplayDiff] = Field(default_factory=list)
    replayed_profiler: ProfilerSummary | None = None
    replayed_anomaly: AnomalyBlock | None = None
    validator_summary: ValidatorSummary | None = None


from app.models.operations import (  # noqa: E402
    AuditEvent,
    CaseWorkflowState,
    ConnectorProvenance,
    ExportAccessLogEntry,
    GovernanceMetadata,
    LifecycleEvent,
    XlsxSheetPreview,
)

MetaBlock.model_rebuild()
XlsxIngestResponse.model_rebuild()
