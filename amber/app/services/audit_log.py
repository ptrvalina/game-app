"""Append-only deterministic audit event stream (hash-chained, replay-safe)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.models.operations import AmberRole, AuditEvent, AuditEventType, LifecycleEvent, LifecycleEventName


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_event_hash(*, prev_hash: str, sequence: int, event_type: str, occurred_at: datetime, details: dict[str, Any]) -> str:
    body = {
        "prev_hash": prev_hash,
        "sequence": sequence,
        "event_type": event_type,
        "occurred_at": occurred_at.isoformat(),
        "details": details,
    }
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def append_audit_event(
    events: list[AuditEvent],
    *,
    event_type: AuditEventType,
    actor_role: AmberRole | None = None,
    actor_id: str | None = None,
    details: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> AuditEvent:
    when = occurred_at or datetime.now(timezone.utc)
    prev = events[-1].event_hash if events else "genesis"
    sequence = len(events) + 1
    payload = details or {}
    event_hash = compute_event_hash(
        prev_hash=prev,
        sequence=sequence,
        event_type=event_type,
        occurred_at=when,
        details=payload,
    )
    event = AuditEvent(
        sequence=sequence,
        event_type=event_type,
        occurred_at=when,
        actor_role=actor_role,
        actor_id=actor_id,
        details=payload,
        prev_hash=prev,
        event_hash=event_hash,
    )
    events.append(event)
    return event


def append_lifecycle(
    events: list[LifecycleEvent],
    *,
    event: LifecycleEventName,
    actor_id: str | None = None,
    note: str | None = None,
    occurred_at: datetime | None = None,
) -> LifecycleEvent:
    item = LifecycleEvent(
        event=event,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        actor_id=actor_id,
        note=note,
    )
    events.append(item)
    return item


def verify_audit_chain(events: list[AuditEvent]) -> bool:
    prev = "genesis"
    for event in events:
        if event.prev_hash != prev:
            return False
        expected = compute_event_hash(
            prev_hash=event.prev_hash,
            sequence=event.sequence,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            details=event.details,
        )
        if expected != event.event_hash:
            return False
        prev = event.event_hash
    return True
