"""
FastAPI-приложение Amber: API v1, health, веб-консоль, middleware.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.errors import build_error_response, sanitize_validation_errors
from app.middleware.api_key import ApiKeyMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.models.schemas import AnalyzeRequest, AnalyzeResponse
from app.deps import get_engine
from app.xai.engine import XAIEngine

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SETTINGS = get_settings()


def _configure_logging(level: str) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging(SETTINGS.log_level)
    app.state.settings = SETTINGS
    app.state.engine = XAIEngine(SETTINGS)
    logging.getLogger(__name__).info("Amber стартовал (engine готов)")
    yield


TAGS_METADATA = [
    {"name": "Сервис", "description": "Проверка доступности и метаданные."},
    {"name": "Анализ", "description": "XAI-анализ алерта: Router, Analyst, Reporter, профиль, аномалии."},
]

app = FastAPI(
    title="Amber",
    version="1.0.0",
    description=(
        "**AI Compliance Copilot** — интеллектуальный слой поверх AML. "
        "Режимы: `fiat`, `crypto`, `cross`. Юрисдикции: `RU`, `BY`, `EU`. "
        "Данные запроса не сохраняются на стороне сервиса."
    ),
    lifespan=lifespan,
    openapi_tags=TAGS_METADATA,
    docs_url="/docs" if SETTINGS.docs_enabled else None,
    redoc_url="/redoc" if SETTINGS.docs_enabled else None,
    openapi_url="/openapi.json" if SETTINGS.docs_enabled else None,
)

def _cors_options() -> tuple[list[str], bool]:
    """
    Браузеры не принимают Access-Control-Allow-Origin: * вместе с credentials.
    Для дефолтного «*» отключаем credentials; для явного списка хостов — включаем.
    """
    origins = SETTINGS.cors_origins_list()
    if len(origins) == 1 and origins[0] == "*":
        return origins, False
    return origins, True


_cors_origins, _cors_credentials = _cors_options()

app.add_middleware(GZipMiddleware, minimum_size=800)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(RequestIdMiddleware)

app.include_router(v1_router, prefix="/api/v1")


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    body = build_error_response(
        code="validation_error",
        message="Тело запроса не соответствует схеме.",
        request_id=rid,
        details=sanitize_validation_errors(exc.errors()),
    )
    return JSONResponse(status_code=422, content=body.model_dump(mode="json"))


@app.exception_handler(StarletteHTTPException)
async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    body = build_error_response(
        code="http_error",
        message=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
        request_id=rid,
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    logging.getLogger(__name__).exception("Необработанная ошибка request_id=%s", rid)
    body = build_error_response(
        code="internal_error",
        message="Внутренняя ошибка сервера. Повторите запрос или обратитесь к администратору.",
        request_id=rid,
    )
    return JSONResponse(status_code=500, content=body.model_dump(mode="json"))


@app.get("/", tags=["Сервис"], summary="Консоль")
async def root() -> RedirectResponse:
    if SETTINGS.console_enabled:
        return RedirectResponse(url="/console", status_code=302)
    if SETTINGS.docs_enabled:
        return RedirectResponse(url="/docs", status_code=302)
    return RedirectResponse(url="/health", status_code=302)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Пустой ответ: без маршрута браузер запрашивает /favicon и может попасть под AMBER_API_KEY."""
    return Response(status_code=204)


@app.get("/console", tags=["Сервис"], summary="Веб-консоль демо")
async def console(request: Request) -> Response:
    if not SETTINGS.console_enabled:
        raise HTTPException(status_code=404, detail="Console отключён в текущем окружении")
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        rid = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=503,
            content=build_error_response(
                code="console_unavailable",
                message="static/index.html не найден",
                request_id=rid,
            ).model_dump(mode="json"),
        )
    return FileResponse(index, media_type="text/html; charset=utf-8")


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health", tags=["Сервис"], summary="Liveness")
async def health() -> dict:
    return {"status": "ok", "service": "amber", "version": app.version}


@app.get("/ready", tags=["Сервис"], summary="Readiness (ключи LLM)")
async def ready(request: Request) -> dict:
    s = getattr(request.app.state, "settings", None) or SETTINGS
    engine: XAIEngine | None = getattr(request.app.state, "engine", None)
    llm_status = engine.llm.health_snapshot() if engine else {}
    configured_count = int(bool(s.openai_api_key)) + int(bool(s.anthropic_api_key))
    if configured_count == 0:
        status = "emergency-only"
    elif any(item.get("circuit_open") for item in llm_status.values()):
        status = "degraded"
    else:
        status = "ready"
    return {
        "status": status,
        "llm": {
            "openai_configured": bool(s.openai_api_key),
            "anthropic_configured": bool(s.anthropic_api_key),
            "providers": llm_status,
        },
        "api_key_required": bool(s.api_key),
        "console_enabled": s.console_enabled,
        "docs_enabled": s.docs_enabled,
    }


@app.post(
    "/analyze",
    response_model=AnalyzeResponse,
    tags=["Анализ"],
    summary="Анализ (legacy-путь)",
    deprecated=True,
    description="Используйте `POST /api/v1/analyze` — тот же контракт.",
)
async def analyze_legacy(req: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    engine: XAIEngine = get_engine(request)
    rid = getattr(request.state, "request_id", None)
    return await engine.analyze(req, request_id=rid)
