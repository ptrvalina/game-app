"""Lightweight deterministic RBAC (header-based roles, no external IAM)."""
from __future__ import annotations

from app.models.operations import AmberRole, WorkflowAction

_ROLE_ORDER = {"readonly": 0, "auditor": 1, "analyst": 2, "reviewer": 3, "supervisor": 4}

_PERMISSIONS: dict[WorkflowAction, set[AmberRole]] = {
    "assign": {"analyst", "reviewer", "supervisor"},
    "reassign": {"reviewer", "supervisor"},
    "set_status": {"analyst", "reviewer", "supervisor"},
    "set_disposition": {"analyst", "reviewer", "supervisor"},
    "escalate": {"analyst", "reviewer", "supervisor"},
    "approve": {"reviewer", "supervisor"},
    "supervisor_approve": {"supervisor"},
    "close": {"supervisor"},
}

_EXPORT_PERMISSIONS: dict[str, set[AmberRole]] = {
    "zip_bundle": {"analyst", "reviewer", "supervisor", "auditor"},
    "sar": {"analyst", "reviewer", "supervisor", "auditor"},
    "evidence_csv": {"analyst", "reviewer", "supervisor", "auditor", "readonly"},
    "replay": {"reviewer", "supervisor", "auditor"},
}


class RbacDenied(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def parse_role(header_value: str | None) -> AmberRole:
    if not header_value:
        return "analyst"
    normalized = header_value.strip().lower()
    if normalized not in _ROLE_ORDER:
        return "analyst"
    return normalized  # type: ignore[return-value]


def assert_workflow_action(role: AmberRole, action: WorkflowAction) -> None:
    allowed = _PERMISSIONS.get(action, set())
    if role not in allowed:
        raise RbacDenied(f"Role '{role}' cannot perform action '{action}'.")


def assert_export(role: AmberRole, export_type: str) -> None:
    allowed = _EXPORT_PERMISSIONS.get(export_type, set())
    if role not in allowed:
        raise RbacDenied(f"Role '{role}' cannot export '{export_type}'.")
