"""Версионированное API."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response

from app.deps import get_engine
from app.models.schemas import AnalyzeRequest, AnalyzeResponse
from app.xai.engine import XAIEngine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Анализ"])


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
    logger.info(
        "analyze.start request_id=%s mode=%s jurisdiction=%s",
        rid,
        req.mode,
        req.jurisdiction,
    )
    out = await engine.analyze(req, request_id=rid)
    response.headers["X-Amber-Mode"] = "degraded" if out.meta.degraded_mode else "live"
    logger.info(
        "analyze.done request_id=%s emergency=%s degraded=%s llm_used=%s",
        rid,
        out.meta.emergency_mode,
        out.meta.degraded_mode,
        out.meta.llm_used,
    )
    return out
