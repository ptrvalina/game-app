"""XLSX ingest via deterministic CSV normalization bridge."""
from __future__ import annotations

import io

import pandas as pd

from app.core.config import Settings
from app.models.operations import XlsxSheetPreview
from app.models.schemas import CsvIngestResponse, Jurisdiction, Mode, XlsxIngestResponse
from app.services.connector_governance import build_provenance
from app.services.csv_ingest import CsvIngestService


class XlsxIngestService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._csv = CsvIngestService()

    def ingest_bytes(
        self,
        *,
        content: bytes,
        filename: str | None,
        mode: Mode,
        jurisdiction: Jurisdiction,
        focus_last_n: int,
        sheet_name: str | None = None,
        column_overrides: dict[str, str] | None = None,
        imported_by: str | None = None,
    ) -> XlsxIngestResponse:
        book = pd.ExcelFile(io.BytesIO(content))
        sheets: list[XlsxSheetPreview] = []
        for name in book.sheet_names:
            frame = book.parse(name, nrows=min(self._settings.max_csv_rows, 5000))
            sheets.append(
                XlsxSheetPreview(
                    sheet_name=name,
                    row_count=int(frame.shape[0]),
                    columns=[str(col) for col in frame.columns][:64],
                )
            )
        active = sheet_name if sheet_name in book.sheet_names else book.sheet_names[0]
        frame = book.parse(active)
        csv_bytes = frame.to_csv(index=False).encode("utf-8")
        csv_result = self._csv.ingest_bytes(
            content=csv_bytes,
            filename=filename or "upload.xlsx",
            mode=mode,
            jurisdiction=jurisdiction,
            focus_last_n=focus_last_n,
            max_rows=self._settings.max_csv_rows,
            max_preview_rows=self._settings.max_csv_preview_rows,
            max_malformed_ratio=self._settings.max_malformed_ratio,
            column_overrides=column_overrides,
        )
        provenance = build_provenance(
            source_type="xlsx",
            connector_name="ingest.xlsx",
            imported_by=imported_by,
            normalization_report=(
                csv_result.normalization_report.model_dump(mode="json") if csv_result.normalization_report else None
            ),
            malformed_ratio=csv_result.normalization_report.rejected_ratio if csv_result.normalization_report else 0.0,
            fingerprint_payload={"sheet": active, "sheets": book.sheet_names},
        )
        return _to_xlsx_response(csv_result, sheets=sheets, active_sheet=active, provenance=provenance)


def _to_xlsx_response(csv_result: CsvIngestResponse, *, sheets, active_sheet, provenance):
    extra = dict(csv_result.normalized_request.extra_context or {})
    extra["connector_provenance"] = provenance.model_dump(mode="json")
    normalized = csv_result.normalized_request.model_copy(update={"extra_context": extra})
    return XlsxIngestResponse(
        mode=csv_result.mode,
        jurisdiction=csv_result.jurisdiction,
        normalized_request=normalized,
        summary=csv_result.summary,
        issues=csv_result.issues,
        normalization_report=csv_result.normalization_report,
        available_columns=csv_result.available_columns,
        preview_rows=csv_result.preview_rows,
        sheets=sheets,
        active_sheet=active_sheet,
        connector_provenance=provenance,
    )
