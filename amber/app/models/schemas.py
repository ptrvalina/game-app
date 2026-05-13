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
AnomalyCategory = Literal[
    "amount_spike",
    "velocity_spike",
    "burst_activity",
    "new_counterparty",
    "off_hours",
    "income_mismatch",
    "structuring",
    "smurfing",
    "circular_transfers",
    "cross_transition",
    "crypto_keyword",
]


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


class ReporterLLMResult(StrictBaseModel):
    """Проект SAR / сообщения регулятору."""

    sar_title: str = Field(..., max_length=256)
    sar_body: str = Field(..., max_length=20_000)
    sar_disclaimer: str = Field(
        default="Сгенерировано Amber AI. Требуется проверка и подпись compliance-офицером.",
        max_length=512,
    )


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
    confidence_score: int = Field(default=0, ge=0, le=100)
    categories: list[AnomalyCategory] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    new_pattern_hypothesis: str | None = Field(default=None, max_length=1200)


class StageTrace(StrictBaseModel):
    """Трассировка одного этапа pipeline."""

    stage: Literal["router", "analyst", "reporter"]
    status: StageState
    provider: str = Field(default="none", max_length=32)
    model: str | None = Field(default=None, max_length=128)
    prompt_version: str | None = Field(default=None, max_length=64)
    retries: int = Field(default=0, ge=0, le=10)
    latency_ms: int | None = Field(default=None, ge=0)
    prompt_chars: int | None = Field(default=None, ge=0)
    payload_truncated: bool = False
    error_code: str | None = Field(default=None, max_length=128)


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
    stage_traces: list[StageTrace] = Field(default_factory=list)


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
