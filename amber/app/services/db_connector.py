"""Read-only DB snapshot connectors (no persistence, deterministic preview)."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.core.config import Settings
from app.models.operations import DbConnectorType, DbImportPreviewRequest
from app.models.schemas import CsvIngestResponse
from app.services.connector_governance import build_provenance
from app.services.csv_ingest import CsvIngestService

_FORBIDDEN_SQL = re.compile(r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate)\b", re.I)


class DbConnectorService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._csv = CsvIngestService()

    def preview_import(self, body: DbImportPreviewRequest) -> CsvIngestResponse:
        if _FORBIDDEN_SQL.search(body.query):
            raise ValueError("Only read-only SELECT queries are allowed.")
        rows, columns = self._fetch_rows(body.connector_type, body.connection_uri, body.query)
        if not rows:
            raise ValueError("Query returned no rows.")
        csv_text = self._rows_to_csv(columns, rows)
        result = self._csv.ingest_bytes(
            content=csv_text.encode("utf-8"),
            filename=f"db.{body.connector_type}",
            mode=body.mode,
            jurisdiction=body.jurisdiction,
            focus_last_n=body.focus_last_n,
            max_rows=self._settings.max_csv_rows,
            max_preview_rows=self._settings.max_csv_preview_rows,
            max_malformed_ratio=self._settings.max_malformed_ratio,
        )
        provenance = build_provenance(
            source_type="database",
            connector_name=f"db.{body.connector_type}",
            imported_by=body.imported_by,
            normalization_report=(
                result.normalization_report.model_dump(mode="json") if result.normalization_report else None
            ),
            malformed_ratio=result.normalization_report.rejected_ratio if result.normalization_report else 0.0,
            fingerprint_payload={
                "connector": body.connector_type,
                "table": body.table_name,
                "query_hash": hashlib.sha256(body.query.encode("utf-8")).hexdigest(),
            },
        )
        extra = dict(result.normalized_request.extra_context or {})
        extra["connector_provenance"] = provenance.model_dump(mode="json")
        result.normalized_request = result.normalized_request.model_copy(update={"extra_context": extra})
        return result

    def _fetch_rows(self, connector: DbConnectorType, uri: str, query: str) -> tuple[list[dict[str, Any]], list[str]]:
        if connector == "sqlite":
            parsed = urlparse(uri)
            path = parsed.path
            if path.startswith("/") and len(path) > 3 and path[2] == ":":
                path = path[1:]
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.execute(query)
                fetched = cur.fetchmany(self._settings.max_csv_rows)
                columns = [d[0] for d in cur.description] if cur.description else []
                return [dict(row) for row in fetched], columns
            finally:
                conn.close()
        if connector == "postgresql":
            try:
                import psycopg
            except ImportError as exc:
                raise ValueError("PostgreSQL connector requires psycopg package.") from exc
            with psycopg.connect(uri) as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    columns = [d.name for d in cur.description] if cur.description else []
                    fetched = cur.fetchmany(self._settings.max_csv_rows)
                    return [dict(zip(columns, row)) for row in fetched], columns
        if connector == "mysql":
            try:
                import pymysql
            except ImportError as exc:
                raise ValueError("MySQL connector requires pymysql package.") from exc
            conn = pymysql.connect(uri)
            try:
                with conn.cursor() as cur:
                    cur.execute(query)
                    columns = [d[0] for d in cur.description] if cur.description else []
                    fetched = cur.fetchmany(self._settings.max_csv_rows)
                    return [dict(zip(columns, row)) for row in fetched], columns
            finally:
                conn.close()
        raise ValueError(f"Unsupported connector: {connector}")

    @staticmethod
    def _rows_to_csv(columns: list[str], rows: list[dict[str, Any]]) -> str:
        import csv
        import io

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col) for col in columns})
        return buf.getvalue()
