"""Подготовка данных для LLM: объём и защита от перегруза контекста."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.models.schemas import AnalyzeRequest


def cap_historical_for_profiler(req: AnalyzeRequest, max_rows: int) -> list:
    h = req.historical_transactions
    if max_rows <= 0 or len(h) <= max_rows:
        return list(h)
    return list(h[-max_rows:])


def clone_for_llm(req: AnalyzeRequest, max_historical: int) -> AnalyzeRequest:
    """Для промптов — только хвост истории (свежие паттерны важнее)."""
    hist = req.historical_transactions
    if max_historical > 0 and len(hist) > max_historical:
        hist = hist[-max_historical:]
    return req.model_copy(update={"historical_transactions": hist})


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
        max_narrative = max(80, max_narrative // 2)
        truncated = True
    return data, True


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
