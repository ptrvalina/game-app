"""Подготовка данных для LLM: объём и защита от перегруза контекста."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.models.schemas import AnalyzeRequest


def cap_historical_for_profiler(req: AnalyzeRequest, max_rows: int) -> list:
    h = _sorted_transactions(req.historical_transactions)
    if max_rows <= 0 or len(h) <= max_rows:
        return list(h)
    return list(h[-max_rows:])


def clone_for_llm(req: AnalyzeRequest, max_historical: int) -> AnalyzeRequest:
    """Для промптов — только хвост истории (свежие паттерны важнее)."""
    hist = _sorted_transactions(req.historical_transactions)
    focus = _sorted_transactions(req.focus_transactions)
    if max_historical > 0 and len(hist) > max_historical:
        hist = hist[-max_historical:]
    return req.model_copy(update={"historical_transactions": hist, "focus_transactions": focus})


def shrink_payload_inplace(data: Any, max_chars: int) -> tuple[Any, bool]:
    """
    Если JSON слишком большой — укорачиваем narrative/notes и массивы истории.
    """
    data = deepcopy(data)
    truncated = False
    max_narrative = 400
    for _ in range(4):
        s = json.dumps(data, ensure_ascii=False)
        if len(s) <= max_chars:
            return data, truncated
        _truncate_narratives(data, max_narrative)
        _cap_history_arrays(data)
        _drop_low_value_fields(data)
        max_narrative = max(80, max_narrative // 2)
        truncated = True
    while len(json.dumps(data, ensure_ascii=False)) > max_chars:
        truncated = True
        if _aggressive_reduce(data):
            continue
        return {"payload_truncated": True, "payload_summary": "Payload omitted due to hard size cap."}, True
    return data, truncated


def _truncate_narratives(obj: Any, limit: int) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "narrative" and isinstance(v, str) and len(v) > limit:
                obj[k] = v[:limit] + "…"
            elif k == "notes" and isinstance(v, str) and len(v) > limit * 2:
                obj[k] = v[: limit * 2] + "…"
            else:
                _truncate_narratives(v, limit)
    elif isinstance(obj, list):
        for item in obj:
            _truncate_narratives(item, limit)


def _cap_history_arrays(obj: Any) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in {"historical_transactions", "transactions_excerpt"} and isinstance(v, list) and len(v) > 50:
                obj[k] = v[-50:]
                obj[f"{k}_truncated"] = True
            else:
                _cap_history_arrays(v)
    elif isinstance(obj, list):
        for item in obj:
            _cap_history_arrays(item)


def _drop_low_value_fields(obj: Any) -> None:
    if isinstance(obj, dict):
        for key in ("extra_context", "client_notes", "profiler_text"):
            if key in obj and obj[key]:
                obj[key] = None
        for value in obj.values():
            _drop_low_value_fields(value)
    elif isinstance(obj, list):
        for item in obj:
            _drop_low_value_fields(item)


def _aggressive_reduce(obj: Any) -> bool:
    if isinstance(obj, dict):
        for key in ("focus_transactions_compact", "focus_transactions", "transactions_excerpt", "historical_transactions"):
            value = obj.get(key)
            if isinstance(value, list) and len(value) > 10:
                obj[key] = value[-10:]
                obj[f"{key}_truncated"] = True
                return True
        for key in list(obj.keys()):
            if key.endswith("_text") and isinstance(obj[key], str):
                obj[key] = obj[key][:160] + "…"
                return True
        for value in obj.values():
            if _aggressive_reduce(value):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if _aggressive_reduce(item):
                return True
    return False


def _sorted_transactions(txs: list) -> list:
    return sorted(
        list(txs),
        key=lambda tx: (_sortable_ts(tx.ts) is None, _sortable_ts(tx.ts) or datetime.max.replace(tzinfo=timezone.utc), tx.id or ""),
    )


def _sortable_ts(ts):
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)
