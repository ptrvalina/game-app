"""Версионированное API."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile

from app.deps import get_engine
from app.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    CaseExportArtifact,
    CaseExportRequest,
    CsvIngestResponse,
    Jurisdiction,
    Mode,
    ReplayResponse,
    SarExportFormat,
)
from app.services.case_export import CaseExportService
from app.services.csv_ingest import CsvIngestService
from app.services.replay import ReplayService
from app.xai.engine import XAIEngine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Анализ"])
csv_ingest_service = CsvIngestService()


@router.get("/health", tags=["Сервис"], summary="Liveness API v1")
async def health_v1() -> dict:
    return {"status": "ok", "service": "amber", "api": "v1"}


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    summary="Полный XAI-анализ алерта",
    description="Router → Analyst → Reporter, плюс локальные Profiler и AnomalyDetector. Данные не сохраняются.",
)
async def analyze_v1(
    req: AnalyzeRequest,
    request: Request,
    response: Response,
    engine: XAIEngine = Depends(get_engine),
) -> AnalyzeResponse:
    rid = getattr(request.state, "request_id", None)
    telemetry = getattr(request.app.state, "telemetry", None)
    runtime_guard = getattr(request.app.state, "runtime_guard", None)
    logger.info(
        "analyze.start request_id=%s mode=%s jurisdiction=%s",
        rid,
        req.mode,
        req.jurisdiction,
    )
    started = time.perf_counter()
    if runtime_guard and not await runtime_guard.try_acquire():
        if telemetry:
            telemetry.incr("overload_rejections_total")
        response.headers["Retry-After"] = "1"
        out = engine.analyze_deterministic(req, request_id=rid, emergency_reason="overload_rejected")
        response.headers["X-Amber-Mode"] = "degraded-overload"
        return out
    timeout_seconds = request.app.state.settings.request_timeout_seconds
    deadline_monotonic = time.perf_counter() + timeout_seconds
    try:
        async with asyncio.timeout(timeout_seconds + 1.0):
            if request.app.state.settings.demo_mode and request.app.state.settings.demo_disable_external_llm:
                out = engine.analyze_deterministic(req, request_id=rid, emergency_reason="demo_mode_external_disabled")
            else:
                out = await engine.analyze(req, request_id=rid, deadline_monotonic=deadline_monotonic)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Время обработки запроса превышено") from exc
    finally:
        if runtime_guard:
            with suppress(Exception):
                await runtime_guard.release()
    response.headers["X-Amber-Mode"] = (
        "emergency" if out.meta.emergency_mode else "degraded" if out.meta.degraded_mode else "live"
    )
    if telemetry:
        latency_ms = int((time.perf_counter() - started) * 1000)
        telemetry.incr("analyze_requests_total")
        telemetry.observe_latency_ms("analyze", latency_ms)
        if out.meta.degraded_mode:
            telemetry.incr("analyze_degraded_total")
        if out.meta.emergency_mode:
            telemetry.incr("analyze_emergency_total")
        if out.meta.fallback_used:
            telemetry.incr("provider_fallback_total")
    logger.info(
        "analyze.done request_id=%s validator_status=%s emergency=%s degraded=%s llm_used=%s",
        rid,
        out.meta.validator_status,
        out.meta.emergency_mode,
        out.meta.degraded_mode,
        out.meta.llm_used,
    )
    return out


@router.post(
    "/ingest/csv",
    response_model=CsvIngestResponse,
    summary="CSV onboarding в normalized AnalyzeRequest",
    description="Нормализует банковские, crypto exchange и generic AML CSV-выгрузки в контракт Amber.",
)
async def ingest_csv_v1(
    request: Request,
    file: UploadFile = File(...),
    mode: Mode = Form(...),
    jurisdiction: Jurisdiction = Form(...),
    focus_last_n: int = Form(12),
    alert_id: str | None = Form(default=None),
    client_id_external: str | None = Form(default=None),
    column_overrides_json: str | None = Form(default=None),
) -> CsvIngestResponse:
    settings = request.app.state.settings
    telemetry = getattr(request.app.state, "telemetry", None)
    content = await file.read()
    if len(content) > settings.max_csv_bytes:
        raise HTTPException(status_code=413, detail="CSV превышает допустимый размер")
    column_overrides: dict[str, str] | None = None
    if column_overrides_json:
        try:
            raw_overrides = json.loads(column_overrides_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="column_overrides_json должен быть валидным JSON") from exc
        if not isinstance(raw_overrides, dict):
            raise HTTPException(status_code=422, detail="column_overrides_json должен быть объектом")
        column_overrides = {str(k): str(v) for k, v in raw_overrides.items() if v}
    try:
        out = csv_ingest_service.ingest_bytes(
            content=content,
            filename=file.filename,
            mode=mode,
            jurisdiction=jurisdiction,
            focus_last_n=focus_last_n,
            max_rows=settings.max_csv_rows,
            max_preview_rows=settings.max_csv_preview_rows,
            max_malformed_ratio=settings.max_malformed_ratio,
            alert_id=alert_id,
            client_id_external=client_id_external,
            column_overrides=column_overrides,
        )
        if telemetry and out.normalization_report:
            telemetry.incr("csv_ingest_requests_total")
            telemetry.observe_malformed_ratio(out.normalization_report.rejected_ratio)
        return out
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/case/export",
    response_model=CaseExportArtifact,
    summary="Экспорт supervised-review case artifact",
    description="Строит JSON, markdown или audit bundle без серверного хранения кейса.",
)
async def export_case_v1(request: Request, body: CaseExportRequest) -> CaseExportArtifact:
    settings = request.app.state.settings
    if settings.demo_mode and settings.demo_disable_exports:
        raise HTTPException(status_code=403, detail="Экспорт отключён в DEMO_MODE")
    case_export_service = CaseExportService(settings)
    return case_export_service.export(
        source_request=body.source_request,
        analysis=body.analysis,
        format=body.format,
    )


@router.post(
    "/export/case",
    summary="Экспорт replayable ZIP case bundle",
    description="Возвращает ZIP bundle для supervised review и forensic replay без серверного хранения.",
)
async def export_case_zip_v1(request: Request, body: CaseExportRequest) -> Response:
    settings = request.app.state.settings
    if settings.demo_mode and settings.demo_disable_exports:
        raise HTTPException(status_code=403, detail="Экспорт отключён в DEMO_MODE")
    case_export_service = CaseExportService(settings)
    payload, filename, digest = case_export_service.export_zip_bytes(
        source_request=body.source_request,
        analysis=body.analysis,
    )
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Amber-Bundle-SHA256": digest,
    }
    return Response(content=payload, media_type="application/zip", headers=headers)


@router.post(
    "/export/sar",
    summary="Export SAR memo in txt/markdown/docx",
    description="Возвращает human-review separated SAR artifact в одном из lightweight форматов.",
)
async def export_sar_v1(
    request: Request,
    body: CaseExportRequest,
    format: SarExportFormat = "txt",
) -> Response:
    settings = request.app.state.settings
    if settings.demo_mode and settings.demo_disable_exports:
        raise HTTPException(status_code=403, detail="Экспорт отключён в DEMO_MODE")
    case_export_service = CaseExportService(settings)
    payload, filename, media_type = case_export_service.export_sar_bytes(
        source_request=body.source_request,
        analysis=body.analysis,
        format=format,
    )
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/replay",
    response_model=ReplayResponse,
    summary="Deterministic replay exported bundle",
    description="Не вызывает LLM. Пересчитывает deterministic pipeline, сверяет hash manifest и возвращает drift report.",
)
async def replay_bundle_v1(
    request: Request,
    file: UploadFile = File(...),
) -> ReplayResponse:
    content = await file.read()
    replay = ReplayService(request.app.state.settings)
    out = replay.replay_bundle(content)
    telemetry = getattr(request.app.state, "telemetry", None)
    if telemetry:
        telemetry.incr("replay_requests_total")
        if out.drift_detected:
            telemetry.incr("replay_drift_total")
    return out
