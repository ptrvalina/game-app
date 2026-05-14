"""Deterministic forensic replay for exported Amber case bundles."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

from app.core.config import Settings
from app.models.schemas import (
    AnalyzeRequest,
    AnalystLLMResult,
    AnomalyBlock,
    ProfilerSummary,
    ReplayDiff,
    ReplayHashCheck,
    ReplayResponse,
    ReporterLLMResult,
    RouterLLMResult,
    ScoringProvenance,
    ValidatorSummary,
)
from app.services.payload import cap_historical_for_profiler
from app.services.case_export import CaseExportService
from app.xai.anomaly_detector import AnomalyDetector
from app.xai.policy_validator import PolicyValidationError, PolicyValidator
from app.xai.profiler import Profiler


class ReplayService:
    """Replays deterministic Amber pipeline without any LLM calls."""

    _REQUIRED_FILES = (
        "normalized_request.json",
        "deterministic_evidence.json",
        "anomaly.json",
        "traces.json",
        "reporter.json",
        "sar.txt",
        "audit_manifest.json",
    )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.profiler = Profiler()
        self.anomaly = AnomalyDetector()
        self.policy = PolicyValidator()
        self.exporter = CaseExportService(settings)

    def replay_bundle(self, bundle_bytes: bytes) -> ReplayResponse:
        try:
            files = self._read_bundle(bundle_bytes)
            manifest = json.loads(files["audit_manifest.json"])
            hash_checks = self._bundle_hash_checks(files, manifest.get("file_hashes", {}))
            hash_checks.append(
                ReplayHashCheck(
                    name="manifest_signature",
                    expected_sha256=(manifest.get("signature") or {}).get("signature"),
                    actual_sha256=self.exporter._sign_manifest_payload(manifest),  # noqa: SLF001
                    matches=self.exporter.verify_manifest_signature(manifest),
                    algorithm="hmac-sha256",
                )
            )

            source_request = AnalyzeRequest.model_validate(json.loads(files["normalized_request.json"]))
            historical = cap_historical_for_profiler(source_request, self._settings.max_profiler_transactions)
            profiler_dict = self.profiler.build(historical)
            profiler_summary = ProfilerSummary(**profiler_dict)
            anomaly_dict = self.anomaly.score(
                mode=source_request.mode,
                profile=profiler_dict,
                historical=historical,
                focus=source_request.focus_transactions,
                declared_monthly_income=source_request.client_profile.declared_monthly_income
                if source_request.client_profile
                else None,
            )
            replayed_anomaly = AnomalyBlock(**anomaly_dict)
            provider_metadata = manifest.get("provider_metadata") or {}
            replayed_anomaly, _confidence = self.policy.apply_confidence_caps(
                anomaly=replayed_anomaly,
                profiler=profiler_summary,
                degraded_mode=bool(provider_metadata.get("degraded_mode")),
                emergency_mode=bool(provider_metadata.get("emergency_mode")),
            )

            stored_anomaly = AnomalyBlock.model_validate(json.loads(files["anomaly.json"]))
            router = self._router_from_manifest(manifest, source_request)
            analyst = self._analyst_from_manifest(manifest)
            reporter = ReporterLLMResult.model_validate(json.loads(files["reporter.json"]))
            validator_summary = self._revalidate_outputs(
                source_request=source_request,
                replayed_anomaly=replayed_anomaly,
                router=router,
                analyst=analyst,
                reporter=reporter,
            )

            hash_checks.extend(
                self._deterministic_hash_checks(
                    profiler_summary=profiler_summary,
                    replayed_anomaly=replayed_anomaly,
                    manifest=manifest,
                )
            )
            drift_report = self._drift_report(
                stored_anomaly=stored_anomaly,
                replayed_anomaly=replayed_anomaly,
                stored_evidence=json.loads(files["deterministic_evidence.json"]),
                scoring_hash_expected=(manifest.get("deterministic_hashes") or {}).get("scoring_provenance"),
                hash_checks=hash_checks,
            )
            drift_detected = any(not item.matches for item in hash_checks) or bool(drift_report)
            return ReplayResponse(
                request_id=manifest.get("request_id"),
                replay_status="drift" if drift_detected else "match",
                llm_called=False,
                evidence_revalidated=True,
                drift_detected=drift_detected,
                hash_checks=hash_checks,
                drift_report=drift_report,
                replayed_profiler=profiler_summary,
                replayed_anomaly=replayed_anomaly,
                validator_summary=validator_summary,
            )
        except Exception as exc:  # noqa: BLE001
            return ReplayResponse(
                replay_status="invalid_bundle",
                llm_called=False,
                evidence_revalidated=False,
                drift_detected=True,
                drift_report=[
                    ReplayDiff(
                        section="bundle",
                        field_name="parse",
                        expected=None,
                        actual=str(exc)[:512],
                    )
                ],
            )

    def _read_bundle(self, bundle_bytes: bytes) -> dict[str, str]:
        with zipfile.ZipFile(io.BytesIO(bundle_bytes), "r") as archive:
            names = set(archive.namelist())
            missing = [name for name in self._REQUIRED_FILES if name not in names]
            if missing:
                raise ValueError(f"bundle missing required files: {', '.join(missing)}")
            return {
                name: archive.read(name).decode("utf-8")
                for name in self._REQUIRED_FILES
            }

    def _bundle_hash_checks(self, files: dict[str, str], expected_hashes: dict[str, str]) -> list[ReplayHashCheck]:
        out: list[ReplayHashCheck] = []
        for name in self._REQUIRED_FILES:
            actual = self._sha256(files[name])
            expected = expected_hashes.get(name)
            out.append(
                ReplayHashCheck(
                    name=name,
                    expected_sha256=expected,
                    actual_sha256=actual,
                    matches=(expected == actual) if expected else name == "audit_manifest.json",
                )
            )
        return out

    def _deterministic_hash_checks(
        self,
        *,
        profiler_summary: ProfilerSummary,
        replayed_anomaly: AnomalyBlock,
        manifest: dict,
    ) -> list[ReplayHashCheck]:
        deterministic = manifest.get("deterministic_hashes") or {}
        scoring = ScoringProvenance(
            evidence_codes=[item.code for item in replayed_anomaly.evidence],
            categories=list(replayed_anomaly.categories),
            evidence_count=len(replayed_anomaly.evidence),
        )
        actuals = {
            "profiler": self._sha256(self._json_text(profiler_summary.model_dump(mode="json"))),
            "anomaly": self._sha256(self._json_text(replayed_anomaly.model_dump(mode="json"))),
            "deterministic_evidence": self._sha256(
                self._json_text([item.model_dump(mode="json") for item in replayed_anomaly.evidence])
            ),
            "scoring_provenance": self._sha256(self._json_text(scoring.model_dump(mode="json"))),
        }
        return [
            ReplayHashCheck(
                name=f"deterministic:{name}",
                expected_sha256=deterministic.get(name),
                actual_sha256=value,
                matches=(deterministic.get(name) == value) if deterministic.get(name) else False,
            )
            for name, value in actuals.items()
        ]

    def _router_from_manifest(self, manifest: dict, source_request: AnalyzeRequest) -> RouterLLMResult:
        router_payload = manifest.get("router")
        if isinstance(router_payload, dict):
            return RouterLLMResult.model_validate(router_payload)
        norms = (((manifest.get("provider_metadata") or {}).get("applicable_norms")) or [])
        return RouterLLMResult(
            confirmed_mode=source_request.mode,
            confirmed_jurisdiction=source_request.jurisdiction,
            applicable_norms=norms,
            routing_rationale="replay_bundle",
            compliance_objectives=["replay_validation"],
        )

    def _analyst_from_manifest(self, manifest: dict) -> AnalystLLMResult:
        analyst_payload = manifest.get("analyst")
        if isinstance(analyst_payload, dict):
            return AnalystLLMResult.model_validate(analyst_payload)
        review = manifest.get("review") or {}
        note = review.get("review_notes") or "Replay-only analyst placeholder."
        return AnalystLLMResult(
            patterns_detected=["replay_validation"],
            risk_summary="Replay validation only.",
            risk_explanation=note[:4000],
            regulatory_hooks=[],
            recommendations=["Requires analyst verification."],
            new_pattern_hypothesis=None,
            human_review_required=True,
        )

    def _revalidate_outputs(
        self,
        *,
        source_request: AnalyzeRequest,
        replayed_anomaly: AnomalyBlock,
        router: RouterLLMResult,
        analyst: AnalystLLMResult,
        reporter: ReporterLLMResult,
    ) -> ValidatorSummary:
        statuses: list[str] = []
        issues_count = 0
        failed_stages: list[str] = []
        remediation_action = "none"
        try:
            _, issues, _ = self.policy.validate_router(jurisdiction=source_request.jurisdiction, router=router)
            if issues:
                statuses.append("downgraded")
                issues_count += len(issues)
                remediation_action = "downgrade"
            else:
                statuses.append("passed")
        except PolicyValidationError as exc:
            statuses.append("failed")
            issues_count += len(exc.issues)
            failed_stages.append("router")
            remediation_action = exc.remediation_action

        try:
            _, issues, _ = self.policy.validate_analyst(
                mode=source_request.mode,
                jurisdiction=source_request.jurisdiction,
                anomaly=replayed_anomaly,
                analyst=analyst,
            )
            if issues:
                statuses.append("downgraded")
                issues_count += len(issues)
                remediation_action = "downgrade"
            else:
                statuses.append("passed")
        except PolicyValidationError as exc:
            statuses.append("failed")
            issues_count += len(exc.issues)
            failed_stages.append("analyst")
            remediation_action = exc.remediation_action

        try:
            _, issues, _ = self.policy.validate_reporter(
                mode=source_request.mode,
                jurisdiction=source_request.jurisdiction,
                anomaly=replayed_anomaly,
                reporter=reporter,
            )
            if issues:
                statuses.append("downgraded")
                issues_count += len(issues)
                remediation_action = "downgrade"
            else:
                statuses.append("passed")
        except PolicyValidationError as exc:
            statuses.append("failed")
            issues_count += len(exc.issues)
            failed_stages.append("reporter")
            remediation_action = exc.remediation_action

        status = "failed" if "failed" in statuses else "downgraded" if "downgraded" in statuses else "passed"
        return ValidatorSummary(
            status=status,  # type: ignore[arg-type]
            issues_count=issues_count,
            failed_stages=failed_stages,
            remediation_action=remediation_action,
        )

    def _drift_report(
        self,
        *,
        stored_anomaly: AnomalyBlock,
        replayed_anomaly: AnomalyBlock,
        stored_evidence: list[dict],
        scoring_hash_expected: str | None,
        hash_checks: list[ReplayHashCheck],
    ) -> list[ReplayDiff]:
        out: list[ReplayDiff] = []
        if stored_anomaly.anomaly_score != replayed_anomaly.anomaly_score:
            out.append(
                ReplayDiff(
                    section="anomaly",
                    field_name="anomaly_score",
                    expected=str(stored_anomaly.anomaly_score),
                    actual=str(replayed_anomaly.anomaly_score),
                )
            )
        if stored_anomaly.severity != replayed_anomaly.severity:
            out.append(
                ReplayDiff(
                    section="anomaly",
                    field_name="severity",
                    expected=stored_anomaly.severity,
                    actual=replayed_anomaly.severity,
                )
            )
        stored_codes = [item.get("code") for item in stored_evidence]
        replayed_codes = [item.code for item in replayed_anomaly.evidence]
        if stored_codes != replayed_codes:
            out.append(
                ReplayDiff(
                    section="evidence",
                    field_name="codes",
                    expected=",".join(str(item) for item in stored_codes)[:512],
                    actual=",".join(replayed_codes)[:512],
                )
            )
        if scoring_hash_expected and any(not item.matches for item in hash_checks if item.name.startswith("deterministic:")):
            out.append(
                ReplayDiff(
                    section="scoring_provenance",
                    field_name="sha256",
                    expected=scoring_hash_expected,
                    actual="mismatch",
                )
            )
        if any(item.name == "manifest_signature" and not item.matches for item in hash_checks):
            out.append(
                ReplayDiff(
                    section="bundle",
                    field_name="manifest_signature",
                    expected="valid_signature",
                    actual="invalid_signature",
                )
            )
        return out

    def _json_text(self, value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)

    def _sha256(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
