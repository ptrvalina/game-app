"""
XAIEngine: orchestration поверх deterministic XAI и LLM-нарратива.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from app.core.context import get_request_id
from app.core.config import Settings
from app.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnalystLLMResult,
    AnomalyBlock,
    MetaBlock,
    ProfilerSummary,
    ReporterLLMResult,
    RouterLLMResult,
    ScoringProvenance,
    StageTrace,
    ValidatorSummary,
)
from app.services.payload import cap_historical_for_profiler, clone_for_llm, shrink_payload_inplace
from app.xai.anomaly_detector import AnomalyDetector
from app.xai.llm_provider import LLMProvider
from app.xai.policy_validator import PolicyValidationError, PolicyValidator
from app.xai.profiler import Profiler
from app.xai.prompts.analyst import analyst_system_for_mode, build_analyst_user_payload
from app.xai.prompts.reporter import REPORTER_SYSTEM, build_reporter_user_payload
from app.xai.prompts.router import ROUTER_SYSTEM, build_router_user_payload

logger = logging.getLogger(__name__)

ROUTER_PROMPT_VERSION = "router-v2"
ANALYST_PROMPT_VERSION = "analyst-v2"
REPORTER_PROMPT_VERSION = "reporter-v2"


class XAIEngine:
    """Главное ядро Amber: один вызов analyze() — один полный ответ (без персистентности)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.llm = LLMProvider(settings)
        self.profiler = Profiler()
        self.anomaly = AnomalyDetector()
        self.policy = PolicyValidator()

    def analyze_deterministic(
        self,
        req: AnalyzeRequest,
        *,
        request_id: str | None = None,
        emergency_reason: str = "deterministic_only",
    ) -> AnalyzeResponse:
        hist = cap_historical_for_profiler(req, self._settings.max_profiler_transactions)
        profile_dict = self.profiler.build(hist)
        profiler_summary = ProfilerSummary(**profile_dict)
        declared_income = req.client_profile.declared_monthly_income if req.client_profile else None
        anomaly_dict = self.anomaly.score(
            mode=req.mode,
            profile=profile_dict,
            historical=hist,
            focus=req.focus_transactions,
            declared_monthly_income=declared_income,
        )
        anomaly = AnomalyBlock(**anomaly_dict)
        router = self._emergency_router(req)
        analyst = self._finalize_analyst(analyst=self._emergency_analyst(req, anomaly))
        reporter = self._finalize_reporter(
            req=req,
            router=router,
            analyst=analyst,
            anomaly=anomaly,
            reporter=self._emergency_reporter(req, router, analyst, anomaly),
        )
        traces = [
            self._emergency_trace(
                stage="router",
                prompt_version=ROUTER_PROMPT_VERSION,
                error_code=emergency_reason,
                payload_truncated=False,
                prompt_hash=self._stable_hash({"mode": req.mode, "jurisdiction": req.jurisdiction}),
                payload_hash=self._stable_hash({"focus": len(req.focus_transactions)}),
            ),
            self._emergency_trace(
                stage="analyst",
                prompt_version=ANALYST_PROMPT_VERSION,
                error_code=emergency_reason,
                payload_truncated=False,
                prompt_hash=self._stable_hash({"mode": req.mode, "stage": "analyst"}),
                payload_hash=self._stable_hash({"evidence": len(anomaly.evidence)}),
            ),
            self._emergency_trace(
                stage="reporter",
                prompt_version=REPORTER_PROMPT_VERSION,
                error_code=emergency_reason,
                payload_truncated=False,
                prompt_hash=self._stable_hash({"mode": req.mode, "stage": "reporter"}),
                payload_hash=self._stable_hash({"review": "required"}),
            ),
        ]
        anomaly, confidence_validation = self.policy.apply_confidence_caps(
            anomaly=anomaly,
            profiler=profiler_summary,
            degraded_mode=True,
            emergency_mode=True,
            data_completeness=self._data_completeness(req),
            malformed_input_ratio=self._malformed_input_ratio(req),
        )
        meta = MetaBlock(
            request_id=request_id,
            llm_primary=self._settings.llm_primary,
            llm_used="emergency",
            fallback_used=False,
            emergency_mode=True,
            degraded_mode=True,
            latency_ms_router=None,
            latency_ms_analyst=None,
            latency_ms_reporter=None,
            validator_status="not_run",
            issues_count=0,
            validator_latency_ms=None,
            policy_failures=[emergency_reason],
            remediation_action="emergency_fallback",
            confidence_validation=confidence_validation,
            scoring_provenance=self._scoring_provenance(anomaly),
            validator_summary=self._validator_summary(traces),
            human_review_required=True,
            operating_reason=emergency_reason,
            stage_traces=traces,
        )
        return AnalyzeResponse(
            alert_id=req.alert_id,
            client_id_external=req.client_id_external,
            mode=req.mode,
            jurisdiction=req.jurisdiction,
            router=router,
            profiler=profiler_summary,
            anomaly=anomaly,
            analyst=analyst,
            reporter=reporter,
            meta=meta,
        )

    async def analyze(
        self,
        req: AnalyzeRequest,
        *,
        request_id: str | None = None,
        deadline_monotonic: float | None = None,
    ) -> AnalyzeResponse:
        hist = cap_historical_for_profiler(req, self._settings.max_profiler_transactions)
        profile_dict = self.profiler.build(hist)
        profiler_summary = ProfilerSummary(**profile_dict)

        declared_income = req.client_profile.declared_monthly_income if req.client_profile else None
        anomaly_dict = self.anomaly.score(
            mode=req.mode,
            profile=profile_dict,
            historical=hist,
            focus=req.focus_transactions,
            declared_monthly_income=declared_income,
        )
        anomaly = AnomalyBlock(**anomaly_dict)
        req_llm = clone_for_llm(req, self._settings.max_llm_historical)

        traces: list[StageTrace] = []
        last_provider = "none"

        router_payload_raw = self._build_router_payload(req_llm, profile_dict)
        router_payload, router_truncated = shrink_payload_inplace(
            router_payload_raw,
            self._settings.max_llm_payload_chars,
        )
        router, router_trace = await self._router_stage(
            req,
            router_payload,
            router_truncated,
            deadline_monotonic=deadline_monotonic,
        )
        router, router_trace, router_policy_reason = self._validate_router_policy(req, router, router_trace)
        traces.append(router_trace)
        if router_trace.provider != "emergency":
            last_provider = router_trace.provider

        mode_eff = router.confirmed_mode
        jurisdiction_eff = router.confirmed_jurisdiction

        analyst_payload_raw = self._build_analyst_payload(req_llm, profile_dict, anomaly_dict, router)
        analyst_payload, analyst_truncated = shrink_payload_inplace(
            analyst_payload_raw,
            self._settings.max_llm_payload_chars,
        )
        analyst, analyst_trace = await self._analyst_stage(
            req,
            anomaly,
            mode_eff,
            analyst_payload,
            analyst_truncated,
            deadline_monotonic=deadline_monotonic,
        )
        analyst, analyst_trace, analyst_policy_reason = self._validate_analyst_policy(
            req, anomaly, router, analyst, analyst_trace
        )
        analyst = self._finalize_analyst(analyst=analyst)
        traces.append(analyst_trace)
        if analyst_trace.provider != "emergency":
            last_provider = analyst_trace.provider

        reporter_payload_raw = {
            "jurisdiction": jurisdiction_eff,
            "mode": mode_eff,
            "rule_pack": self._rule_pack(jurisdiction_eff),
            "router": router.model_dump(mode="json"),
            "analyst": analyst.model_dump(mode="json"),
            "anomaly": self._anomaly_for_llm(anomaly.model_dump(mode="json")),
            "transactions_excerpt": self._transactions_excerpt(req_llm, mode_eff),
        }
        reporter_payload, reporter_truncated = shrink_payload_inplace(
            reporter_payload_raw,
            self._settings.max_llm_payload_chars,
        )
        reporter, reporter_trace = await self._reporter_stage(
            req,
            router,
            analyst,
            anomaly,
            mode_eff,
            reporter_payload,
            reporter_truncated,
            deadline_monotonic=deadline_monotonic,
        )
        reporter, reporter_trace, reporter_policy_reason = self._validate_reporter_policy(
            req, anomaly, router, reporter, reporter_trace
        )
        reporter = self._finalize_reporter(req=req, router=router, analyst=analyst, anomaly=anomaly, reporter=reporter)
        traces.append(reporter_trace)
        if reporter_trace.provider != "emergency":
            last_provider = reporter_trace.provider

        fallback_used = any(trace.status == "fallback" for trace in traces)
        emergency = any(trace.status == "emergency" for trace in traces)
        degraded = any(trace.status != "live" for trace in traces)
        anomaly, confidence_validation = self.policy.apply_confidence_caps(
            anomaly=anomaly,
            profiler=profiler_summary,
            degraded_mode=degraded,
            emergency_mode=emergency,
            data_completeness=self._data_completeness(req),
            malformed_input_ratio=self._malformed_input_ratio(req),
        )
        policy_failures = list(
            dict.fromkeys(
                [
                    *router_trace.policy_failures,
                    *analyst_trace.policy_failures,
                    *reporter_trace.policy_failures,
                ]
            )
        )
        policy_failure_reason = next(
            (
                reason
                for reason in (
                    router_policy_reason,
                    analyst_policy_reason,
                    reporter_policy_reason,
                )
                if reason
            ),
            None,
        )

        meta = MetaBlock(
            request_id=request_id,
            llm_primary=self._settings.llm_primary,
            llm_used=last_provider if last_provider != "none" else "emergency",
            fallback_used=fallback_used,
            emergency_mode=emergency,
            degraded_mode=degraded,
            latency_ms_router=traces[0].latency_ms,
            latency_ms_analyst=traces[1].latency_ms,
            latency_ms_reporter=traces[2].latency_ms,
            validator_status=self._aggregate_validator_status(traces),
            issues_count=sum(trace.issues_count for trace in traces),
            validator_latency_ms=sum(trace.validator_latency_ms or 0 for trace in traces) or None,
            policy_failures=policy_failures,
            remediation_action=self._aggregate_remediation_action(traces),
            policy_validation_failed_reason=policy_failure_reason,
            confidence_validation=confidence_validation,
            scoring_provenance=self._scoring_provenance(anomaly),
            validator_summary=self._validator_summary(traces),
            human_review_required=True,
            operating_reason=policy_failure_reason or next((trace.error_code for trace in traces if trace.error_code), None),
            stage_traces=traces,
        )

        return AnalyzeResponse(
            alert_id=req.alert_id,
            client_id_external=req.client_id_external,
            mode=mode_eff,
            jurisdiction=jurisdiction_eff,
            router=router,
            profiler=profiler_summary,
            anomaly=anomaly,
            analyst=analyst,
            reporter=reporter,
            meta=meta,
        )

    async def _router_stage(
        self,
        req: AnalyzeRequest,
        payload: dict[str, Any],
        truncated: bool,
        *,
        deadline_monotonic: float | None,
    ) -> tuple[RouterLLMResult, StageTrace]:
        user_prompt = build_router_user_payload(payload)
        prompt_hash = self._stable_hash({"system": ROUTER_SYSTEM, "user": user_prompt})
        payload_hash = self._stable_hash(payload)
        try:
            result = await self.llm.complete_json(
                stage="router",
                system=ROUTER_SYSTEM,
                user=user_prompt,
                temperature=0.0,
                deadline_monotonic=deadline_monotonic,
            )
            router = RouterLLMResult.model_validate(result.data)
            if self._settings.strict_routing:
                router = router.model_copy(
                    update={
                        "confirmed_mode": req.mode,
                        "confirmed_jurisdiction": req.jurisdiction,
                    }
                )
            router = router.model_copy(update={"applicable_norms": self._rule_pack(req.jurisdiction)["norms"]})
            trace = self._live_trace(
                stage="router",
                prompt_version=ROUTER_PROMPT_VERSION,
                provider=result.provider,
                model=result.model,
                retries=result.retries,
                latency_ms=result.latency_ms,
                prompt_chars=result.prompt_chars,
                fallback_used=result.fallback_used,
                payload_truncated=truncated,
                prompt_hash=prompt_hash,
                payload_hash=payload_hash,
            )
            self._log_stage(trace)
            return router, trace
        except Exception as exc:  # noqa: BLE001
            logger.warning("router.stage failed alert_id=%s error=%s", req.alert_id, exc.__class__.__name__)
            trace = self._emergency_trace(
                stage="router",
                prompt_version=ROUTER_PROMPT_VERSION,
                error_code=self._stage_error_code(exc, deadline_monotonic),
                payload_truncated=truncated,
                prompt_hash=prompt_hash,
                payload_hash=payload_hash,
            )
            return self._emergency_router(req), trace

    async def _analyst_stage(
        self,
        req: AnalyzeRequest,
        anomaly: AnomalyBlock,
        mode_eff: str,
        payload: dict[str, Any],
        truncated: bool,
        *,
        deadline_monotonic: float | None,
    ) -> tuple[AnalystLLMResult, StageTrace]:
        system_prompt = analyst_system_for_mode(mode_eff)
        user_prompt = build_analyst_user_payload(payload)
        prompt_hash = self._stable_hash({"system": system_prompt, "user": user_prompt})
        payload_hash = self._stable_hash(payload)
        try:
            result = await self.llm.complete_json(
                stage="analyst",
                system=system_prompt,
                user=user_prompt,
                temperature=0.05,
                deadline_monotonic=deadline_monotonic,
            )
            analyst = AnalystLLMResult.model_validate(result.data)
            self._assert_mode_output_guard(mode_eff, [analyst.risk_summary, analyst.risk_explanation, analyst.new_pattern_hypothesis])
            trace = self._live_trace(
                stage="analyst",
                prompt_version=ANALYST_PROMPT_VERSION,
                provider=result.provider,
                model=result.model,
                retries=result.retries,
                latency_ms=result.latency_ms,
                prompt_chars=result.prompt_chars,
                fallback_used=result.fallback_used,
                payload_truncated=truncated,
                prompt_hash=prompt_hash,
                payload_hash=payload_hash,
            )
            self._log_stage(trace)
            return analyst, trace
        except Exception as exc:  # noqa: BLE001
            logger.warning("analyst.stage failed alert_id=%s error=%s", req.alert_id, exc.__class__.__name__)
            trace = self._emergency_trace(
                stage="analyst",
                prompt_version=ANALYST_PROMPT_VERSION,
                error_code=self._stage_error_code(exc, deadline_monotonic),
                payload_truncated=truncated,
                prompt_hash=prompt_hash,
                payload_hash=payload_hash,
            )
            return self._emergency_analyst(req, anomaly), trace

    async def _reporter_stage(
        self,
        req: AnalyzeRequest,
        router: RouterLLMResult,
        analyst: AnalystLLMResult,
        anomaly: AnomalyBlock,
        mode_eff: str,
        payload: dict[str, Any],
        truncated: bool,
        *,
        deadline_monotonic: float | None,
    ) -> tuple[ReporterLLMResult, StageTrace]:
        user_prompt = build_reporter_user_payload(payload)
        prompt_hash = self._stable_hash({"system": REPORTER_SYSTEM, "user": user_prompt})
        payload_hash = self._stable_hash(payload)
        try:
            result = await self.llm.complete_json(
                stage="reporter",
                system=REPORTER_SYSTEM,
                user=user_prompt,
                temperature=0.05,
                deadline_monotonic=deadline_monotonic,
            )
            reporter = ReporterLLMResult.model_validate(result.data)
            self._assert_mode_output_guard(mode_eff, [reporter.sar_title, reporter.sar_body, reporter.sar_disclaimer])
            trace = self._live_trace(
                stage="reporter",
                prompt_version=REPORTER_PROMPT_VERSION,
                provider=result.provider,
                model=result.model,
                retries=result.retries,
                latency_ms=result.latency_ms,
                prompt_chars=result.prompt_chars,
                fallback_used=result.fallback_used,
                payload_truncated=truncated,
                prompt_hash=prompt_hash,
                payload_hash=payload_hash,
            )
            self._log_stage(trace)
            return reporter, trace
        except Exception as exc:  # noqa: BLE001
            logger.warning("reporter.stage failed alert_id=%s error=%s", req.alert_id, exc.__class__.__name__)
            trace = self._emergency_trace(
                stage="reporter",
                prompt_version=REPORTER_PROMPT_VERSION,
                error_code=self._stage_error_code(exc, deadline_monotonic),
                payload_truncated=truncated,
                prompt_hash=prompt_hash,
                payload_hash=payload_hash,
            )
            return self._emergency_reporter(req, router, analyst, anomaly), trace

    def _build_router_payload(self, req: AnalyzeRequest, profile_dict: dict) -> dict[str, Any]:
        return {
            "requested_mode": req.mode,
            "requested_jurisdiction": req.jurisdiction,
            "strict_routing": self._settings.strict_routing,
            "rule_pack": self._rule_pack(req.jurisdiction),
            "client_profile": self._client_profile_trusted(req),
            "focus_stats": self._focus_stats(req),
            "asset_mix": self._asset_mix(req),
            "aml_system_flags": req.aml_system_flags or [],
            "profiler_summary": self._profile_for_llm(profile_dict),
            "profiler_text": self._profile_prompt_text(profile_dict),
            "focus_transactions_compact": self._compact_transactions(req.focus_transactions, mode=req.mode),
        }

    def _build_analyst_payload(
        self,
        req: AnalyzeRequest,
        profile_dict: dict,
        anomaly_dict: dict,
        router: RouterLLMResult,
    ) -> dict[str, Any]:
        return {
            "mode": router.confirmed_mode,
            "jurisdiction": router.confirmed_jurisdiction,
            "rule_pack": self._rule_pack(router.confirmed_jurisdiction),
            "deterministic_evidence": {
                "focus_stats": self._focus_stats(req),
                "asset_mix": self._asset_mix(req),
                "aml_system_flags": req.aml_system_flags or [],
                "profiler_summary": self._profile_for_llm(profile_dict),
                "profiler_text": self._profile_prompt_text(profile_dict),
                "anomaly": self._anomaly_for_llm(anomaly_dict),
            },
            "untrusted_evidence": {
                "declared_occupation": req.client_profile.declared_occupation if req.client_profile else None,
                "segment": req.client_profile.segment if req.client_profile else None,
                "client_notes": req.client_profile.notes if req.client_profile else None,
                "extra_context": req.extra_context,
                "focus_transactions": self._compact_transactions(req.focus_transactions, mode=router.confirmed_mode),
            },
        }

    def _client_profile_trusted(self, req: AnalyzeRequest) -> dict[str, Any]:
        profile = req.client_profile
        if not profile:
            return {}
        return {
            "declared_monthly_income": str(profile.declared_monthly_income) if profile.declared_monthly_income is not None else None,
        }

    def _focus_stats(self, req: AnalyzeRequest) -> dict[str, Any]:
        amounts = [float(t.amount) for t in req.focus_transactions]
        return {
            "count": len(amounts),
            "sum": sum(amounts),
            "min": min(amounts),
            "max": max(amounts),
        }

    def _asset_mix(self, req: AnalyzeRequest) -> dict[str, int]:
        mix: dict[str, int] = {"fiat": 0, "crypto": 0, "unknown": 0}
        for t in req.historical_transactions + req.focus_transactions:
            key = t.asset_type if t.asset_type in mix else "unknown"
            mix[key] = mix[key] + 1
        return mix

    def _transactions_excerpt(self, req: AnalyzeRequest, mode: str, limit: int = 24) -> list[dict[str, Any]]:
        combined = req.historical_transactions[-limit:] + req.focus_transactions
        return self._compact_transactions(combined[-limit:], mode=mode)

    def _compact_transactions(self, txs, *, mode: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in txs:
            row = {
                "id": t.id,
                "ts": t.ts.isoformat() if t.ts else None,
                "amount": str(t.amount),
                "currency": t.currency,
                "direction": t.direction,
                "counterparty": t.counterparty,
                "channel": t.channel,
                "narrative": t.narrative,
            }
            if mode != "fiat":
                row["asset_type"] = t.asset_type
            out.append(row)
        return out

    def _rule_pack(self, jurisdiction: str) -> dict[str, list[str]]:
        if jurisdiction == "RU":
            return {
                "norms": ["115-ФЗ", "Подзаконные акты Банка России по ПОД/ФТ"],
                "tone": ["официально-деловой", "без вымышленных точных ссылок на статьи"],
            }
        if jurisdiction == "BY":
            return {
                "norms": ["Декрет №8", "Указ №19", "Нормы Нацбанка РБ по внутреннему контролю"],
                "tone": ["официально-деловой", "без вымышленных редакций и статей"],
            }
        return {
            "norms": ["5AMLD", "MiCA"],
            "tone": ["официально-деловой", "уровень принципов ЕС, без локальных выдумок"],
        }

    def _assert_mode_output_guard(self, mode: str, texts: list[str | None]) -> None:
        if mode != "fiat" or not self._settings.strict_fiat_guard:
            return
        haystack = " ".join(x or "" for x in texts).lower()
        banned = [
            "крипт",
            "blockchain",
            "token",
            "stablecoin",
            "бирж",
            "crypto",
            "блокч",
            "токен",
        ]
        if any(token in haystack for token in banned):
            raise ValueError("fiat_mode_crypto_leak")

    def _live_trace(
        self,
        *,
        stage: str,
        prompt_version: str,
        provider: str,
        model: str,
        retries: int,
        latency_ms: int,
        prompt_chars: int,
        fallback_used: bool,
        payload_truncated: bool,
        prompt_hash: str,
        payload_hash: str,
    ) -> StageTrace:
        return StageTrace(
            stage=stage,
            status="fallback" if fallback_used else "live",
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            payload_hash=payload_hash,
            retries=retries,
            latency_ms=latency_ms,
            prompt_chars=prompt_chars,
            payload_truncated=payload_truncated,
        )

    def _emergency_trace(
        self,
        *,
        stage: str,
        prompt_version: str,
        error_code: str,
        payload_truncated: bool,
        prompt_hash: str,
        payload_hash: str,
    ) -> StageTrace:
        return StageTrace(
            stage=stage,
            status="emergency",
            provider="emergency",
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            payload_hash=payload_hash,
            error_code=error_code,
            payload_truncated=payload_truncated,
            remediation_action="emergency_fallback",
        )

    def _log_stage(self, trace: StageTrace) -> None:
        request_id = get_request_id()
        logger.info(
            "xai.stage request_id=%s stage=%s status=%s provider=%s retries=%s latency_ms=%s prompt_chars=%s "
            "validator_status=%s issues_count=%s remediation_action=%s error_code=%s",
            request_id,
            trace.stage,
            trace.status,
            trace.provider,
            trace.retries,
            trace.latency_ms,
            trace.prompt_chars,
            trace.validator_status,
            trace.issues_count,
            trace.remediation_action,
            trace.error_code,
        )

    def _emergency_router(self, req: AnalyzeRequest) -> RouterLLMResult:
        norms_map = {
            "RU": ["115-ФЗ (ПОД/ФТ)", "Подзаконные акты ЦБ РФ по внутреннему контролю"],
            "BY": ["Декрет №8", "Указ №19", "Нормы Нацбанка РБ о внутреннем контроле"],
            "EU": ["5AMLD", "MiCA"],
        }
        return RouterLLMResult(
            confirmed_mode=req.mode,
            confirmed_jurisdiction=req.jurisdiction,
            applicable_norms=norms_map.get(req.jurisdiction, []),
            routing_rationale="Emergency Mode: использованы заявленные режим и юрисдикция, потому что LLM-этап маршрутизации недоступен.",
            compliance_objectives=[
                "Проверить соответствие операций профилю клиента",
                "Оценить наличие подозрительных паттернов",
                "Подготовить обоснование для ручного SAR",
            ],
        )

    def _emergency_analyst(self, req: AnalyzeRequest, anomaly: AnomalyBlock) -> AnalystLLMResult:
        patterns: list[str] = []
        if anomaly.anomaly_score >= 60:
            patterns.append("behavioral_anomaly")
        if req.mode == "fiat":
            patterns.append("manual_review_required_fiat")
        elif req.mode == "crypto":
            patterns.append("manual_review_required_crypto")
        else:
            patterns.append("manual_review_required_cross")
        return AnalystLLMResult(
            patterns_detected=patterns,
            risk_summary="Emergency Mode: автоматический анализ паттернов ограничен.",
            risk_explanation=(
                "Детализированное объяснение недоступно без LLM. Используйте deterministic evidence из anomaly и profiler. "
                "Требуется проверка analyst/compliance officer."
            ),
            regulatory_hooks=[],
            recommendations=[
                "Повторите запрос позже",
                "Проведите ручную проверку по чек-листу внутренних процедур",
            ],
            new_pattern_hypothesis=anomaly.new_pattern_hypothesis,
            human_review_required=True,
        )

    def _emergency_reporter(
        self,
        req: AnalyzeRequest,
        router: RouterLLMResult,
        analyst: AnalystLLMResult,
        anomaly: AnomalyBlock,
    ) -> ReporterLLMResult:
        reporter = ReporterLLMResult(
            sar_title=f"Internal compliance memo (Emergency) — {req.alert_id or 'ALERT'}",
            executive_summary=analyst.risk_summary,
            observed_behavior=self._default_observed_behavior(req=req, analyst=analyst, anomaly=anomaly),
            anomaly_evidence=self._default_anomaly_evidence(anomaly),
            regulatory_context=router.applicable_norms,
            recommended_actions=analyst.recommendations,
            sar_disclaimer=(
                "Generated assistance only. Requires analyst verification and is not a final legal determination. "
                "Emergency Mode was used because the reporter stage was unavailable or rejected."
            ),
            human_review_required=True,
        )
        return self._finalize_reporter(req=req, router=router, analyst=analyst, anomaly=anomaly, reporter=reporter)

    def _profile_for_llm(self, profile_dict: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in profile_dict.items() if k not in {"top_counterparties"}}

    def _profile_prompt_text(self, profile_dict: dict[str, Any]) -> str:
        return Profiler.format_for_prompt(self._profile_for_llm(profile_dict))

    def _anomaly_for_llm(self, anomaly_dict: dict[str, Any]) -> dict[str, Any]:
        safe_evidence: list[dict[str, Any]] = []
        for item in anomaly_dict.get("evidence", []):
            safe_evidence.append(
                {
                    "code": item.get("code"),
                    "category": item.get("category"),
                    "contribution": item.get("contribution"),
                    "threshold_value": item.get("threshold_value"),
                    "observed_value": item.get("observed_value") if isinstance(item.get("observed_value"), (int, float)) else "<redacted-text>",
                    "baseline_value": item.get("baseline_value") if isinstance(item.get("baseline_value"), (int, float)) else "<redacted-text>",
                }
            )
        return {
            "anomaly_score": anomaly_dict.get("anomaly_score"),
            "severity": anomaly_dict.get("severity"),
            "confidence_score": anomaly_dict.get("confidence_score"),
            "categories": anomaly_dict.get("categories", []),
            "evidence": safe_evidence,
            "new_pattern_hypothesis": anomaly_dict.get("new_pattern_hypothesis"),
        }

    def _finalize_analyst(self, *, analyst: AnalystLLMResult) -> AnalystLLMResult:
        recommendations = list(analyst.recommendations)
        if not any("провер" in item.lower() or "review" in item.lower() for item in recommendations):
            recommendations.append("Требуется analyst verification перед любыми дальнейшими действиями.")
        return analyst.model_copy(update={"recommendations": recommendations[:12], "human_review_required": True})

    def _finalize_reporter(
        self,
        *,
        req: AnalyzeRequest,
        router: RouterLLMResult,
        analyst: AnalystLLMResult,
        anomaly: AnomalyBlock,
        reporter: ReporterLLMResult,
    ) -> ReporterLLMResult:
        executive_summary = (reporter.executive_summary or analyst.risk_summary or "Требуется ручная проверка кейса.").strip()
        observed_behavior = reporter.observed_behavior or self._default_observed_behavior(
            req=req,
            analyst=analyst,
            anomaly=anomaly,
        )
        anomaly_evidence = reporter.anomaly_evidence or self._default_anomaly_evidence(anomaly)
        regulatory_context = reporter.regulatory_context or router.applicable_norms
        recommended_actions = reporter.recommended_actions or list(analyst.recommendations)
        if not any("вериф" in item.lower() or "verification" in item.lower() or "провер" in item.lower() for item in recommended_actions):
            recommended_actions.append("Requires analyst verification before escalation, filing, or case closure.")
        disclaimer = (
            "Generated assistance only. Requires analyst verification and is not a final legal determination."
            if not reporter.sar_disclaimer
            else reporter.sar_disclaimer
        )
        normalized = reporter.model_copy(
            update={
                "sar_title": reporter.sar_title or f"Internal compliance memo — {req.alert_id or 'ALERT'}",
                "executive_summary": executive_summary[:2000],
                "observed_behavior": observed_behavior[:12],
                "anomaly_evidence": anomaly_evidence[:20],
                "regulatory_context": regulatory_context[:12],
                "recommended_actions": recommended_actions[:12],
                "sar_disclaimer": disclaimer[:512],
                "human_review_required": True,
            }
        )
        return normalized.model_copy(update={"sar_body": self._render_sar_body(normalized)})

    def _default_observed_behavior(
        self,
        *,
        req: AnalyzeRequest,
        analyst: AnalystLLMResult,
        anomaly: AnomalyBlock,
    ) -> list[str]:
        total_amount = sum(float(item.amount) for item in req.focus_transactions)
        currencies = sorted({(item.currency or "").upper() for item in req.focus_transactions if item.currency})
        base = [
            (
                f"Focus window contains {len(req.focus_transactions)} transactions totaling {total_amount:.2f}"
                + (f" {'/'.join(currencies)}." if currencies else ".")
            ),
            analyst.risk_explanation,
        ]
        for reason in anomaly.reasons[:4]:
            if reason not in base:
                base.append(reason)
        return [item for item in base if item][:12]

    def _default_anomaly_evidence(self, anomaly: AnomalyBlock) -> list[str]:
        lines: list[str] = []
        for item in anomaly.evidence[:8]:
            lines.append(
                f"{item.label} | observed={item.observed_value} | baseline={item.baseline_value} | "
                f"threshold={item.threshold_value} | contribution={item.contribution}"
            )
        if not lines:
            lines.append("Deterministic anomaly evidence is limited; supervised review remains mandatory.")
        return lines

    def _render_sar_body(self, reporter: ReporterLLMResult) -> str:
        sections = [
            "INTERNAL COMPLIANCE MEMO (DRAFT — NOT FOR EXTERNAL FILING)",
            "",
            "Executive Summary",
            reporter.executive_summary,
            "",
            "AI Narrative (Policy-Validated)",
            "The following sections are narrative support layered on top of deterministic evidence. They do not override scoring or policy controls.",
            "",
            "Observed Behavior",
            *[f"- {item}" for item in reporter.observed_behavior],
            "",
            "Deterministic Anomaly Evidence",
            *[f"- {item}" for item in reporter.anomaly_evidence],
            "",
            "Regulatory / Policy Context",
            *[f"- {item}" for item in reporter.regulatory_context],
            "",
            "Recommended Actions (Supervised)",
            *[f"- {item}" for item in reporter.recommended_actions],
            "",
            "Human Review",
            "HUMAN_REVIEW_REQUIRED: true — requires analyst verification before escalation or regulatory submission.",
            "Analyst Notes: not recorded in runtime response; attach via review workflow before export.",
            "",
            "Disclaimer",
            reporter.sar_disclaimer,
        ]
        return "\n".join(sections)

    def _scoring_provenance(self, anomaly: AnomalyBlock) -> ScoringProvenance:
        return ScoringProvenance(
            evidence_codes=[item.code for item in anomaly.evidence],
            categories=list(anomaly.categories),
            evidence_count=len(anomaly.evidence),
        )

    def _malformed_input_ratio(self, req: AnalyzeRequest) -> float:
        if not req.extra_context:
            return 0.0
        ingest = req.extra_context.get("amber_ingest")
        if not isinstance(ingest, dict):
            return 0.0
        try:
            value = float(ingest.get("rejected_ratio", 0.0))
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, value))

    def _data_completeness(self, req: AnalyzeRequest) -> int:
        txs = req.historical_transactions + req.focus_transactions
        if not txs:
            return 100
        score = 0
        max_score = len(txs) * 5
        for tx in txs:
            score += 1 if tx.ts else 0
            score += 1 if tx.currency else 0
            score += 1 if tx.direction != "unknown" else 0
            score += 1 if tx.asset_type != "unknown" else 0
            score += 1 if (tx.counterparty or tx.channel) else 0
        return int(round((score / max_score) * 100)) if max_score else 100

    def _stable_hash(self, value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _validate_router_policy(
        self,
        req: AnalyzeRequest,
        router: RouterLLMResult,
        trace: StageTrace,
    ) -> tuple[RouterLLMResult, StageTrace, str | None]:
        if trace.status == "emergency":
            return router, trace, None
        started = time.perf_counter()
        try:
            validated, issues, latency_ms = self.policy.validate_router(
                jurisdiction=req.jurisdiction,
                router=router,
            )
            updated_trace = trace.model_copy(
                update={
                    "validator_status": "downgraded" if issues else "passed",
                    "issues_count": len(issues),
                    "validator_latency_ms": latency_ms,
                    "policy_failures": [issue.code for issue in issues],
                    "remediation_action": "downgrade" if issues else "none",
                }
            )
            self._log_stage(updated_trace)
            return validated, updated_trace, None
        except PolicyValidationError as exc:
            updated_trace = trace.model_copy(
                update={
                    "status": "emergency",
                    "validator_status": "failed",
                    "issues_count": len(exc.issues),
                    "validator_latency_ms": int((time.perf_counter() - started) * 1000),
                    "policy_failures": [issue.code for issue in exc.issues],
                    "remediation_action": exc.remediation_action,
                    "error_code": "policy_validation_failed",
                }
            )
            self._log_stage(updated_trace)
            return self._emergency_router(req), updated_trace, self._policy_reason(exc)

    def _validate_analyst_policy(
        self,
        req: AnalyzeRequest,
        anomaly: AnomalyBlock,
        router: RouterLLMResult,
        analyst: AnalystLLMResult,
        trace: StageTrace,
    ) -> tuple[AnalystLLMResult, StageTrace, str | None]:
        if trace.status == "emergency":
            return analyst, trace, None
        started = time.perf_counter()
        try:
            validated, issues, latency_ms = self.policy.validate_analyst(
                mode=router.confirmed_mode,
                jurisdiction=router.confirmed_jurisdiction,
                anomaly=anomaly,
                analyst=analyst,
            )
            updated_trace = trace.model_copy(
                update={
                    "validator_status": "downgraded" if issues else "passed",
                    "issues_count": len(issues),
                    "validator_latency_ms": latency_ms,
                    "policy_failures": [issue.code for issue in issues],
                    "remediation_action": "downgrade" if issues else "none",
                }
            )
            self._log_stage(updated_trace)
            return validated, updated_trace, None
        except PolicyValidationError as exc:
            updated_trace = trace.model_copy(
                update={
                    "status": "emergency",
                    "validator_status": "failed",
                    "issues_count": len(exc.issues),
                    "validator_latency_ms": int((time.perf_counter() - started) * 1000),
                    "policy_failures": [issue.code for issue in exc.issues],
                    "remediation_action": exc.remediation_action,
                    "error_code": "policy_validation_failed",
                }
            )
            self._log_stage(updated_trace)
            return self._emergency_analyst(req, anomaly), updated_trace, self._policy_reason(exc)

    def _validate_reporter_policy(
        self,
        req: AnalyzeRequest,
        anomaly: AnomalyBlock,
        router: RouterLLMResult,
        reporter: ReporterLLMResult,
        trace: StageTrace,
    ) -> tuple[ReporterLLMResult, StageTrace, str | None]:
        if trace.status == "emergency":
            return reporter, trace, None
        started = time.perf_counter()
        try:
            validated, issues, latency_ms = self.policy.validate_reporter(
                mode=router.confirmed_mode,
                jurisdiction=router.confirmed_jurisdiction,
                anomaly=anomaly,
                reporter=reporter,
            )
            updated_trace = trace.model_copy(
                update={
                    "validator_status": "downgraded" if issues else "passed",
                    "issues_count": len(issues),
                    "validator_latency_ms": latency_ms,
                    "policy_failures": [issue.code for issue in issues],
                    "remediation_action": "downgrade" if issues else "none",
                }
            )
            self._log_stage(updated_trace)
            return validated, updated_trace, None
        except PolicyValidationError as exc:
            updated_trace = trace.model_copy(
                update={
                    "status": "emergency",
                    "validator_status": "failed",
                    "issues_count": len(exc.issues),
                    "validator_latency_ms": int((time.perf_counter() - started) * 1000),
                    "policy_failures": [issue.code for issue in exc.issues],
                    "remediation_action": exc.remediation_action,
                    "error_code": "policy_validation_failed",
                }
            )
            self._log_stage(updated_trace)
            return self._emergency_reporter(req, router, self._emergency_analyst(req, anomaly), anomaly), updated_trace, self._policy_reason(exc)

    def _aggregate_validator_status(self, traces: list[StageTrace]) -> str:
        statuses = {trace.validator_status for trace in traces}
        if "failed" in statuses:
            return "failed"
        if "downgraded" in statuses:
            return "downgraded"
        if "passed" in statuses:
            return "passed"
        return "not_run"

    def _aggregate_remediation_action(self, traces: list[StageTrace]) -> str:
        actions = {trace.remediation_action for trace in traces}
        if "emergency_fallback" in actions:
            return "emergency_fallback"
        if "downgrade" in actions:
            return "downgrade"
        return "none"

    def _validator_summary(self, traces: list[StageTrace]) -> ValidatorSummary:
        return ValidatorSummary(
            status=self._aggregate_validator_status(traces),
            issues_count=sum(trace.issues_count for trace in traces),
            failed_stages=[trace.stage for trace in traces if trace.validator_status == "failed"],
            remediation_action=self._aggregate_remediation_action(traces),
        )

    def _policy_reason(self, error: PolicyValidationError) -> str:
        return "; ".join(issue.code for issue in error.issues[:5])

    def _stage_error_code(self, exc: Exception, deadline_monotonic: float | None) -> str:
        message = str(exc).lower()
        if isinstance(exc, TimeoutError):
            return "request_deadline_exceeded"
        if "deadline" in message or "budget_exceeded" in message:
            return "request_deadline_exceeded"
        if deadline_monotonic is not None and time.perf_counter() >= deadline_monotonic:
            return "request_deadline_exceeded"
        return exc.__class__.__name__
