"""Lightweight scheduled import metadata (no background workers)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from app.models.operations import ScheduledImportMetadata, ScheduledImportPreviewRequest


class ScheduledImportService:
    def preview_schedule(self, body: ScheduledImportPreviewRequest) -> ScheduledImportMetadata:
        now = datetime.now(timezone.utc)
        fingerprint = hashlib.sha256(
            f"{body.connector_name}|{body.interval_hours}|{body.mode}|{body.jurisdiction}".encode("utf-8")
        ).hexdigest()
        schedule_id = fingerprint[:16]
        return ScheduledImportMetadata(
            schedule_id=schedule_id,
            connector_name=body.connector_name,
            interval_hours=body.interval_hours,
            next_run_preview=now + timedelta(hours=body.interval_hours),
            last_run_preview=None,
            deterministic_fingerprint=fingerprint,
        )
