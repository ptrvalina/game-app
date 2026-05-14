"""SAR / memo formatting (deterministic render path)."""
from __future__ import annotations

from app.core.config import get_settings
from app.models.schemas import ReporterLLMResult
from app.xai.engine import XAIEngine


def test_sar_memo_sections() -> None:
    eng = XAIEngine(get_settings())
    rep = ReporterLLMResult(
        sar_title="Memo",
        executive_summary="Client shows atypical velocity versus profile.",
        observed_behavior=["3 high-value credits in 48h."],
        anomaly_evidence=["velocity_daily | contribution=20"],
        regulatory_context=["Internal AML policy — tier-2 review."],
        recommended_actions=["Request KYC refresh.", "Requires analyst verification."],
    )
    body = eng._render_sar_body(rep)
    assert "INTERNAL COMPLIANCE MEMO" in body
    assert "Executive Summary" in body
    assert "Deterministic Anomaly Evidence" in body
    assert "HUMAN_REVIEW_REQUIRED" in body
