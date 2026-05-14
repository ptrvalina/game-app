"""Deterministic policy enforcement for Amber LLM outputs."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from app.models.schemas import (
    AnalystLLMResult,
    AnomalyBlock,
    ConfidenceValidation,
    Jurisdiction,
    Mode,
    ProfilerSummary,
    ReporterLLMResult,
    RouterLLMResult,
    SeverityBand,
)


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    field_name: str
    blocking: bool = True
    remediation_action: str = "emergency_fallback"


class PolicyValidationError(RuntimeError):
    def __init__(self, stage: str, issues: list[ValidationIssue], remediation_action: str = "emergency_fallback") -> None:
        self.stage = stage
        self.issues = issues
        self.remediation_action = remediation_action
        message = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        super().__init__(message)


class PolicyValidator:
    """Deterministic authority layer above untrusted LLM outputs."""

    _FIAT_BANNED_TERMS = (
        "crypto",
        "cryptocurrency",
        "bitcoin",
        "usdt",
        "wallet",
        "blockchain",
        "exchange",
        "крипта",
        "крипт",
        "биткоин",
        "кошелёк",
        "кошелек",
        "кошел",
        "блокчейн",
        "блокч",
        "биржа",
        "бирж",
    )
    _BANNED_LEGAL_PHRASES = {
        "доказывает": "может указывать",
        "подтверждает отмывание": "имеются признаки, требующие проверки",
        "является преступной схемой": "может указывать на потенциально подозрительный паттерн",
        "гарантированно связано": "потенциально связано",
        "точно используется": "может использоваться",
        "однозначно является": "может являться",
    }
    _UNSUPPORTED_ALLEGATION_TOKENS = (
        "criminal activity",
        "money laundering confirmed",
        "confirmed laundering",
        "преступной деятельност",
        "совершил преступление",
        "отмывание доказано",
        "преступная активность подтверждена",
    )
    _SOFT_TONE_PHRASES = (
        "может указывать",
        "имеются признаки",
        "требует проверки",
        "потенциально",
        "необходимо дополнительное изучение",
    )
    _UNSUPPORTED_REGULATOR_TOKENS = ("fincen", "ofac", "sec", "cftc")
    _SEVERITY_TOKENS = {
        "critical": ("critical", "критич", "extreme"),
        "high": ("high risk", "высок", "serious"),
        "medium": ("medium risk", "moderate", "умерен"),
    }
    _UNSUPPORTED_ESCALATION_TOKENS = ("file immediately", "immediate filing", "report regulator now", "немедленно подать", "срочно сообщить регулятору")
    _JURISDICTION_ALLOWLIST: dict[Jurisdiction, tuple[str, ...]] = {
        "RU": ("115-ФЗ", "Банк России", "ЦБ РФ", "Подзаконные акты Банка России", "Подзаконные акты ЦБ РФ"),
        "BY": ("Декрет №8", "Указ №19", "Нацбанк РБ", "Нормы Нацбанка РБ"),
        "EU": ("5AMLD", "MiCA", "ЕС", "Евросоюз"),
    }
    _ALL_JURISDICTION_MARKERS: dict[Jurisdiction, tuple[str, ...]] = _JURISDICTION_ALLOWLIST
    _TYPOLOGY_RULES = {
        "structuring": {"categories": {"structuring"}, "require_threshold": True},
        "smurfing": {"categories": {"smurfing"}, "require_threshold": True},
        "circular transfers": {"categories": {"circular_transfers"}, "require_threshold": True},
        "circular transfer": {"categories": {"circular_transfers"}, "require_threshold": True},
        "round-tripping": {"categories": {"circular_transfers"}, "require_threshold": True},
        "layering": {"categories": set(), "require_threshold": True},
        "rapid transition": {"categories": {"cross_transition"}, "require_threshold": True},
        "rapid movement": {"categories": {"cross_transition"}, "require_threshold": True},
        "exchange hopping": {"categories": {"exchange_hopping"}, "require_threshold": True},
        "wallet fan-out": {"categories": {"wallet_fan_out"}, "require_threshold": True},
        "wallet fan out": {"categories": {"wallet_fan_out"}, "require_threshold": True},
        "micro-splitting": {"categories": {"micro_splitting"}, "require_threshold": True},
        "micro splitting": {"categories": {"micro_splitting"}, "require_threshold": True},
        "bridge-like": {"categories": {"bridge_behavior"}, "require_threshold": True},
        "cash to crypto": {"categories": {"cash_to_crypto_outflow"}, "require_threshold": True},
        "timing correlation": {"categories": {"timing_correlation"}, "require_threshold": True},
        "transition window": {"categories": {"transition_window"}, "require_threshold": True},
        "dormant": {"categories": {"dormant_activation"}, "require_threshold": True},
        "salary mismatch": {"categories": {"salary_mismatch"}, "require_threshold": True},
        "salary pass-through": {"categories": {"salary_pass_through"}, "require_threshold": True},
        "salary pass through": {"categories": {"salary_pass_through"}, "require_threshold": True},
        "mule account": {"categories": {"mule_account_indicators"}, "require_threshold": True},
        "rapid cash-out": {"categories": {"rapid_cash_out"}, "require_threshold": True},
        "rapid cash out": {"categories": {"rapid_cash_out"}, "require_threshold": True},
        "funnel account": {"categories": {"funnel_account_behavior"}, "require_threshold": True},
        "peel chain": {"categories": {"peel_chains"}, "require_threshold": True},
        "stablecoin burst": {"categories": {"stablecoin_bursts"}, "require_threshold": True},
        "fan-in": {"categories": {"fan_in"}, "require_threshold": True},
        "fan in": {"categories": {"fan_in"}, "require_threshold": True},
        "counterparty burst": {"categories": {"new_counterparty_burst"}, "require_threshold": True},
        "velocity spike": {"categories": {"velocity_spike"}, "require_threshold": True},
        "bridge sequencing": {"categories": {"bridge_sequencing"}, "require_threshold": True},
        "exchange boundary crossing": {"categories": {"repeated_exchange_boundary_crossing"}, "require_threshold": True},
        "transition cluster": {"categories": {"time_linked_transition_clusters"}, "require_threshold": True},
    }

    def validate_router(self, *, jurisdiction: Jurisdiction, router: RouterLLMResult) -> tuple[RouterLLMResult, list[ValidationIssue], int]:
        started = time.perf_counter()
        issues = self._check_jurisdiction_text(
            stage="router",
            jurisdiction=jurisdiction,
            fields={
                "routing_rationale": router.routing_rationale,
                "applicable_norms": "\n".join(router.applicable_norms),
            },
        )
        if issues:
            raise PolicyValidationError("router", issues)
        return router, [], self._elapsed_ms(started)

    def validate_analyst(
        self,
        *,
        mode: Mode,
        jurisdiction: Jurisdiction,
        anomaly: AnomalyBlock,
        analyst: AnalystLLMResult,
    ) -> tuple[AnalystLLMResult, list[ValidationIssue], int]:
        started = time.perf_counter()
        blocking_issues: list[ValidationIssue] = []
        non_blocking_issues: list[ValidationIssue] = []

        fields = {
            "risk_summary": analyst.risk_summary,
            "risk_explanation": analyst.risk_explanation,
            "recommendations": "\n".join(analyst.recommendations),
            "new_pattern_hypothesis": analyst.new_pattern_hypothesis or "",
        }
        if mode == "fiat":
            blocking_issues.extend(self._check_fiat_isolation("analyst", fields))

        blocking_issues.extend(self._check_jurisdiction_text("analyst", jurisdiction, {
            **fields,
            "regulatory_hooks": "\n".join(analyst.regulatory_hooks),
        }))

        filtered_patterns, removed_pattern_issues = self._filter_unsupported_patterns(analyst.patterns_detected, anomaly)
        non_blocking_issues.extend(removed_pattern_issues)
        blocking_issues.extend(self._check_narrative_claims("analyst", fields, anomaly))
        blocking_issues.extend(self._check_unsupported_allegations("analyst", fields))
        blocking_issues.extend(self._check_narrative_contradictions("analyst", fields, anomaly))
        blocking_issues.extend(self._check_unsupported_severity("analyst", fields, anomaly))

        updated = analyst.model_copy(
            update={
                "patterns_detected": filtered_patterns,
                "risk_summary": self._downgrade_language(analyst.risk_summary),
                "risk_explanation": self._downgrade_language(analyst.risk_explanation),
                "recommendations": self._compress_list([self._downgrade_language(item) for item in analyst.recommendations]),
                "new_pattern_hypothesis": self._downgrade_language(analyst.new_pattern_hypothesis) if analyst.new_pattern_hypothesis else None,
                "regulatory_hooks": self._validate_regulatory_hooks(jurisdiction, analyst.regulatory_hooks),
                "human_review_required": True,
            }
        )
        non_blocking_issues.extend(self._diff_language_issues(analyst, updated, stage="analyst"))

        if blocking_issues:
            raise PolicyValidationError("analyst", blocking_issues)
        return updated, non_blocking_issues, self._elapsed_ms(started)

    def validate_reporter(
        self,
        *,
        mode: Mode,
        jurisdiction: Jurisdiction,
        anomaly: AnomalyBlock,
        reporter: ReporterLLMResult,
    ) -> tuple[ReporterLLMResult, list[ValidationIssue], int]:
        started = time.perf_counter()
        blocking_issues: list[ValidationIssue] = []
        non_blocking_issues: list[ValidationIssue] = []

        fields = {
            "sar_title": reporter.sar_title,
            "executive_summary": reporter.executive_summary,
            "observed_behavior": "\n".join(reporter.observed_behavior),
            "anomaly_evidence": "\n".join(reporter.anomaly_evidence),
            "regulatory_context": "\n".join(reporter.regulatory_context),
            "recommended_actions": "\n".join(reporter.recommended_actions),
            "sar_body": reporter.sar_body,
            "sar_disclaimer": reporter.sar_disclaimer,
        }
        if mode == "fiat":
            blocking_issues.extend(self._check_fiat_isolation("reporter", fields))
        blocking_issues.extend(self._check_jurisdiction_text("reporter", jurisdiction, fields))
        blocking_issues.extend(self._check_narrative_claims("reporter", fields, anomaly))
        blocking_issues.extend(self._check_unsupported_allegations("reporter", fields))
        blocking_issues.extend(self._check_narrative_contradictions("reporter", fields, anomaly))
        blocking_issues.extend(self._check_unsupported_severity("reporter", fields, anomaly))
        blocking_issues.extend(self._check_unsupported_escalation("reporter", fields))
        non_blocking_issues.extend(self._check_reporter_sections(reporter))
        non_blocking_issues.extend(self._check_duplicate_evidence_list("reporter", reporter.anomaly_evidence))

        updated = reporter.model_copy(
            update={
                "sar_title": self._downgrade_language(reporter.sar_title),
                "executive_summary": self._downgrade_language(reporter.executive_summary) or "",
                "observed_behavior": self._compress_list([self._downgrade_language(item) or "" for item in reporter.observed_behavior]),
                "anomaly_evidence": self._compress_list([self._downgrade_language(item) or "" for item in reporter.anomaly_evidence]),
                "regulatory_context": self._compress_list([self._downgrade_language(item) or "" for item in reporter.regulatory_context]),
                "recommended_actions": self._compress_list([self._downgrade_language(item) or "" for item in reporter.recommended_actions]),
                "sar_body": self._downgrade_language(reporter.sar_body),
                "sar_disclaimer": self._downgrade_language(reporter.sar_disclaimer),
                "human_review_required": True,
            }
        )
        non_blocking_issues.extend(self._diff_language_issues(reporter, updated, stage="reporter"))

        if blocking_issues:
            raise PolicyValidationError("reporter", blocking_issues)
        return updated, non_blocking_issues, self._elapsed_ms(started)

    def apply_confidence_caps(
        self,
        *,
        anomaly: AnomalyBlock,
        profiler: ProfilerSummary,
        degraded_mode: bool,
        emergency_mode: bool,
        data_completeness: int = 100,
        malformed_input_ratio: float = 0.0,
    ) -> tuple[AnomalyBlock, ConfidenceValidation]:
        reasons: list[str] = []
        cap = 100
        evidence_count = len(anomaly.evidence)
        threshold_backed = sum(
            1 for item in anomaly.evidence if item.threshold_value not in (None, "", "<redacted-text>")
        )
        anomaly_agreement = int(round((threshold_backed / evidence_count) * 100)) if evidence_count else 0
        if evidence_count < 2:
            cap = min(cap, 65)
            reasons.append("evidence_count_lt_2")
        if profiler.window_transactions < 20 or profiler.activity_days < 5:
            cap = min(cap, 55)
            reasons.append("low_history")
        if evidence_count and anomaly_agreement < 50:
            cap = min(cap, 70)
            reasons.append("low_anomaly_agreement")
        if data_completeness < 80:
            cap = min(cap, 70)
            reasons.append("low_data_completeness")
        if data_completeness < 60:
            cap = min(cap, 55)
        if malformed_input_ratio >= 0.05:
            cap = min(cap, 75)
            reasons.append("malformed_input_ratio")
        if malformed_input_ratio >= 0.20:
            cap = min(cap, 60)
        if emergency_mode:
            cap = min(cap, 25)
            reasons.append("emergency_mode")
        elif degraded_mode:
            cap = min(cap, 45)
            reasons.append("degraded_mode")

        effective = min(anomaly.confidence_score, cap)
        updated = anomaly.model_copy(
            update={
                "confidence_score": effective,
                "severity": self.severity_for_score(anomaly.anomaly_score),
            }
        )
        validation = ConfidenceValidation(
            original_score=anomaly.confidence_score,
            effective_score=effective,
            cap=cap,
            reasons=reasons,
            history_depth=profiler.window_transactions,
            evidence_count=evidence_count,
            anomaly_agreement=anomaly_agreement,
            data_completeness=data_completeness,
            malformed_input_ratio=round(malformed_input_ratio, 6),
            explanation=(
                f"history_depth={profiler.window_transactions}; evidence_count={evidence_count}; "
                f"anomaly_agreement={anomaly_agreement}; data_completeness={data_completeness}; "
                f"malformed_input_ratio={malformed_input_ratio:.3f}; degraded_mode={degraded_mode}; "
                f"emergency_mode={emergency_mode}"
            )[:1200],
        )
        return updated, validation

    def severity_for_score(self, score: int) -> SeverityBand:
        if score <= 29:
            return "low"
        if score <= 59:
            return "medium"
        if score <= 79:
            return "high"
        return "critical"

    def _check_fiat_isolation(self, stage: str, fields: dict[str, str]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field_name, text in fields.items():
            lowered = (text or "").lower()
            for token in self._FIAT_BANNED_TERMS:
                if token in lowered:
                    issues.append(
                        ValidationIssue(
                            code="fiat_crypto_leakage",
                            message=f"Fiat-mode text leaks forbidden term: {token}",
                            field_name=f"{stage}.{field_name}",
                        )
                    )
                    break
        return issues

    def _check_jurisdiction_text(
        self,
        stage: str,
        jurisdiction: Jurisdiction,
        fields: dict[str, str],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field_name, text in fields.items():
            lowered = (text or "").lower()
            for other_jurisdiction, markers in self._ALL_JURISDICTION_MARKERS.items():
                if other_jurisdiction == jurisdiction:
                    continue
                if any(marker.lower() in lowered for marker in markers):
                    issues.append(
                        ValidationIssue(
                            code="jurisdiction_mismatch",
                            message=f"Mixed jurisdiction marker detected for {other_jurisdiction}",
                            field_name=f"{stage}.{field_name}",
                        )
                    )
            if self._looks_like_legal_reference(lowered) and not any(
                marker.lower() in lowered for marker in self._JURISDICTION_ALLOWLIST[jurisdiction]
            ):
                issues.append(
                    ValidationIssue(
                        code="unsupported_legal_reference",
                        message="Legal reference is outside deterministic jurisdiction allowlist",
                        field_name=f"{stage}.{field_name}",
                    )
                )
            if any(token in lowered for token in self._UNSUPPORTED_REGULATOR_TOKENS):
                issues.append(
                    ValidationIssue(
                        code="unsupported_regulator_reference",
                        message="Unsupported regulator reference for selected jurisdiction",
                        field_name=f"{stage}.{field_name}",
                    )
                )
        return issues

    def _check_narrative_claims(
        self,
        stage: str,
        fields: dict[str, str],
        anomaly: AnomalyBlock,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        supported_categories = set(anomaly.categories)
        evidence_by_category = {
            item.category: item for item in anomaly.evidence if item.threshold_value not in (None, "", "<redacted-text>")
        }
        for field_name, text in fields.items():
            lowered = (text or "").lower()
            for claim, rules in self._TYPOLOGY_RULES.items():
                if claim not in lowered:
                    continue
                expected_categories = rules["categories"]
                if not expected_categories or not (supported_categories & expected_categories):
                    issues.append(
                        ValidationIssue(
                            code="unsupported_typology_claim",
                            message=f"Claim '{claim}' is not backed by deterministic anomaly categories",
                            field_name=f"{stage}.{field_name}",
                        )
                    )
                    continue
                if rules["require_threshold"] and not any(cat in evidence_by_category for cat in expected_categories):
                    issues.append(
                        ValidationIssue(
                            code="missing_evidence_threshold",
                            message=f"Claim '{claim}' lacks threshold-backed evidence",
                            field_name=f"{stage}.{field_name}",
                        )
                    )
        return issues

    def _check_unsupported_allegations(self, stage: str, fields: dict[str, str]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field_name, text in fields.items():
            lowered = (text or "").lower()
            for token in self._UNSUPPORTED_ALLEGATION_TOKENS:
                if token in lowered:
                    issues.append(
                        ValidationIssue(
                            code="unsupported_allegation",
                            message=f"Unsupported allegation phrase detected: {token}",
                            field_name=f"{stage}.{field_name}",
                        )
                    )
                    break
        return issues

    def _check_narrative_contradictions(
        self,
        stage: str,
        fields: dict[str, str],
        anomaly: AnomalyBlock,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        high_risk_tokens = ("нет признаков", "no suspicious", "no anomaly", "risk absent", "case can be closed")
        severe_tokens = ("critical", "критич", "severe")
        for field_name, text in fields.items():
            lowered = (text or "").lower()
            if anomaly.anomaly_score >= 60 and any(token in lowered for token in high_risk_tokens):
                issues.append(
                    ValidationIssue(
                        code="narrative_contradiction",
                        message="Narrative downplays materially elevated deterministic anomaly score",
                        field_name=f"{stage}.{field_name}",
                    )
                )
            if anomaly.anomaly_score <= 29 and any(token in lowered for token in severe_tokens):
                issues.append(
                    ValidationIssue(
                        code="narrative_contradiction",
                        message="Narrative overstates severity versus deterministic anomaly score",
                        field_name=f"{stage}.{field_name}",
                    )
                )
        return issues

    def _check_unsupported_severity(
        self,
        stage: str,
        fields: dict[str, str],
        anomaly: AnomalyBlock,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        expected = self.severity_for_score(anomaly.anomaly_score)
        evidence_count = len(anomaly.evidence)
        for field_name, text in fields.items():
            lowered = (text or "").lower()
            if expected in {"low", "medium"} and any(token in lowered for token in self._SEVERITY_TOKENS["critical"]):
                issues.append(
                    ValidationIssue(
                        code="unsupported_severity_claim",
                        message="Narrative severity exceeds deterministic score band",
                        field_name=f"{stage}.{field_name}",
                    )
                )
            if expected == "low" and any(token in lowered for token in self._SEVERITY_TOKENS["high"]):
                issues.append(
                    ValidationIssue(
                        code="unsupported_severity_claim",
                        message="Narrative overstates severity versus deterministic evidence",
                        field_name=f"{stage}.{field_name}",
                    )
                )
            if evidence_count < 2 and any(token in lowered for token in self._SEVERITY_TOKENS["critical"] + self._SEVERITY_TOKENS["high"]):
                issues.append(
                    ValidationIssue(
                        code="unsupported_severity_claim",
                        message="Insufficient deterministic evidence for elevated severity wording",
                        field_name=f"{stage}.{field_name}",
                    )
                )
        return issues

    def _check_unsupported_escalation(self, stage: str, fields: dict[str, str]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field_name, text in fields.items():
            lowered = (text or "").lower()
            if any(token in lowered for token in self._UNSUPPORTED_ESCALATION_TOKENS):
                issues.append(
                    ValidationIssue(
                        code="unsupported_escalation",
                        message="Narrative requests unsupported immediate escalation or filing",
                        field_name=f"{stage}.{field_name}",
                    )
                )
        return issues

    def _check_reporter_sections(self, reporter: ReporterLLMResult) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        required = {
            "executive_summary": reporter.executive_summary,
            "observed_behavior": "\n".join(reporter.observed_behavior),
            "anomaly_evidence": "\n".join(reporter.anomaly_evidence),
            "regulatory_context": "\n".join(reporter.regulatory_context),
            "recommended_actions": "\n".join(reporter.recommended_actions),
            "sar_disclaimer": reporter.sar_disclaimer,
        }
        for field_name, value in required.items():
            if not (value or "").strip():
                issues.append(
                    ValidationIssue(
                        code="sar_section_missing",
                        message=f"Reporter section is empty: {field_name}",
                        field_name=f"reporter.{field_name}",
                        blocking=False,
                        remediation_action="downgrade",
                    )
                )
        return issues

    def _check_duplicate_evidence_list(self, stage: str, items: list[str]) -> list[ValidationIssue]:
        seen: set[str] = set()
        issues: list[ValidationIssue] = []
        for item in items:
            normalized = re.sub(r"\s+", " ", (item or "").strip().lower())
            if not normalized:
                continue
            if normalized in seen:
                issues.append(
                    ValidationIssue(
                        code="duplicate_evidence",
                        message="Duplicate evidence line detected",
                        field_name=f"{stage}.anomaly_evidence",
                        blocking=False,
                        remediation_action="downgrade",
                    )
                )
                break
            seen.add(normalized)
        return issues

    def _filter_unsupported_patterns(
        self,
        patterns: list[str],
        anomaly: AnomalyBlock,
    ) -> tuple[list[str], list[ValidationIssue]]:
        filtered: list[str] = []
        issues: list[ValidationIssue] = []
        supported_categories = set(anomaly.categories)
        evidence_categories = {
            item.category for item in anomaly.evidence if item.threshold_value not in (None, "", "<redacted-text>")
        }
        for pattern in patterns:
            normalized = pattern.lower()
            claim_rule = self._claim_rule_for_pattern(normalized)
            if not claim_rule:
                filtered.append(pattern)
                continue
            expected_categories = claim_rule["categories"]
            if expected_categories and (supported_categories & expected_categories) and (not claim_rule["require_threshold"] or expected_categories & evidence_categories):
                filtered.append(pattern)
                continue
            issues.append(
                ValidationIssue(
                    code="unsupported_pattern_removed",
                    message=f"Removed unsupported pattern '{pattern}'",
                    field_name="analyst.patterns_detected",
                    blocking=False,
                    remediation_action="downgrade",
                )
            )
        return filtered, issues

    def _claim_rule_for_pattern(self, pattern: str):
        for claim, rules in self._TYPOLOGY_RULES.items():
            if claim in pattern:
                return rules
        return None

    def _validate_regulatory_hooks(self, jurisdiction: Jurisdiction, hooks: list[str]) -> list[str]:
        validated: list[str] = []
        allow = self._JURISDICTION_ALLOWLIST[jurisdiction]
        for hook in hooks:
            lowered = hook.lower()
            if self._looks_like_legal_reference(lowered) and not any(marker.lower() in lowered for marker in allow):
                raise PolicyValidationError(
                    "analyst",
                    [
                        ValidationIssue(
                            code="unsupported_regulatory_hook",
                            message=f"Unsupported hook for jurisdiction {jurisdiction}: {hook}",
                            field_name="analyst.regulatory_hooks",
                        )
                    ],
                )
            validated.append(self._downgrade_language(hook))
        return validated

    def _downgrade_language(self, text: str | None) -> str | None:
        if text is None:
            return None
        updated = text
        for source, target in self._BANNED_LEGAL_PHRASES.items():
            updated = re.sub(re.escape(source), target, updated, flags=re.IGNORECASE)
        return updated

    def _compress_list(self, items: list[str | None]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            normalized = re.sub(r"\s+", " ", (item or "").strip())
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(normalized)
        return out

    def _diff_language_issues(self, before, after, *, stage: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        before_dump = before.model_dump(mode="json")
        after_dump = after.model_dump(mode="json")
        for key, before_value in before_dump.items():
            after_value = after_dump.get(key)
            if before_value != after_value:
                issues.append(
                    ValidationIssue(
                        code="unsupported_claim_downgraded",
                        message=f"Deterministically softened unsupported phrasing in {key}",
                        field_name=f"{stage}.{key}",
                        blocking=False,
                        remediation_action="downgrade",
                    )
                )
        return issues

    def _looks_like_legal_reference(self, lowered: str) -> bool:
        return any(token in lowered for token in ("115-фз", "5amld", "mica", "декрет", "указ", "ст.", "article"))

    def _elapsed_ms(self, started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
