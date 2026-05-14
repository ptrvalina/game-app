"""Deterministic CSV onboarding for bank, crypto, and generic AML exports."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app.core.redaction import redact_mapping, redact_pii_text
from app.models.schemas import (
    AnalyzeRequest,
    CsvIngestIssue,
    CsvPreviewRow,
    CsvNormalizationReport,
    CsvIngestResponse,
    CsvIngestSummary,
    Jurisdiction,
    Mode,
    TransactionRecord,
)


_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "transaction_id", "tx_id", "operation_id", "reference", "ref"),
    "amount": (
        "amount",
        "sum",
        "value",
        "transaction_amount",
        "gross_amount",
        "qty",
        "quantity",
        "amount_base",
        "notional",
    ),
    "timestamp": ("timestamp", "created_at", "date", "datetime", "time", "transaction_date", "executed_at"),
    "currency": ("currency", "ccy", "asset", "coin", "token", "quote_asset"),
    "counterparty": ("counterparty", "recipient", "sender", "beneficiary", "payee", "from", "to", "wallet", "address"),
    "direction": ("direction", "type", "flow", "side", "dr_cr", "debit_credit"),
    "channel": ("channel", "method", "payment_method", "rail", "network"),
    "geo": ("geo", "country", "region", "jurisdiction"),
    "mcc": ("mcc", "merchant_category_code"),
    "narrative": ("narrative", "description", "details", "note", "comment", "memo"),
    "asset_type": ("asset_type", "instrument_type", "class"),
}

_CRYPTO_CURRENCIES = {
    "BTC",
    "ETH",
    "USDT",
    "USDC",
    "BNB",
    "SOL",
    "TRX",
    "DOGE",
    "XRP",
    "ADA",
    "LTC",
    "DOT",
    "MATIC",
}
_CURRENCY_ALIASES = {"RUR": "RUB", "USDTTRC20": "USDT", "USDTERC20": "USDT", "XBT": "BTC"}
_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
)


@dataclass(slots=True)
class ParsedCsv:
    rows: list[TransactionRecord]
    issues: list[CsvIngestIssue]
    delimiter: str
    encoding: str
    total_rows: int
    normalization_report: CsvNormalizationReport
    available_columns: list[str]
    preview_rows: list[CsvPreviewRow]


class CsvIngestService:
    """Normalizes heterogeneous CSV exports into Amber AnalyzeRequest."""

    def ingest_bytes(
        self,
        *,
        content: bytes,
        filename: str | None,
        mode: Mode,
        jurisdiction: Jurisdiction,
        focus_last_n: int = 12,
        max_rows: int = 10_000,
        max_preview_rows: int = 12,
        max_malformed_ratio: float = 0.35,
        alert_id: str | None = None,
        client_id_external: str | None = None,
        column_overrides: dict[str, str] | None = None,
    ) -> CsvIngestResponse:
        parsed = self.parse_transactions(
            content=content,
            mode=mode,
            max_rows=max_rows,
            max_preview_rows=max_preview_rows,
            max_malformed_ratio=max_malformed_ratio,
            column_overrides=column_overrides,
        )
        txs = sorted(parsed.rows, key=lambda tx: (self._sortable_ts(tx.ts), tx.id or ""))
        if not txs:
            raise ValueError("CSV не содержит ни одной валидной операции")

        split = max(1, min(focus_last_n, len(txs)))
        historical = txs[:-split]
        focus = txs[-split:]
        request = AnalyzeRequest(
            mode=mode,
            jurisdiction=jurisdiction,
            alert_id=alert_id or self._default_alert_id(filename),
            client_id_external=client_id_external,
            historical_transactions=historical,
            focus_transactions=focus,
            extra_context={
                "amber_ingest": {
                    "source": "csv",
                    "rejected_rows": len(parsed.issues),
                    "rejected_ratio": parsed.normalization_report.rejected_ratio,
                }
            },
        )
        summary = CsvIngestSummary(
            filename=filename,
            delimiter=parsed.delimiter,
            encoding=parsed.encoding,
            total_rows=parsed.total_rows,
            parsed_rows=len(txs),
            rejected_rows=len(parsed.issues),
            focus_rows=len(focus),
            historical_rows=len(historical),
        )
        return CsvIngestResponse(
            mode=mode,
            jurisdiction=jurisdiction,
            normalized_request=request,
            summary=summary,
            issues=parsed.issues,
            normalization_report=parsed.normalization_report,
            available_columns=parsed.available_columns,
            preview_rows=parsed.preview_rows,
        )

    def parse_transactions(
        self,
        *,
        content: bytes,
        mode: Mode,
        max_rows: int = 10_000,
        max_preview_rows: int = 12,
        max_malformed_ratio: float = 0.35,
        column_overrides: dict[str, str] | None = None,
    ) -> ParsedCsv:
        text, encoding = self._decode_bytes(content)
        delimiter = self._detect_delimiter(text)
        stream = io.StringIO(text)
        reader = csv.DictReader(stream, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("CSV header не найден")

        normalized_headers = {name: self._normalize_header(name) for name in reader.fieldnames if name}
        available_columns = sorted(set(normalized_headers.values()))
        normalized_overrides = self._normalize_overrides(column_overrides, available_columns)
        column_mapping = self._resolve_column_mapping(set(normalized_headers.values()), normalized_overrides)
        rows: list[TransactionRecord] = []
        issues: list[CsvIngestIssue] = []
        preview_rows: list[CsvPreviewRow] = []
        total_rows = 0
        decimal_comma_rows = 0
        debit_credit_normalized_rows = 0
        currency_alias_rows = 0
        missing_timestamp_rows = 0
        for row_number, raw_row in enumerate(reader, start=2):
            total_rows += 1
            if total_rows > max_rows:
                raise ValueError(f"CSV превышает допустимый лимит строк ({max_rows})")
            try:
                row = {normalized_headers.get(key, self._normalize_header(key)): (value or "").strip() for key, value in raw_row.items()}
                record, row_stats = self._row_to_transaction(
                    row=row,
                    row_number=row_number,
                    mode=mode,
                    column_mapping=column_mapping,
                )
                rows.append(record)
                decimal_comma_rows += row_stats["decimal_comma"]
                debit_credit_normalized_rows += row_stats["debit_credit_normalized"]
                currency_alias_rows += row_stats["currency_alias"]
                missing_timestamp_rows += row_stats["missing_timestamp"]
                if len(preview_rows) < max_preview_rows:
                    preview_rows.append(
                        CsvPreviewRow(
                            row_number=row_number,
                            status="parsed",
                            values=self._preview_values(record),
                        )
                    )
            except ValueError as exc:
                issue = CsvIngestIssue(
                    row_number=row_number,
                    code="row_parse_error",
                    message=str(exc)[:256],
                    raw_preview=self._raw_preview(raw_row),
                )
                issues.append(issue)
                if len(preview_rows) < max_preview_rows:
                    preview_rows.append(
                        CsvPreviewRow(
                            row_number=row_number,
                            status="rejected",
                            values=redact_mapping({str(k): (v or "")[:64] for k, v in raw_row.items() if k}),
                            issue_code=issue.code,
                            issue_message=issue.message,
                        )
                    )
        rejected_ratio = (len(issues) / total_rows) if total_rows else 0.0
        malformed_threshold_exceeded = total_rows > 0 and rejected_ratio > max_malformed_ratio
        report = CsvNormalizationReport(
            encoding_used=encoding,
            delimiter_used=delimiter,
            column_mapping=column_mapping,
            decimal_comma_rows=decimal_comma_rows,
            debit_credit_normalized_rows=debit_credit_normalized_rows,
            currency_alias_rows=currency_alias_rows,
            missing_timestamp_rows=missing_timestamp_rows,
            malformed_rows=len(issues),
            rejected_ratio=round(rejected_ratio, 6),
            malformed_threshold_exceeded=malformed_threshold_exceeded,
            override_applied_fields=sorted(normalized_overrides.keys()),
        )
        if malformed_threshold_exceeded and not rows:
            raise ValueError("Слишком высокая доля malformed rows; проверьте mapping/формат CSV")
        return ParsedCsv(
            rows=rows,
            issues=issues,
            delimiter=delimiter,
            encoding=encoding,
            total_rows=total_rows,
            normalization_report=report,
            available_columns=available_columns,
            preview_rows=preview_rows,
        )

    def _row_to_transaction(
        self,
        *,
        row: dict[str, str],
        row_number: int,
        mode: Mode,
        column_mapping: dict[str, str],
    ) -> tuple[TransactionRecord, dict[str, int]]:
        amount_raw = self._value_for_alias(row, "amount", column_mapping)
        if not amount_raw:
            raise ValueError("Не найдена колонка/значение amount")
        amount, decimal_comma_used = self._parse_amount(amount_raw)

        direction_value, debit_credit_normalized = self._normalize_direction(
            self._value_for_alias(row, "direction", column_mapping),
            amount_raw,
        )
        currency, currency_alias_applied = self._normalize_currency(self._value_for_alias(row, "currency", column_mapping))
        asset_type = self._normalize_asset_type(
            explicit=self._value_for_alias(row, "asset_type", column_mapping),
            currency=currency,
            counterparty=self._value_for_alias(row, "counterparty", column_mapping),
            narrative=self._value_for_alias(row, "narrative", column_mapping),
            channel=self._value_for_alias(row, "channel", column_mapping),
            mode=mode,
        )
        ts = self._parse_timestamp(self._value_for_alias(row, "timestamp", column_mapping))

        return (
            TransactionRecord(
                id=self._value_for_alias(row, "id", column_mapping) or f"csv-{row_number}",
                ts=ts,
                amount=abs(amount),
                currency=currency,
                direction=direction_value,
                counterparty=self._value_for_alias(row, "counterparty", column_mapping) or None,
                channel=self._value_for_alias(row, "channel", column_mapping) or None,
                geo=self._value_for_alias(row, "geo", column_mapping) or None,
                mcc=self._value_for_alias(row, "mcc", column_mapping) or None,
                narrative=self._value_for_alias(row, "narrative", column_mapping) or None,
                asset_type=asset_type,
            ),
            {
                "decimal_comma": int(decimal_comma_used),
                "debit_credit_normalized": int(debit_credit_normalized),
                "currency_alias": int(currency_alias_applied),
                "missing_timestamp": int(ts is None),
            },
        )

    def _value_for_alias(self, row: dict[str, str], canonical: str, column_mapping: dict[str, str]) -> str:
        mapped = column_mapping.get(canonical)
        if mapped and mapped in row and row[mapped]:
            return row[mapped]
        aliases = _COLUMN_ALIASES.get(canonical, ())
        for alias in aliases:
            if alias in row and row[alias]:
                return row[alias]
        return ""

    def _decode_bytes(self, content: bytes) -> tuple[str, str]:
        last_error: Exception | None = None
        for encoding in ("utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be", "cp1251", "latin-1"):
            try:
                return content.decode(encoding), encoding
            except UnicodeDecodeError as exc:
                last_error = exc
        raise ValueError(f"Не удалось декодировать CSV: {last_error}")

    def _detect_delimiter(self, text: str) -> str:
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            return dialect.delimiter
        except csv.Error:
            scores = {delimiter: sample.count(delimiter) for delimiter in (",", ";", "\t", "|")}
            return max(scores, key=scores.get)

    def _normalize_header(self, value: str | None) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower())
        normalized = normalized.strip("_")
        return normalized

    def _resolve_column_mapping(self, headers: set[str], overrides: dict[str, str]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for canonical, aliases in _COLUMN_ALIASES.items():
            override = overrides.get(canonical)
            if override and override in headers:
                mapping[canonical] = override
                continue
            for alias in aliases:
                if alias in headers:
                    mapping[canonical] = alias
                    break
        return mapping

    def _parse_timestamp(self, raw: str) -> datetime | None:
        value = (raw or "").strip()
        if not value:
            return None
        normalized = value.replace("Z", "+00:00")
        try:
            ts = datetime.fromisoformat(normalized)
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        for pattern in _TIMESTAMP_FORMATS:
            try:
                ts = datetime.strptime(value, pattern)
                return ts.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        raise ValueError(f"Некорректный timestamp: {value}")

    def _parse_amount(self, raw: str) -> tuple[Decimal, bool]:
        value = re.sub(r"[^\d,.\-()]", "", raw or "").strip()
        if not value:
            raise ValueError("Пустая сумма")
        negative = value.startswith("-") or (value.startswith("(") and value.endswith(")"))
        value = value.strip("()-")
        decimal_comma_used = False
        if "," in value and "." in value:
            if value.rfind(",") > value.rfind("."):
                value = value.replace(".", "").replace(",", ".")
                decimal_comma_used = True
            else:
                value = value.replace(",", "")
        elif value.count(",") == 1 and value.count(".") == 0:
            value = value.replace(",", ".")
            decimal_comma_used = True
        elif value.count(".") > 1:
            left, right = value.rsplit(".", 1)
            value = left.replace(".", "") + "." + right
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"Некорректная сумма: {raw}") from exc
        normalized = -amount if negative else amount
        return normalized, decimal_comma_used

    def _normalize_currency(self, raw: str) -> tuple[str | None, bool]:
        value = re.sub(r"[^A-Za-z0-9]", "", (raw or "").upper())
        if not value:
            return None, False
        normalized = _CURRENCY_ALIASES.get(value, value[:12])
        return normalized, normalized != value

    def _normalize_direction(self, raw: str, amount_raw: str) -> tuple[str, bool]:
        value = (raw or "").strip().lower()
        if value in {"in", "incoming", "credit", "cr", "c", "deposit", "buy", "received", "receive"}:
            return "in", value not in {"in", "incoming"}
        if value in {"out", "outgoing", "debit", "dr", "d", "withdrawal", "sell", "sent", "send"}:
            return "out", value not in {"out", "outgoing"}
        if amount_raw.strip().startswith("-") or amount_raw.strip().startswith("("):
            return "out", False
        return "unknown", False

    def _normalize_asset_type(
        self,
        *,
        explicit: str,
        currency: str | None,
        counterparty: str,
        narrative: str,
        channel: str,
        mode: Mode,
    ) -> str:
        hint = " ".join(part.lower() for part in (explicit, counterparty, narrative, channel) if part)
        if currency in _CRYPTO_CURRENCIES or any(token in hint for token in ("wallet", "address", "exchange", "binance", "bybit", "coinbase", "bridge", "dex")):
            return "crypto"
        if explicit.strip().lower() in {"fiat", "bank"}:
            return "fiat"
        if mode == "crypto":
            return "crypto"
        if mode == "fiat":
            return "fiat"
        return "unknown"

    def _sortable_ts(self, ts: datetime | None) -> tuple[int, datetime]:
        if ts is None:
            return (1, datetime.max.replace(tzinfo=timezone.utc))
        return (0, ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc))

    def _default_alert_id(self, filename: str | None) -> str:
        stem = re.sub(r"[^A-Za-z0-9]+", "-", filename or "csv").strip("-") or "csv"
        return f"CSV-{stem[:48]}"

    def _raw_preview(self, raw_row: dict[str, str | None]) -> str:
        pairs: list[str] = []
        for key, value in list(raw_row.items())[:6]:
            safe_key = (key or "").strip()[:32]
            safe_value = redact_pii_text((value or "").replace("\n", " ").replace("\r", " ").strip()[:24]) or ""
            pairs.append(f"{safe_key}={safe_value}")
        return " | ".join(pairs)[:256]

    def _normalize_overrides(self, overrides: dict[str, str] | None, available_columns: list[str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        if not overrides:
            return normalized
        allowed = set(available_columns)
        for canonical, selected in overrides.items():
            key = self._normalize_header(canonical)
            value = self._normalize_header(selected)
            if key in _COLUMN_ALIASES and value in allowed:
                normalized[key] = value
        return normalized

    def _preview_values(self, record: TransactionRecord) -> dict[str, str | None]:
        return redact_mapping(
            {
                "id": record.id,
                "ts": record.ts.isoformat() if record.ts else None,
                "amount": str(record.amount),
                "currency": record.currency,
                "direction": record.direction,
                "counterparty": record.counterparty,
                "channel": record.channel,
                "narrative": record.narrative,
                "asset_type": record.asset_type,
            }
        )
