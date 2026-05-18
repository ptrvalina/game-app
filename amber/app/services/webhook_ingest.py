"""Deterministic webhook JSON ingest (stateless normalization)."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from decimal import Decimal

from app.core.config import Settings
from app.models.operations import WebhookIngestRequest
from app.models.schemas import AnalyzeRequest, CsvIngestResponse, TransactionRecord
from app.services.connector_governance import build_provenance
from app.services.csv_ingest import CsvIngestService


class WebhookIngestService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._csv = CsvIngestService()

    def ingest(self, body: WebhookIngestRequest) -> CsvIngestResponse:
        if body.webhook_signature and self._settings.bundle_signing_secret:
            expected = hmac.new(
                self._settings.bundle_signing_secret.encode("utf-8"),
                json.dumps(body.transactions, sort_keys=True, default=str).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected, body.webhook_signature):
                raise ValueError("Webhook signature validation failed.")
        rows = []
        for idx, row in enumerate(body.transactions, start=1):
            amount_raw = row.get("amount")
            if amount_raw is None:
                raise ValueError(f"transactions[{idx}] missing amount")
            rows.append(
                {
                    "timestamp": row.get("timestamp") or row.get("ts") or row.get("date"),
                    "amount": str(amount_raw),
                    "currency": row.get("currency"),
                    "direction": row.get("direction", "unknown"),
                    "counterparty": row.get("counterparty"),
                    "channel": row.get("channel"),
                    "narrative": row.get("narrative"),
                    "asset_type": row.get("asset_type"),
                    "id": row.get("id"),
                }
            )
        csv_text = self._rows_to_csv(rows)
        result = self._csv.ingest_bytes(
            content=csv_text.encode("utf-8"),
            filename="webhook.json",
            mode=body.mode,
            jurisdiction=body.jurisdiction,
            focus_last_n=min(len(rows), 500),
            max_rows=self._settings.max_csv_rows,
            max_preview_rows=self._settings.max_csv_preview_rows,
            max_malformed_ratio=self._settings.max_malformed_ratio,
            alert_id=body.alert_id,
            client_id_external=body.client_id_external,
        )
        provenance = build_provenance(
            source_type="webhook",
            connector_name="ingest.webhook",
            imported_by=body.imported_by,
            normalization_report=(
                result.normalization_report.model_dump(mode="json") if result.normalization_report else None
            ),
            malformed_ratio=result.normalization_report.rejected_ratio if result.normalization_report else 0.0,
            fingerprint_payload={"mode": body.mode, "jurisdiction": body.jurisdiction, "tx_count": len(rows)},
        )
        # attach via summary extra - store on normalized_request extra_context
        req = result.normalized_request
        extra = dict(req.extra_context or {})
        extra["connector_provenance"] = provenance.model_dump(mode="json")
        result.normalized_request = req.model_copy(update={"extra_context": extra})
        return result

    @staticmethod
    def _rows_to_csv(rows: list[dict[str, object | None]]) -> str:
        import csv
        import io

        headers = ["timestamp", "amount", "currency", "direction", "counterparty", "channel", "narrative", "asset_type", "id"]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in headers})
        return buf.getvalue()
