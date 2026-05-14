"""CSV ingest: delimiter, encoding, malformed rows, size limits."""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services.csv_ingest import CsvIngestService


@pytest.fixture
def client() -> TestClient:
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_csv_semicolon_delimiter() -> None:
    text = "timestamp;amount;currency;direction;asset_type;id\n2026-01-01T10:00:00;100.00;EUR;in;fiat;a1\n"
    svc = CsvIngestService()
    parsed = svc.parse_transactions(content=text.encode("utf-8"), mode="fiat", max_rows=100)
    assert parsed.delimiter == ";"
    assert len(parsed.rows) == 1
    assert float(parsed.rows[0].amount) == 100
    assert parsed.available_columns
    assert parsed.preview_rows


def test_csv_malformed_row_recorded_as_issue() -> None:
    text = "timestamp,amount,direction,asset_type,id\n2026-01-01T10:00:00,,in,fiat,bad\n2026-01-02T10:00:00,10,in,fiat,ok\n"
    svc = CsvIngestService()
    parsed = svc.parse_transactions(content=text.encode("utf-8"), mode="fiat", max_rows=100)
    assert len(parsed.rows) == 1
    assert len(parsed.issues) >= 1
    assert parsed.issues[0].raw_preview
    assert parsed.normalization_report.malformed_rows >= 1


def test_csv_utf16_decimal_comma_and_debit_credit_report() -> None:
    text = "date;sum;currency;debit_credit;asset_type;id\n14.05.2026 10:00;1.234,56;RUR;CR;fiat;d1\n"
    svc = CsvIngestService()
    parsed = svc.parse_transactions(content=text.encode("utf-16"), mode="fiat", max_rows=100)
    assert len(parsed.rows) == 1
    row = parsed.rows[0]
    assert str(row.amount) == "1234.56"
    assert row.currency == "RUB"
    assert row.direction == "in"
    assert parsed.normalization_report.decimal_comma_rows == 1
    assert parsed.normalization_report.debit_credit_normalized_rows == 1
    assert parsed.normalization_report.currency_alias_rows == 1


def test_csv_ingest_demo_structuring(client: TestClient) -> None:
    demo = Path(__file__).resolve().parents[1] / "demo" / "fiat_structuring.csv"
    assert demo.is_file()
    with demo.open("rb") as fh:
        files = {"file": ("fiat_structuring.csv", fh.read(), "text/csv")}
    r = client.post(
        "/api/v1/ingest/csv",
        data={"mode": "fiat", "jurisdiction": "EU", "focus_last_n": "6"},
        files=files,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["parsed_rows"] >= 1
    assert "normalized_request" in data
    assert "preview_rows" in data
    assert "available_columns" in data


def test_ingest_csv_mapping_override(client: TestClient) -> None:
    text = "booked_at,total,ccy,flow,kind,ref\n2026-01-01T10:00:00,10,EUR,in,fiat,a1\n"
    files = {"file": ("mapped.csv", text.encode("utf-8"), "text/csv")}
    r = client.post(
        "/api/v1/ingest/csv",
        data={
            "mode": "fiat",
            "jurisdiction": "BY",
            "column_overrides_json": '{"timestamp":"booked_at","amount":"total","currency":"ccy","direction":"flow","asset_type":"kind","id":"ref"}',
        },
        files=files,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["normalization_report"]["override_applied_fields"]
    assert data["normalized_request"]["focus_transactions"][0]["currency"] == "EUR"


def test_ingest_csv_payload_too_large(client: TestClient) -> None:
    max_b = client.app.state.settings.max_csv_bytes
    blob = b"x" * (max_b + 1)
    files = {"file": ("huge.csv", blob, "text/csv")}
    r = client.post("/api/v1/ingest/csv", data={"mode": "fiat", "jurisdiction": "BY"}, files=files)
    assert r.status_code == 413
