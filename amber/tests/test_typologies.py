"""Deterministic typology signals (no LLM, no network)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.schemas import TransactionRecord
from app.xai.anomaly_detector import AnomalyDetector


def _tx(
    tid: str,
    ts: datetime,
    *,
    amount: float,
    direction: str,
    asset: str = "fiat",
    cp: str | None = None,
    channel: str | None = None,
    narrative: str | None = None,
) -> TransactionRecord:
    return TransactionRecord(
        id=tid,
        ts=ts,
        amount=Decimal(str(amount)),
        currency="EUR",
        direction=direction,
        counterparty=cp,
        channel=channel,
        asset_type=asset,
        narrative=narrative,
    )


def test_structuring_signal() -> None:
    base = datetime(2026, 5, 10, 10, 0, 0, tzinfo=timezone.utc)
    historical = [
        _tx("h1", base - timedelta(days=30), amount=3000, direction="in", cp="Pool"),
        _tx("h2", base - timedelta(days=25), amount=3100, direction="in", cp="Pool"),
        _tx("h3", base - timedelta(days=20), amount=2950, direction="in", cp="Pool"),
    ]
    focus = [
        _tx("f1", base, amount=2485, direction="in", cp="Importer"),
        _tx("f2", base + timedelta(minutes=20), amount=2490, direction="in", cp="Importer"),
        _tx("f3", base + timedelta(minutes=40), amount=2475, direction="in", cp="Importer"),
        _tx("f4", base + timedelta(hours=1), amount=2510, direction="in", cp="Importer"),
    ]
    profile = {"median_amount": 2000.0, "p95_amount": 3200.0, "max_amount": 3500.0, "avg_daily_count": 1.0, "activity_days": 20}
    det = AnomalyDetector()
    out = det.score(mode="fiat", profile=profile, historical=historical, focus=focus, declared_monthly_income=None)
    cats = set(out["categories"])
    assert "structuring" in cats


def test_dormant_activation_signal() -> None:
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    new = datetime(2026, 6, 1, tzinfo=timezone.utc)
    historical = [_tx("h1", old, amount=100, direction="in", cp="A")]
    focus = [
        _tx("f1", new, amount=200, direction="in", cp="B"),
        _tx("f2", new + timedelta(hours=1), amount=210, direction="in", cp="B"),
    ]
    profile = {"median_amount": 150.0, "p95_amount": 200.0, "max_amount": 250.0, "avg_daily_count": 0.2, "activity_days": 2}
    out = AnomalyDetector().score(mode="fiat", profile=profile, historical=historical, focus=focus, declared_monthly_income=None)
    assert "dormant_activation" in out["categories"]


def test_cross_cash_to_crypto_signal() -> None:
    base = datetime(2026, 5, 10, 8, 0, 0, tzinfo=timezone.utc)
    historical = []
    focus = [
        _tx("c1", base, amount=8000, direction="in", asset="fiat", channel="cash", cp="Branch"),
        _tx("c2", base + timedelta(hours=2), amount=0.5, direction="out", asset="crypto", cp="wallet-1"),
    ]
    profile = {"median_amount": 1000.0, "p95_amount": 5000.0, "max_amount": 8000.0, "avg_daily_count": 1.0, "activity_days": 1}
    out = AnomalyDetector().score(mode="cross", profile=profile, historical=historical, focus=focus, declared_monthly_income=None)
    assert "cash_to_crypto_outflow" in out["categories"]


def test_crypto_exchange_hopping_signal() -> None:
    base = datetime(2026, 5, 1, 8, 0, 0, tzinfo=timezone.utc)
    historical: list[TransactionRecord] = []
    focus: list[TransactionRecord] = []
    for i, name in enumerate(["binance-a", "kraken-b", "okx-c", "coinbase-d", "bybit-e", "htx-f"]):
        focus.append(
            _tx(
                f"x{i}",
                base + timedelta(hours=i),
                amount=0.05,
                direction="out",
                asset="crypto",
                cp=name,
                narrative="transfer",
            )
        )
    profile = {"median_amount": 0.1, "p95_amount": 0.2, "max_amount": 1.0, "avg_daily_count": 2.0, "activity_days": 3}
    out = AnomalyDetector().score(mode="crypto", profile=profile, historical=historical, focus=focus, declared_monthly_income=None)
    assert "exchange_hopping" in out["categories"]


def test_funnel_account_behavior_signal() -> None:
    base = datetime(2026, 5, 12, 8, 0, 0, tzinfo=timezone.utc)
    focus = [
        _tx("i1", base, amount=800, direction="in", cp="src-1"),
        _tx("i2", base + timedelta(minutes=10), amount=850, direction="in", cp="src-2"),
        _tx("i3", base + timedelta(minutes=20), amount=830, direction="in", cp="src-3"),
        _tx("i4", base + timedelta(minutes=30), amount=820, direction="in", cp="src-4"),
        _tx("o1", base + timedelta(hours=1), amount=3000, direction="out", cp="sink-1"),
    ]
    profile = {"median_amount": 600.0, "p95_amount": 900.0, "max_amount": 1000.0, "avg_daily_count": 1.0, "activity_days": 10}
    out = AnomalyDetector().score(mode="fiat", profile=profile, historical=[], focus=focus, declared_monthly_income=None)
    assert "funnel_account_behavior" in out["categories"]
