"""Deterministic connector provenance fingerprints."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.models.operations import ConnectorProvenance


def deterministic_import_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_provenance(
    *,
    source_type: str,
    connector_name: str,
    imported_by: str | None,
    normalization_report: dict[str, Any] | None,
    malformed_ratio: float,
    fingerprint_payload: dict[str, Any],
) -> ConnectorProvenance:
    report_hash = None
    if normalization_report:
        report_hash = deterministic_import_hash(normalization_report)
    return ConnectorProvenance(
        source_type=source_type,
        connector_name=connector_name,
        imported_by=imported_by,
        import_timestamp=datetime.now(timezone.utc),
        normalization_report_sha256=report_hash,
        malformed_ratio=malformed_ratio,
        deterministic_hash=deterministic_import_hash(fingerprint_payload),
    )
