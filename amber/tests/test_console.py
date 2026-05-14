"""Static console / demo assets for pilot UI."""
from __future__ import annotations

from pathlib import Path


def test_console_html_has_pilot_controls() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    assert "csvFile" in html
    assert "evidenceTable" in html
    assert "traceContainer" in html
    assert "btnCopySar" in html
    assert "btnExportBundle" in html
    assert "btnReplayBundle" in html
    assert "reviewStatus" in html
    assert "btnCsvPreview" in html
    assert "btnAnalyzePreview" in html
    assert "demoLibrary" in html
    assert "txDrilldown" in html
    assert "btnExportSarDocx" in html
    assert "caseTitle" in html
    assert "caseReliability" in html
    assert "replaySummary" in html
    assert "telemetryPanel" in html
    assert "btnEvidenceCsv" in html
    assert "evidenceFilter" in html
    assert "evidenceGroup" in html


def test_demo_datasets_exist() -> None:
    demo = Path(__file__).resolve().parents[1] / "demo"
    for name in (
        "fiat_normal.csv",
        "fiat_structuring.csv",
        "crypto_layering.csv",
        "cross_border_case.csv",
        "dormant_reactivation.csv",
        "salary_mismatch.csv",
        "exchange_hopping.csv",
    ):
        assert (demo / name).is_file(), name
