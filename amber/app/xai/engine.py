"""
XAIEngine: orchestration поверх deterministic XAI и LLM-нарратива.
"""
from __future__ import annotations

import logging
from typing import Any

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
    StageTrace,
)
from app.services.payload import cap_historical_for_profiler, clone_for_llm, shrink_payload_inplace
from app.xai.anomaly_detector import AnomalyDetector
from app.xai.llm_provider import LLMProvider
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

    async def analyze(self, req: AnalyzeRequest, *, request_id: str | None = None) -> AnalyzeResponse:
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
        router, router_trace = await self._router_stage(req, router_payload, router_truncated)
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
        )
        traces.append(analyst_trace)
        if analyst_trace.provider != "emergency":
            last_provider = analyst_trace.provider

        reporter_payload_raw = {
            "jurisdiction": jurisdiction_eff,
            "mode": mode_eff,
            "rule_pack": self._rule_pack(jurisdiction_eff),
            "router": router.model_dump(mode="json"),
            "analyst": analyst.model_dump(mode="json"),
            "anomaly": anomaly.model_dump(mode="json"),
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
        )
        traces.append(reporter_trace)
        if reporter_trace.provider != "emergency":
            last_provider = reporter_trace.provider

        fallback_used = any(trace.status == "fallback" for trace in traces)
        emergency = any(trace.status == "emergency" for trace in traces)
        degraded = any(trace.status != "live" for trace in traces)

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
    ) -> tuple[RouterLLMResult, StageTrace]:
        try:
            result = await self.llm.complete_json(
                stage="router",
                system=ROUTER_SYSTEM,
                user=build_router_user_payload(payload),
                temperature=0.0,
            )
            router = RouterLLMResult.model_validate(result.data)
            if self._settings.strict_routing:
                router = router.model_copy(
                    update={
                        "confirmed_mode": req.mode,
                        "confirmed_jurisdiction": req.jurisdiction,
                    }
                )
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
            )
            self._log_stage(trace)
            return router, trace
        except Exception as exc:  # noqa: BLE001
            logger.warning("router.stage failed alert_id=%s error=%s", req.alert_id, exc.__class__.__name__)
            trace = self._emergency_trace(
                stage="router",
                prompt_version=ROUTER_PROMPT_VERSION,
                error_code=exc.__class__.__name__,
                payload_truncated=truncated,
            )
            return self._emergency_router(req), trace

    async def _analyst_stage(
        self,
        req: AnalyzeRequest,
        anomaly: AnomalyBlock,
        mode_eff: str,
        payload: dict[str, Any],
        truncated: bool,
    ) -> tuple[AnalystLLMResult, StageTrace]:
        try:
            result = await self.llm.complete_json(
                stage="analyst",
                system=analyst_system_for_mode(mode_eff),
                user=build_analyst_user_payload(payload),
                temperature=0.05,
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
            )
            self._log_stage(trace)
            return analyst, trace
        except Exception as exc:  # noqa: BLE001
            logger.warning("analyst.stage failed alert_id=%s error=%s", req.alert_id, exc.__class__.__name__)
            trace = self._emergency_trace(
                stage="analyst",
                prompt_version=ANALYST_PROMPT_VERSION,
                error_code=exc.__class__.__name__,
                payload_truncated=truncated,
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
    ) -> tuple[ReporterLLMResult, StageTrace]:
        try:
            result = await self.llm.complete_json(
                stage="reporter",
                system=REPORTER_SYSTEM,
                user=build_reporter_user_payload(payload),
                temperature=0.05,
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
            )
            self._log_stage(trace)
            return reporter, trace
        except Exception as exc:  # noqa: BLE001
            logger.warning("reporter.stage failed alert_id=%s error=%s", req.alert_id, exc.__class__.__name__)
            trace = self._emergency_trace(
                stage="reporter",
                prompt_version=REPORTER_PROMPT_VERSION,
                error_code=exc.__class__.__name__,
                payload_truncated=truncated,
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
            "profiler_summary": profile_dict,
            "profiler_text": Profiler.format_for_prompt(profile_dict),
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
                "profiler_summary": profile_dict,
                "profiler_text": Profiler.format_for_prompt(profile_dict),
                "anomaly": anomaly_dict,
            },
            "untrusted_evidence": {
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
            "declared_occupation": profile.declared_occupation,
            "segment": profile.segment,
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
    ) -> StageTrace:
        return StageTrace(
            stage=stage,
            status="fallback" if fallback_used else "live",
            provider=provider,
            model=model,
            prompt_version=prompt_version,
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
    ) -> StageTrace:
        return StageTrace(
            stage=stage,
            status="emergency",
            provider="emergency",
            prompt_version=prompt_version,
            error_code=error_code,
            payload_truncated=payload_truncated,
        )

    def _log_stage(self, trace: StageTrace) -> None:
        logger.info(
            "xai.stage stage=%s status=%s provider=%s retries=%s latency_ms=%s prompt_chars=%s",
            trace.stage,
            trace.status,
            trace.provider,
            trace.retries,
            trace.latency_ms,
            trace.prompt_chars,
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
            risk_explanation="Детализированное объяснение недоступно без LLM. Используйте deterministic evidence из anomaly и profiler.",
            regulatory_hooks=[],
            recommendations=[
                "Повторите запрос позже",
                "Проведите ручную проверку по чек-листу внутренних процедур",
            ],
            new_pattern_hypothesis=anomaly.new_pattern_hypothesis,
        )

    def _emergency_reporter(
        self,
        req: AnalyzeRequest,
        router: RouterLLMResult,
        analyst: AnalystLLMResult,
        anomaly: AnomalyBlock,
    ) -> ReporterLLMResult:
        body_lines = [
            f"Сообщение (проект, Emergency Mode) по алерту: {req.alert_id or 'N/A'}",
            "",
            "## Резюме",
            analyst.risk_summary,
            "",
            "## Аномалия (статистика Amber)",
            f"anomaly_score: {anomaly.anomaly_score}/100",
            *([f"- {r}" for r in anomaly.reasons]),
            "",
            "## Категории",
            *([f"- {c}" for c in anomaly.categories]),
            "",
            "## Применимые нормы",
            *([f"- {n}" for n in router.applicable_norms]),
            "",
            "## Рекомендации",
            *([f"- {x}" for x in analyst.recommendations]),
        ]
        return ReporterLLMResult(
            sar_title=f"Проект SAR (Emergency) — {req.alert_id or 'ALERT'}",
            sar_body="\n".join(body_lines),
            sar_disclaimer="Сгенерировано Amber AI в Emergency Mode. Требуется полная проверка compliance-офицером.",
        )
