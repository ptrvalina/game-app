"""Deterministic case artifact export without server-side persistence."""
from __future__ import annotations

import hmac
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from app.core.config import Settings
from app.models.schemas import AnalyzeRequest, AnalyzeResponse, CaseExportArtifact, CaseExportFormat, SarExportFormat


class CaseExportService:
    """Builds portable pilot-review artifacts from one request/response pair."""

    _BUNDLE_ORDER = (
        "normalized_request.json",
        "deterministic_evidence.json",
        "anomaly.json",
        "traces.json",
        "reporter.json",
        "sar.txt",
        "workflow.json",
        "audit_log.json",
        "audit_manifest.json",
    )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def export(
        self,
        *,
        source_request: AnalyzeRequest,
        analysis: AnalyzeResponse,
        format: CaseExportFormat,
    ) -> CaseExportArtifact:
        if format == "markdown":
            content = self._to_markdown(source_request=source_request, analysis=analysis)
            media_type = "text/markdown; charset=utf-8"
            suffix = "md"
        elif format == "audit_bundle":
            content = self._to_audit_bundle(source_request=source_request, analysis=analysis)
            media_type = "application/json"
            suffix = "bundle.json"
        else:
            content = self._to_json(source_request=source_request, analysis=analysis)
            media_type = "application/json"
            suffix = "json"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        filename = f"{self._case_stem(source_request, analysis)}.{suffix}"
        return CaseExportArtifact(
            format=format,
            filename=filename,
            media_type=media_type,
            content=content,
            sha256=digest,
        )

    def export_sar_bytes(
        self,
        *,
        source_request: AnalyzeRequest,
        analysis: AnalyzeResponse,
        format: SarExportFormat,
    ) -> tuple[bytes, str, str]:
        stem = self._case_stem(source_request, analysis)
        if format == "markdown":
            text = self._sar_markdown(source_request=source_request, analysis=analysis)
            payload = text.encode("utf-8")
            media_type = "text/markdown; charset=utf-8"
            filename = f"{stem}.md"
        elif format == "docx":
            payload = self._docx_bytes(self._sar_markdown(source_request=source_request, analysis=analysis))
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            filename = f"{stem}.docx"
        else:
            text = self._sar_text(source_request=source_request, analysis=analysis)
            payload = text.encode("utf-8")
            media_type = "text/plain; charset=utf-8"
            filename = f"{stem}.txt"
        return payload, filename, media_type

    def export_zip_bytes(
        self,
        *,
        source_request: AnalyzeRequest,
        analysis: AnalyzeResponse,
    ) -> tuple[bytes, str, str]:
        files = self._bundle_files(source_request=source_request, analysis=analysis)
        output = io.BytesIO()
        with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in self._BUNDLE_ORDER:
                content = files[name]
                info = zipfile.ZipInfo(filename=name, date_time=(2024, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
        payload = output.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        return payload, f"{self._case_stem(source_request, analysis)}.zip", digest

    def _to_json(self, *, source_request: AnalyzeRequest, analysis: AnalyzeResponse) -> str:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "request": source_request.model_dump(mode="json"),
            "analysis": analysis.model_dump(mode="json"),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _to_audit_bundle(self, *, source_request: AnalyzeRequest, analysis: AnalyzeResponse) -> str:
        payload = {
            "bundle_version": "pilot-audit-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "request": source_request.model_dump(mode="json"),
            "anomaly_evidence": [item.model_dump(mode="json") for item in analysis.anomaly.evidence],
            "sar": analysis.reporter.model_dump(mode="json"),
            "stage_traces": [item.model_dump(mode="json") for item in analysis.meta.stage_traces],
            "provider_metadata": {
                "llm_primary": analysis.meta.llm_primary,
                "llm_used": analysis.meta.llm_used,
                "fallback_used": analysis.meta.fallback_used,
                "degraded_mode": analysis.meta.degraded_mode,
                "emergency_mode": analysis.meta.emergency_mode,
            },
            "confidence_validation": analysis.meta.confidence_validation.model_dump(mode="json")
            if analysis.meta.confidence_validation
            else None,
            "validator_summary": analysis.meta.validator_summary.model_dump(mode="json")
            if analysis.meta.validator_summary
            else None,
            "scoring_provenance": analysis.meta.scoring_provenance.model_dump(mode="json")
            if analysis.meta.scoring_provenance
            else None,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _bundle_files(
        self,
        *,
        source_request: AnalyzeRequest,
        analysis: AnalyzeResponse,
    ) -> dict[str, str]:
        request_json = self._json_text(source_request.model_dump(mode="json"))
        evidence_json = self._json_text([item.model_dump(mode="json") for item in analysis.anomaly.evidence])
        anomaly_json = self._json_text(analysis.anomaly.model_dump(mode="json"))
        traces_json = self._json_text([item.model_dump(mode="json") for item in analysis.meta.stage_traces])
        reporter_json = self._json_text(analysis.reporter.model_dump(mode="json"))
        sar_text = analysis.reporter.sar_body
        workflow_json = self._json_text(
            analysis.meta.workflow.model_dump(mode="json") if analysis.meta.workflow else {}
        )
        audit_json = self._json_text([item.model_dump(mode="json") for item in analysis.meta.audit_events])
        files = {
            "normalized_request.json": request_json,
            "deterministic_evidence.json": evidence_json,
            "anomaly.json": anomaly_json,
            "traces.json": traces_json,
            "reporter.json": reporter_json,
            "sar.txt": self._sar_text(source_request=source_request, analysis=analysis),
            "workflow.json": workflow_json,
            "audit_log.json": audit_json,
        }
        file_hashes = {name: self._sha256(content) for name, content in files.items()}
        manifest = {
            "bundle_version": "amber-case-bundle-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "request_id": analysis.meta.request_id,
            "case_id": self._case_stem(source_request, analysis),
            "review": {
                "review_status": analysis.meta.review_status,
                "review_notes": analysis.meta.review_notes,
                "reviewed_by": analysis.meta.reviewed_by,
                "reviewed_at": analysis.meta.reviewed_at.isoformat() if analysis.meta.reviewed_at else None,
            },
            "file_hashes": file_hashes,
            "deterministic_hashes": {
                "normalized_request": file_hashes["normalized_request.json"],
                "anomaly": file_hashes["anomaly.json"],
                "deterministic_evidence": file_hashes["deterministic_evidence.json"],
                "profiler": self._sha256(self._json_text(analysis.profiler.model_dump(mode="json"))),
                "scoring_provenance": self._sha256(
                    self._json_text(
                        analysis.meta.scoring_provenance.model_dump(mode="json")
                        if analysis.meta.scoring_provenance
                        else {}
                    )
                ),
            },
            "provider_metadata": {
                "llm_primary": analysis.meta.llm_primary,
                "llm_used": analysis.meta.llm_used,
                "fallback_used": analysis.meta.fallback_used,
                "degraded_mode": analysis.meta.degraded_mode,
                "emergency_mode": analysis.meta.emergency_mode,
                "applicable_norms": analysis.router.applicable_norms,
            },
            "router": analysis.router.model_dump(mode="json"),
            "analyst": analysis.analyst.model_dump(mode="json"),
            "confidence_validation": (
                analysis.meta.confidence_validation.model_dump(mode="json")
                if analysis.meta.confidence_validation
                else None
            ),
            "validator_summary": (
                analysis.meta.validator_summary.model_dump(mode="json")
                if analysis.meta.validator_summary
                else None
            ),
        }
        manifest["signature"] = {
            "algorithm": "hmac-sha256",
            "signed_files": list(self._BUNDLE_ORDER[:-1]),
            "signature": self._sign_manifest_payload(manifest),
        }
        files["audit_manifest.json"] = self._json_text(manifest)
        return files

    def _to_markdown(self, *, source_request: AnalyzeRequest, analysis: AnalyzeResponse) -> str:
        evidence_lines = [
            (
                f"- `{item.category}` / `{item.code}`: {item.label} "
                f"(observed={item.observed_value}, baseline={item.baseline_value}, threshold={item.threshold_value}, contribution={item.contribution})"
            )
            for item in analysis.anomaly.evidence
        ] or ["- No deterministic evidence captured."]
        trace_lines = [
            (
                f"- `{trace.stage}` status={trace.status} provider={trace.provider} "
                f"model={trace.model or 'n/a'} latency_ms={trace.latency_ms or 0} "
                f"validator={trace.validator_status} issues={trace.issues_count}"
            )
            for trace in analysis.meta.stage_traces
        ]
        return "\n".join(
            [
                f"# Amber Case Artifact — {self._case_stem(source_request, analysis)}",
                "",
                "## Review Status",
                f"- human_review_required: `{analysis.meta.human_review_required}`",
                f"- review_notice: {analysis.meta.review_notice}",
                "",
                "## Request Context",
                f"- mode: `{analysis.mode}`",
                f"- jurisdiction: `{analysis.jurisdiction}`",
                f"- alert_id: `{analysis.alert_id or 'n/a'}`",
                f"- client_id_external: `{analysis.client_id_external or 'n/a'}`",
                "",
                "## Executive Summary",
                analysis.reporter.executive_summary or analysis.analyst.risk_summary,
                "",
                "## Observed Behavior",
                *[f"- {item}" for item in analysis.reporter.observed_behavior],
                "",
                "## Deterministic Evidence",
                *evidence_lines,
                "",
                "## Regulatory Context",
                *[f"- {item}" for item in analysis.reporter.regulatory_context],
                "",
                "## Recommended Actions",
                *[f"- {item}" for item in analysis.reporter.recommended_actions],
                "",
                "## Stage Traces",
                *trace_lines,
                "",
                "## Disclaimer",
                analysis.reporter.sar_disclaimer,
            ]
        )

    def verify_manifest_signature(self, manifest: dict) -> bool:
        signature_block = manifest.get("signature") or {}
        expected = signature_block.get("signature")
        if not expected:
            return False
        return hmac.compare_digest(expected, self._sign_manifest_payload(manifest))

    def _case_stem(self, source_request: AnalyzeRequest, analysis: AnalyzeResponse) -> str:
        base = source_request.alert_id or analysis.alert_id or analysis.meta.request_id or "amber-case"
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in base)
        return safe[:72].strip("-") or "amber-case"

    def _json_text(self, value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)

    def _sha256(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _sign_manifest_payload(self, manifest: dict) -> str:
        payload = dict(manifest)
        payload.pop("signature", None)
        canonical = self._json_text(payload)
        return hmac.new(
            self._settings.bundle_signing_secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _sar_text(self, *, source_request: AnalyzeRequest, analysis: AnalyzeResponse) -> str:
        return "\n".join(
            [
                "AMBER SUPERVISED SAR MEMO",
                "",
                "Deterministic Evidence",
                *[
                    f"- [{item.code}] {item.label} | threshold={item.threshold_value} | observed={item.observed_value} | baseline={item.baseline_value}"
                    for item in analysis.anomaly.evidence
                ],
                "",
                "AI Narrative (Policy-Validated)",
                analysis.reporter.sar_body,
                "",
                "Analyst Notes",
                analysis.meta.review_notes or "No analyst notes recorded.",
                "",
                "Mandatory Human Review Disclaimer",
                analysis.meta.review_notice,
                "",
                f"Review Status: {analysis.meta.review_status}",
                f"Reviewed By: {analysis.meta.reviewed_by or 'n/a'}",
                f"Reviewed At: {analysis.meta.reviewed_at.isoformat() if analysis.meta.reviewed_at else 'n/a'}",
                f"Alert ID: {source_request.alert_id or analysis.alert_id or 'n/a'}",
            ]
        )

    def _sar_markdown(self, *, source_request: AnalyzeRequest, analysis: AnalyzeResponse) -> str:
        return "\n".join(
            [
                f"# Amber SAR Memo — {self._case_stem(source_request, analysis)}",
                "",
                "## Deterministic Evidence",
                *[
                    f"- `[{item.code}]` {item.label} | threshold=`{item.threshold_value}` | observed=`{item.observed_value}` | baseline=`{item.baseline_value}`"
                    for item in analysis.anomaly.evidence
                ],
                "",
                "## AI Narrative (Policy-Validated)",
                analysis.reporter.sar_body,
                "",
                "## Analyst Notes",
                analysis.meta.review_notes or "No analyst notes recorded.",
                "",
                "## Mandatory Human Review Disclaimer",
                analysis.meta.review_notice,
                "",
                "## Review Metadata",
                f"- review_status: `{analysis.meta.review_status}`",
                f"- reviewed_by: `{analysis.meta.reviewed_by or 'n/a'}`",
                f"- reviewed_at: `{analysis.meta.reviewed_at.isoformat() if analysis.meta.reviewed_at else 'n/a'}`",
            ]
        )

    def _docx_bytes(self, markdown_text: str) -> bytes:
        paragraphs = []
        for line in markdown_text.splitlines():
            text = escape(line if line else " ")
            paragraphs.append(f"<w:p><w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>")
        document_xml = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<w:document xmlns:wpc=\"http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas\" "
            "xmlns:mc=\"http://schemas.openxmlformats.org/markup-compatibility/2006\" "
            "xmlns:o=\"urn:schemas-microsoft-com:office:office\" "
            "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" "
            "xmlns:m=\"http://schemas.openxmlformats.org/officeDocument/2006/math\" "
            "xmlns:v=\"urn:schemas-microsoft-com:vml\" "
            "xmlns:wp14=\"http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing\" "
            "xmlns:wp=\"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing\" "
            "xmlns:w10=\"urn:schemas-microsoft-com:office:word\" "
            "xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" "
            "xmlns:w14=\"http://schemas.microsoft.com/office/word/2010/wordml\" "
            "xmlns:w15=\"http://schemas.microsoft.com/office/word/2012/wordml\" "
            "xmlns:wpg=\"http://schemas.microsoft.com/office/word/2010/wordprocessingGroup\" "
            "xmlns:wpi=\"http://schemas.microsoft.com/office/word/2010/wordprocessingInk\" "
            "xmlns:wne=\"http://schemas.microsoft.com/office/word/2006/wordml\" "
            "xmlns:wps=\"http://schemas.microsoft.com/office/word/2010/wordprocessingShape\" "
            "mc:Ignorable=\"w14 w15 wp14\">"
            f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>"
        )
        output = io.BytesIO()
        with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "[Content_Types].xml",
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
                "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
                "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
                "<Override PartName=\"/word/document.xml\" "
                "ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
                "</Types>",
            )
            archive.writestr(
                "_rels/.rels",
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
                "<Relationship Id=\"rId1\" "
                "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" "
                "Target=\"word/document.xml\"/>"
                "</Relationships>",
            )
            archive.writestr("word/document.xml", document_xml)
        return output.getvalue()
