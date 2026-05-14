"""Централизованная конфигурация Amber (12-factor, без секретов в коде)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    environment: str = Field(default="dev", alias="AMBER_ENV")
    log_level: str = Field(default="INFO", alias="AMBER_LOG_LEVEL")
    cors_origins: str = Field(default="*", alias="AMBER_CORS_ORIGINS")
    api_key: str | None = Field(default=None, alias="AMBER_API_KEY")
    enable_console: bool = Field(default=True, alias="AMBER_ENABLE_CONSOLE")
    enable_docs: bool = Field(default=True, alias="AMBER_ENABLE_DOCS")
    strict_routing: bool = Field(default=True, alias="AMBER_STRICT_ROUTING")
    strict_fiat_guard: bool = Field(default=True, alias="AMBER_STRICT_FIAT_GUARD")
    enforce_python312: bool = Field(default=False, alias="AMBER_ENFORCE_PYTHON312")
    demo_mode: bool = Field(default=False, alias="AMBER_DEMO_MODE")
    demo_disable_exports: bool = Field(default=False, alias="AMBER_DEMO_DISABLE_EXPORTS")
    demo_disable_external_llm: bool = Field(default=False, alias="AMBER_DEMO_DISABLE_EXTERNAL_LLM")

    llm_timeout_seconds: float = Field(default=25.0, alias="AMBER_LLM_TIMEOUT_SECONDS", ge=3.0, le=120.0)
    llm_wall_budget_seconds: float = Field(
        default=38.0,
        alias="AMBER_LLM_WALL_BUDGET_SECONDS",
        ge=8.0,
        le=180.0,
        description="Hard wall-clock cap for a single complete_json call across providers/retries",
    )
    llm_max_retries: int = Field(default=1, alias="AMBER_LLM_MAX_RETRIES", ge=0, le=5)
    llm_circuit_breaker_threshold: int = Field(
        default=3,
        alias="AMBER_LLM_CIRCUIT_BREAKER_THRESHOLD",
        ge=1,
        le=20,
    )
    llm_circuit_breaker_seconds: int = Field(
        default=60,
        alias="AMBER_LLM_CIRCUIT_BREAKER_SECONDS",
        ge=5,
        le=3600,
    )
    max_profiler_transactions: int = Field(
        default=5000,
        alias="AMBER_MAX_PROFILER_TRANSACTIONS",
        ge=10,
        le=100_000,
    )
    max_llm_historical: int = Field(
        default=150,
        alias="AMBER_MAX_LLM_HISTORICAL",
        ge=0,
        le=2000,
    )
    max_llm_payload_chars: int = Field(
        default=120_000,
        alias="AMBER_MAX_LLM_PAYLOAD_CHARS",
        ge=10_000,
        le=500_000,
    )
    request_timeout_seconds: float = Field(
        default=45.0,
        alias="AMBER_REQUEST_TIMEOUT_SECONDS",
        ge=5.0,
        le=300.0,
    )
    max_request_bytes: int = Field(
        default=1_500_000,
        alias="AMBER_MAX_REQUEST_BYTES",
        ge=32_768,
        le=20_000_000,
    )
    max_csv_bytes: int = Field(
        default=2_000_000,
        alias="AMBER_MAX_CSV_BYTES",
        ge=32_768,
        le=20_000_000,
    )
    max_csv_rows: int = Field(
        default=10_000,
        alias="AMBER_MAX_CSV_ROWS",
        ge=100,
        le=500_000,
    )
    max_csv_preview_rows: int = Field(default=12, alias="AMBER_MAX_CSV_PREVIEW_ROWS", ge=3, le=100)
    max_malformed_ratio: float = Field(default=0.35, alias="AMBER_MAX_MALFORMED_RATIO", ge=0.0, le=1.0)
    max_concurrent_requests: int = Field(default=8, alias="AMBER_MAX_CONCURRENT_REQUESTS", ge=1, le=256)
    bundle_signing_secret: str = Field(
        default="amber-dev-signing-secret-change-me",
        alias="AMBER_BUNDLE_SIGNING_SECRET",
        min_length=16,
        max_length=256,
    )

    llm_primary: str = Field(default="openai", alias="AMBER_LLM_PRIMARY")
    openai_model: str = Field(default="gpt-4o", alias="AMBER_OPENAI_MODEL")
    anthropic_model: str = Field(
        default="claude-3-5-sonnet-20241022",
        alias="AMBER_ANTHROPIC_MODEL",
    )
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    @field_validator("environment")
    @classmethod
    def _env(cls, v: str) -> str:
        env = v.strip().lower()
        if env not in {"dev", "test", "staging", "prod"}:
            raise ValueError("AMBER_ENV должен быть одним из: dev, test, staging, prod")
        return env

    @field_validator("llm_primary")
    @classmethod
    def _primary(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ("openai", "anthropic"):
            raise ValueError("AMBER_LLM_PRIMARY должен быть openai или anthropic")
        return v

    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def docs_enabled(self) -> bool:
        return self.enable_docs and self.environment != "prod"

    @property
    def console_enabled(self) -> bool:
        return self.enable_console and self.environment != "prod"

    def validate_runtime(self) -> None:
        import sys

        if (self.enforce_python312 or self.environment in {"staging", "prod"}) and sys.version_info[:2] != (3, 12):
            raise RuntimeError("Amber в production режиме требует Python 3.12")
        if self.environment in {"staging", "prod"} and self.bundle_signing_secret == "amber-dev-signing-secret-change-me":
            raise RuntimeError("AMBER_BUNDLE_SIGNING_SECRET должен быть переопределён вне dev/test")
        if self.demo_mode and self.environment == "prod":
            raise RuntimeError("DEMO_MODE нельзя включать в prod")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime()
    return settings
