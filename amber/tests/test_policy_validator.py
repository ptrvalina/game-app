from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.models.schemas import (
    AnalyzeRequest,
    AnalystLLMResult,
    AnomalyBlock,
    ClientProfile,
    ConfidenceValidation,
    EvidenceItem,
    ProfilerSummary,
    ReporterLLMResult,
    RouterLLMResult,
    TransactionRecord,
)
from app.xai.engine import XAIEngine
from app.xai.policy_validator import PolicyValidationError, PolicyValidator


def _anomaly(
    *,
    score: int = 72,
    confidence: int = 80,
    categories: list[str] | None = None,
    evidence: list[EvidenceItem] | None = None,
) -> AnomalyBlock:
    return AnomalyBlock(
        anomaly_score=score,
        confidence_score=confidence,
        severity="low",
        categories=categories or ["structuring"],
        reasons=["test"],
        evidence=evidence
        or [
            EvidenceItem(
                code="structuring_band",
                label="Structuring evidence",
                category="structuring",
                observed_value="3 operations",
                baseline_value="historical",
                threshold_value="<= 5%",
                contribution=18,
                tx_refs=["tx-1", "tx-2"],
            )
        ],
    )


def _profiler(*, window_transactions: int = 50, activity_days: int = 10) -> ProfilerSummary:
    return ProfilerSummary(
        window_transactions=window_transactions,
        activity_days=activity_days,
        avg_amount=100.0,
        median_amount=90.0,
        p95_amount=250.0,
        max_amount=300.0,
        avg_daily_count=2.0,
        rolling_7d_count=8.0,
        rolling_30d_count=20.0,
        top_counterparties=[],
        counterparty_concentration=0.2,
        burst_days=0,
        behavior_drift_score=0,
        timezone_basis="naive_as_utc",
        usual_hours_start=9,
        usual_hours_end=18,
        profile_notes=[],
    )


def test_fiat_crypto_leakage_rejected() -> None:
    validator = PolicyValidator()
    analyst = AnalystLLMResult(
        patterns_detected=["manual_review_required_fiat"],
        risk_summary="Операции указывают на связь с bitcoin wallet.",
        risk_explanation="Есть bitcoin wallet.",
        regulatory_hooks=[],
        recommendations=["Проверить клиента"],
    )

    with pytest.raises(PolicyValidationError) as exc:
        validator.validate_analyst(
            mode="fiat",
            jurisdiction="BY",
            anomaly=_anomaly(),
            analyst=analyst,
        )

    assert any(issue.code == "fiat_crypto_leakage" for issue in exc.value.issues)


def test_unsupported_claims_downgraded() -> None:
    validator = PolicyValidator()
    analyst = AnalystLLMResult(
        patterns_detected=["structuring"],
        risk_summary="Это доказывает нарушение.",
        risk_explanation="Паттерн гарантированно связано с нарушением и точно используется для обхода контроля.",
        regulatory_hooks=[],
        recommendations=["Факт однозначно является подозрительным"],
    )

    validated, issues, _ = validator.validate_analyst(
        mode="fiat",
        jurisdiction="BY",
        anomaly=_anomaly(),
        analyst=analyst,
    )

    assert "может указывать" in validated.risk_summary.lower()
    assert "потенциально связано" in validated.risk_explanation.lower()
    assert "может использоваться" in validated.risk_explanation.lower()
    assert "может являться" in validated.recommendations[0].lower()
    assert any(issue.code == "unsupported_claim_downgraded" for issue in issues)


def test_jurisdiction_mismatch_rejected() -> None:
    validator = PolicyValidator()
    reporter = ReporterLLMResult(
        sar_title="SAR",
        sar_body="Применимые нормы: MiCA и 5AMLD.",
        sar_disclaimer="Требует проверки",
    )

    with pytest.raises(PolicyValidationError) as exc:
        validator.validate_reporter(
            mode="fiat",
            jurisdiction="RU",
            anomaly=_anomaly(),
            reporter=reporter,
        )

    assert any(issue.code == "jurisdiction_mismatch" for issue in exc.value.issues)


def test_confidence_caps_applied() -> None:
    validator = PolicyValidator()
    anomaly = _anomaly(
        score=84,
        confidence=92,
        categories=["new_counterparty"],
        evidence=[
            EvidenceItem(
                code="new_counterparty",
                label="New counterparty",
                category="new_counterparty",
                observed_value="new cp",
                baseline_value="none",
                threshold_value=None,
                contribution=14,
                tx_refs=["tx-1"],
            )
        ],
    )

    updated, validation = validator.apply_confidence_caps(
        anomaly=anomaly,
        profiler=_profiler(window_transactions=8, activity_days=2),
        degraded_mode=True,
        emergency_mode=False,
    )

    assert updated.confidence_score <= 45
    assert updated.severity == "critical"
    assert validation.cap <= 45
    assert "low_history" in validation.reasons
    assert "degraded_mode" in validation.reasons
    assert "history_depth=" in validation.explanation


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0, "low"), (29, "low"), (30, "medium"), (59, "medium"), (60, "high"), (79, "high"), (80, "critical"), (100, "critical")],
)
def test_severity_mapping(score: int, expected: str) -> None:
    validator = PolicyValidator()
    assert validator.severity_for_score(score) == expected


def test_evidence_bound_claim_rejected() -> None:
    validator = PolicyValidator()
    anomaly = _anomaly(categories=["new_counterparty"], evidence=[])
    analyst = AnalystLLMResult(
        patterns_detected=["layering"],
        risk_summary="Наблюдается layering.",
        risk_explanation="Layering подтверждает обход контроля.",
        regulatory_hooks=[],
        recommendations=["Проверить клиента"],
    )

    with pytest.raises(PolicyValidationError) as exc:
        validator.validate_analyst(
            mode="cross",
            jurisdiction="EU",
            anomaly=anomaly,
            analyst=analyst,
        )

    assert any(issue.code in {"unsupported_typology_claim", "missing_evidence_threshold"} for issue in exc.value.issues)


def test_contradictory_reporter_narrative_rejected() -> None:
    validator = PolicyValidator()
    reporter = ReporterLLMResult(
        sar_title="Memo",
        executive_summary="Нет признаков подозрительности.",
        observed_behavior=["No suspicious activity observed."],
        anomaly_evidence=["velocity evidence"],
        regulatory_context=["Декрет №8"],
        recommended_actions=["Close case immediately."],
        sar_disclaimer="Requires review",
    )

    with pytest.raises(PolicyValidationError) as exc:
        validator.validate_reporter(
            mode="fiat",
            jurisdiction="BY",
            anomaly=_anomaly(score=78),
            reporter=reporter,
        )

    assert any(issue.code == "narrative_contradiction" for issue in exc.value.issues)


def test_duplicate_evidence_downgraded() -> None:
    validator = PolicyValidator()
    reporter = ReporterLLMResult(
        sar_title="Memo",
        executive_summary="Имеются признаки.",
        observed_behavior=["Observed behavior."],
        anomaly_evidence=["same evidence", "same evidence"],
        regulatory_context=["Декрет №8"],
        recommended_actions=["Requires analyst verification."],
        sar_disclaimer="Requires review",
    )

    validated, issues, _ = validator.validate_reporter(
        mode="fiat",
        jurisdiction="BY",
        anomaly=_anomaly(),
        reporter=reporter,
    )

    assert validated.human_review_required is True
    assert any(issue.code == "duplicate_evidence" for issue in issues)


def test_confidence_calibration_accounts_for_malformed_ratio() -> None:
    validator = PolicyValidator()
    anomaly = _anomaly(confidence=88)

    updated, validation = validator.apply_confidence_caps(
        anomaly=anomaly,
        profiler=_profiler(window_transactions=50, activity_days=12),
        degraded_mode=False,
        emergency_mode=False,
        data_completeness=72,
        malformed_input_ratio=0.25,
    )

    assert updated.confidence_score <= 60
    assert "malformed_input_ratio" in validation.reasons
    assert validation.data_completeness == 72


def test_unsupported_severity_claim_rejected() -> None:
    validator = PolicyValidator()
    reporter = ReporterLLMResult(
        sar_title="Critical case",
        executive_summary="This is a critical laundering case.",
        observed_behavior=["critical risk pattern"],
        anomaly_evidence=["single weak evidence"],
        regulatory_context=["Декрет №8"],
        recommended_actions=["Requires analyst verification."],
        sar_disclaimer="Requires review",
    )

    with pytest.raises(PolicyValidationError) as exc:
        validator.validate_reporter(
            mode="fiat",
            jurisdiction="BY",
            anomaly=_anomaly(score=25, evidence=[]),
            reporter=reporter,
        )

    assert any(issue.code == "unsupported_severity_claim" for issue in exc.value.issues)


def test_unsupported_regulator_reference_rejected() -> None:
    validator = PolicyValidator()
    reporter = ReporterLLMResult(
        sar_title="Memo",
        executive_summary="Internal review",
        observed_behavior=["Observed behavior."],
        anomaly_evidence=["Evidence"],
        regulatory_context=["Report to FinCEN immediately."],
        recommended_actions=["Requires analyst verification."],
        sar_disclaimer="Requires review",
    )

    with pytest.raises(PolicyValidationError) as exc:
        validator.validate_reporter(
            mode="fiat",
            jurisdiction="BY",
            anomaly=_anomaly(),
            reporter=reporter,
        )

    assert any(issue.code == "unsupported_regulator_reference" for issue in exc.value.issues)


def test_engine_policy_failure_returns_emergency_fallback() -> None:
    settings = get_settings()
    engine = XAIEngine(settings)
    responses = iter(
        [
            {
                "confirmed_mode": "fiat",
                "confirmed_jurisdiction": "BY",
                "applicable_norms": ["Декрет №8"],
                "routing_rationale": "Используем заявленную юрисдикцию.",
                "compliance_objectives": ["Проверить операции"],
            },
            {
                "patterns_detected": ["structuring"],
                "risk_summary": "Имеются признаки structuring.",
                "risk_explanation": "Требует проверки.",
                "regulatory_hooks": [],
                "recommendations": ["Проверить источник средств"],
                "new_pattern_hypothesis": None,
            },
            {
                "sar_title": "Bitcoin SAR",
                "sar_body": "Данный кейс подтверждает отмывание через bitcoin wallet.",
                "sar_disclaimer": "Требует проверки",
            },
        ]
    )

    async def fake_complete_json(**kwargs):
        return SimpleNamespace(
            data=next(responses),
            provider="openai",
            model="gpt-4o",
            retries=0,
            fallback_used=False,
            latency_ms=10,
            prompt_chars=100,
        )

    engine.llm.complete_json = fake_complete_json  # type: ignore[method-assign]

    req = AnalyzeRequest(
        mode="fiat",
        jurisdiction="BY",
        client_profile=ClientProfile(declared_monthly_income=1000),
        historical_transactions=[],
        focus_transactions=[
            TransactionRecord(id="tx-1", amount=2000, direction="in", asset_type="fiat"),
            TransactionRecord(id="tx-2", amount=2100, direction="in", asset_type="fiat"),
        ],
    )

    out = asyncio.run(engine.analyze(req, request_id="req-policy"))

    assert out.meta.emergency_mode is True
    assert out.meta.policy_validation_failed_reason is not None
    assert "fiat_crypto_leakage" in out.meta.policy_validation_failed_reason
    assert out.meta.validator_summary is not None
    assert out.meta.validator_summary.status == "failed"
    assert out.meta.validator_summary.failed_stages == ["reporter"]
    assert out.meta.stage_traces[-1].validator_status == "failed"
    assert out.meta.stage_traces[-1].remediation_action == "emergency_fallback"
    assert out.meta.stage_traces[-1].prompt_hash is not None
    assert out.meta.stage_traces[-1].payload_hash is not None
    assert "Emergency Mode" in out.reporter.sar_disclaimer


def test_engine_validator_metadata_and_safe_downgrade() -> None:
    settings = get_settings()
    engine = XAIEngine(settings)
    engine.anomaly.score = lambda **kwargs: _anomaly().model_dump(mode="json")  # type: ignore[method-assign]
    responses = iter(
        [
            {
                "confirmed_mode": "fiat",
                "confirmed_jurisdiction": "BY",
                "applicable_norms": ["Декрет №8"],
                "routing_rationale": "Используем заявленную юрисдикцию.",
                "compliance_objectives": ["Проверить операции"],
            },
            {
                "patterns_detected": ["structuring"],
                "risk_summary": "Это доказывает наличие structuring.",
                "risk_explanation": "Имеются признаки активности, которая точно используется для обхода лимитов.",
                "regulatory_hooks": ["Декрет №8"],
                "recommendations": ["Однозначно является основанием для дополнительной проверки."],
                "new_pattern_hypothesis": None,
            },
            {
                "sar_title": "Проект SAR",
                "sar_body": "Имеются признаки дробления операций. Требует проверки compliance-офицером.",
                "sar_disclaimer": "Необходимо дополнительное изучение.",
            },
        ]
    )

    async def fake_complete_json(**kwargs):
        return SimpleNamespace(
            data=next(responses),
            provider="openai",
            model="gpt-4o",
            retries=0,
            fallback_used=False,
            latency_ms=10,
            prompt_chars=100,
        )

    engine.llm.complete_json = fake_complete_json  # type: ignore[method-assign]

    req = AnalyzeRequest(
        mode="fiat",
        jurisdiction="BY",
        client_profile=ClientProfile(declared_monthly_income=1000),
        historical_transactions=[],
        focus_transactions=[
            TransactionRecord(id="tx-1", amount=2000, direction="in", asset_type="fiat"),
            TransactionRecord(id="tx-2", amount=2100, direction="in", asset_type="fiat"),
        ],
    )

    out = asyncio.run(engine.analyze(req, request_id="req-downgrade"))

    assert out.meta.emergency_mode is False
    assert out.meta.validator_status == "downgraded"
    assert out.meta.validator_summary is not None
    assert out.meta.validator_summary.status == "downgraded"
    assert out.meta.validator_summary.failed_stages == []
    assert out.meta.validator_summary.issues_count >= 1
    assert out.meta.validator_summary.remediation_action == "downgrade"
    assert out.meta.stage_traces[1].validator_status == "downgraded"
    assert out.meta.stage_traces[1].issues_count >= 1
    assert out.meta.stage_traces[1].validator_latency_ms is not None
    assert "unsupported_claim_downgraded" in out.meta.stage_traces[1].policy_failures
    assert all(trace.prompt_hash for trace in out.meta.stage_traces)
    assert all(trace.payload_hash for trace in out.meta.stage_traces)
    assert "может указывать" in out.analyst.risk_summary.lower()
    assert "может использоваться" in out.analyst.risk_explanation.lower()
