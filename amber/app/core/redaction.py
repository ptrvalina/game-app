"""Deterministic masking of common PII for logs and UI previews."""
from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\-\s()]{7,}\d)(?!\w)")
_ACCOUNT_RE = re.compile(r"\b\d{10,20}\b")
_WALLET_RE = re.compile(r"\b(?:0x[a-fA-F0-9]{10,}|[13][a-km-zA-HJ-NP-Z1-9]{10,}|[A-Za-z0-9]{18,64})\b")


def mask_identifier(value: str | None, *, keep_prefix: int = 2, keep_suffix: int = 2) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if len(raw) <= keep_prefix + keep_suffix:
        return "*" * len(raw)
    middle = "*" * (len(raw) - keep_prefix - keep_suffix)
    return f"{raw[:keep_prefix]}{middle}{raw[-keep_suffix:]}"


def redact_pii_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value
    text = _EMAIL_RE.sub(lambda m: f"{mask_identifier(m.group(1), keep_prefix=1, keep_suffix=1)}@{m.group(2)}", text)
    text = _PHONE_RE.sub(lambda m: mask_identifier(re.sub(r"\D+", "", m.group(1)), keep_prefix=2, keep_suffix=2) or "", text)
    text = _ACCOUNT_RE.sub(lambda m: mask_identifier(m.group(0), keep_prefix=2, keep_suffix=2) or "", text)
    text = _WALLET_RE.sub(lambda m: mask_identifier(m.group(0), keep_prefix=4, keep_suffix=4) or "", text)
    return text


def redact_mapping(values: dict[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, str):
            redacted[key] = redact_pii_text(value)
        else:
            redacted[key] = value
    return redacted
