"""
Прослойка к LLM: OpenAI (основной) и Anthropic (резерв), с fallback, retry и circuit breaker.

Zero retention полностью зависит от условий контракта с провайдером. На стороне Amber
данные не логируются в сыром виде и не хранятся за пределами обработки запроса.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    configured: bool = False
    consecutive_failures: int = 0
    circuit_open_until: datetime | None = None
    last_error_code: str | None = None
    last_success_at: datetime | None = None


@dataclass
class LLMCallResult:
    data: dict[str, Any]
    provider: str
    model: str
    retries: int
    fallback_used: bool
    latency_ms: int
    prompt_chars: int


class LLMProvider:
    """Вызовы LLM с JSON-ответом и цепочкой fallback (только доступные провайдеры)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._openai = None
        self._anthropic = None
        self._health = {
            "openai": ProviderHealth(configured=bool(settings.openai_api_key)),
            "anthropic": ProviderHealth(configured=bool(settings.anthropic_api_key)),
        }
        timeout = httpx.Timeout(15.0, read=settings.llm_timeout_seconds)
        try:
            from openai import AsyncOpenAI

            if settings.openai_api_key:
                self._openai = AsyncOpenAI(api_key=settings.openai_api_key, timeout=timeout)
        except ImportError:
            logger.warning("Пакет openai не установлен — ветка OpenAI недоступна")
        try:
            from anthropic import AsyncAnthropic

            if settings.anthropic_api_key:
                try:
                    self._anthropic = AsyncAnthropic(
                        api_key=settings.anthropic_api_key,
                        timeout=timeout,
                    )
                except TypeError:
                    self._anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)
        except ImportError:
            logger.warning("Пакет anthropic не установлен — ветка Anthropic недоступна")

    def health_snapshot(self) -> dict[str, dict[str, Any]]:
        now = datetime.now(timezone.utc)
        return {
            name: {
                "configured": state.configured,
                "circuit_open": bool(state.circuit_open_until and state.circuit_open_until > now),
                "consecutive_failures": state.consecutive_failures,
                "last_error_code": state.last_error_code,
                "last_success_at": state.last_success_at.isoformat() if state.last_success_at else None,
                "zero_retention_contract_required": True,
            }
            for name, state in self._health.items()
        }

    def _providers_to_try(self) -> list[str]:
        primary = self._settings.llm_primary
        order = [primary] + [p for p in ("openai", "anthropic") if p != primary]
        out: list[str] = []
        for name in order:
            if self._is_circuit_open(name):
                continue
            if name == "openai" and self._openai:
                out.append("openai")
            elif name == "anthropic" and self._anthropic:
                out.append("anthropic")
        return out

    async def complete_json(
        self,
        *,
        stage: str,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMCallResult:
        providers = self._providers_to_try()
        if not providers:
            raise RuntimeError("Нет доступных LLM-провайдеров: ключи отсутствуют или circuit breaker открыт")

        prompt_chars = len(system) + len(user)
        last_err: Exception | None = None

        for provider_idx, name in enumerate(providers):
            started = time.perf_counter()
            retries_used = 0
            for attempt in range(self._settings.llm_max_retries + 1):
                try:
                    if name == "openai":
                        data = await self._openai_json(
                            system=system,
                            user=user,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        )
                        model = self._settings.openai_model
                    else:
                        data = await self._anthropic_json(
                            system=system,
                            user=user,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        )
                        model = self._settings.anthropic_model

                    self._record_success(name)
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    return LLMCallResult(
                        data=data,
                        provider=name,
                        model=model,
                        retries=retries_used,
                        fallback_used=provider_idx > 0,
                        latency_ms=latency_ms,
                        prompt_chars=prompt_chars,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    error_code = self._error_code(exc)
                    retryable = self._is_retryable(exc)
                    self._record_failure(name, error_code=error_code)
                    logger.warning(
                        "llm.%s failed stage=%s provider=%s attempt=%s retryable=%s error=%s",
                        "call",
                        stage,
                        name,
                        attempt + 1,
                        retryable,
                        error_code,
                    )
                    if retryable and attempt < self._settings.llm_max_retries:
                        retries_used += 1
                        await asyncio.sleep(min(2**attempt, 3))
                        continue
                    break

        raise RuntimeError(f"Все LLM-провайдеры вернули ошибку: {self._error_code(last_err)}")

    def _is_circuit_open(self, provider: str) -> bool:
        state = self._health[provider]
        return bool(state.circuit_open_until and state.circuit_open_until > datetime.now(timezone.utc))

    def _record_success(self, provider: str) -> None:
        state = self._health[provider]
        state.consecutive_failures = 0
        state.circuit_open_until = None
        state.last_error_code = None
        state.last_success_at = datetime.now(timezone.utc)

    def _record_failure(self, provider: str, *, error_code: str) -> None:
        state = self._health[provider]
        state.consecutive_failures += 1
        state.last_error_code = error_code
        if state.consecutive_failures >= self._settings.llm_circuit_breaker_threshold:
            state.circuit_open_until = datetime.now(timezone.utc) + timedelta(
                seconds=self._settings.llm_circuit_breaker_seconds
            )

    def _is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, TimeoutError, httpx.NetworkError)):
            return True
        msg = str(exc).lower()
        return any(token in msg for token in ("timeout", "rate limit", "429", "temporar", "connection reset"))

    def _error_code(self, exc: Exception | None) -> str:
        if exc is None:
            return "unknown"
        return exc.__class__.__name__

    async def _openai_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        assert self._openai is not None
        resp = await self._openai.chat.completions.create(
            model=self._settings.openai_model,
            temperature=temperature,
            top_p=1,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content or "{}"
        return self._parse_json_object(text)

    async def _anthropic_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        assert self._anthropic is not None
        msg = await self._anthropic.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system + "\n\nОтветь ТОЛЬКО валидным JSON без markdown.",
            messages=[{"role": "user", "content": user}],
        )
        text = ""
        for block in msg.content:
            if block.type == "text":
                text += block.text
        return self._parse_json_object(text)

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        normalized = text.strip()
        if normalized.startswith("```"):
            lines = normalized.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            normalized = "\n".join(lines).strip()
        parsed = json.loads(normalized)
        if not isinstance(parsed, dict):
            raise ValueError("LLM вернул не JSON object")
        return parsed
