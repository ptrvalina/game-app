"""
Profiler: статистический профиль «нормы» клиента на pandas (без ML).
"""
from __future__ import annotations

from collections import Counter
from datetime import timezone
import math

import pandas as pd

from app.models.schemas import TransactionRecord


class Profiler:
    """Строит сводку по историческим транзакциям."""

    def build(self, txs: list[TransactionRecord]) -> dict:
        """
        Возвращает dict с числовыми полями и списками для AnomalyDetector и промптов.
        """
        if not txs:
            return {
                "window_transactions": 0,
                "activity_days": 0,
                "avg_amount": None,
                "median_amount": None,
                "p95_amount": None,
                "max_amount": None,
                "avg_daily_count": None,
                "rolling_7d_count": None,
                "rolling_30d_count": None,
                "top_counterparties": [],
                "counterparty_concentration": None,
                "burst_days": 0,
                "behavior_drift_score": 0,
                "timezone_basis": "naive_as_utc",
                "usual_hours_start": None,
                "usual_hours_end": None,
                "profile_notes": ["Недостаточно истории для профиля"],
            }

        rows: list[dict] = []
        any_tz_aware = False
        for t in txs:
            ts = self._normalize_ts(t.ts)
            any_tz_aware = any_tz_aware or bool(t.ts and t.ts.tzinfo)
            rows.append(
                {
                    "amount": float(t.amount),
                    "ts": ts,
                    "counterparty": (t.counterparty or "").strip() or None,
                    "hour": ts.hour if ts else None,
                    "date": ts.date().isoformat() if ts else None,
                }
            )
        df = pd.DataFrame(rows)
        notes: list[str] = []

        avg_amount = float(df["amount"].mean())
        median_amount = float(df["amount"].median())
        p95_amount = float(df["amount"].quantile(0.95))
        max_amount = float(df["amount"].max())

        avg_daily_count = None
        rolling_7d_count = None
        rolling_30d_count = None
        activity_days = 0
        burst_days = 0
        behavior_drift_score = 0
        if df["date"].notna().any():
            daily_counts = (
                df.groupby("date").size().rename("count").sort_index()
            )
            activity_days = int(len(daily_counts))
            day_index = pd.date_range(daily_counts.index.min(), daily_counts.index.max(), freq="D")
            dense = daily_counts.reindex(day_index.strftime("%Y-%m-%d"), fill_value=0)
            avg_daily_count = float(dense.mean())
            rolling_7d_count = float(dense.rolling(7, min_periods=1).sum().mean())
            rolling_30d_count = float(dense.rolling(30, min_periods=1).sum().mean())
            burst_threshold = max(avg_daily_count * 2.0, 6.0)
            burst_days = int((dense >= burst_threshold).sum())
            if burst_days:
                notes.append("В истории встречались burst-дни с аномально высокой активностью")

            split = max(1, len(df) // 2)
            early_mean = float(df["amount"].iloc[:split].mean())
            late_series = df["amount"].iloc[split:]
            late_mean = float(late_series.mean()) if len(late_series) else early_mean
            if early_mean > 0 and not math.isnan(late_mean):
                behavior_drift_score = int(min(100, abs(late_mean / early_mean - 1.0) * 100))
                if behavior_drift_score >= 35:
                    notes.append("За окно истории заметен дрейф поведенческого профиля")

        top_counterparties: list[str] = []
        counterparty_concentration = None
        cps = [c for c in df["counterparty"].dropna().tolist() if c]
        if cps:
            counter = Counter(cps)
            top_counterparties = [name for name, _ in counter.most_common(5)]
            counterparty_concentration = round(counter.most_common(1)[0][1] / len(cps), 4)
            if counterparty_concentration >= 0.6:
                notes.append("Высокая концентрация на одном контрагенте")

        usual_hours_start: int | None = None
        usual_hours_end: int | None = None
        if df["hour"].notna().any():
            h = df["hour"].dropna().astype(int)
            low, high = int(h.quantile(0.1)), int(h.quantile(0.9))
            usual_hours_start = max(0, min(low, 23))
            usual_hours_end = max(0, min(high, 23))

        return {
            "window_transactions": int(len(df)),
            "activity_days": activity_days,
            "avg_amount": avg_amount,
            "median_amount": median_amount,
            "p95_amount": p95_amount,
            "max_amount": max_amount,
            "avg_daily_count": avg_daily_count,
            "rolling_7d_count": rolling_7d_count,
            "rolling_30d_count": rolling_30d_count,
            "top_counterparties": top_counterparties,
            "counterparty_concentration": counterparty_concentration,
            "burst_days": burst_days,
            "behavior_drift_score": behavior_drift_score,
            "timezone_basis": "source_tz_normalized_utc" if any_tz_aware else "naive_as_utc",
            "usual_hours_start": usual_hours_start,
            "usual_hours_end": usual_hours_end,
            "profile_notes": notes,
        }

    @staticmethod
    def format_for_prompt(profile: dict) -> str:
        """Человекочитаемая сводка для LLM."""
        lines = [
            f"Оконно транзакций: {profile['window_transactions']}",
            f"Активных дней: {profile.get('activity_days')}",
            f"Средняя сумма: {profile.get('avg_amount')}",
            f"Медиана суммы: {profile.get('median_amount')}",
            f"p95 суммы: {profile.get('p95_amount')}",
            f"Максимум суммы: {profile.get('max_amount')}",
            f"Среднее число операций в день: {profile.get('avg_daily_count')}",
            f"Средний rolling 7d count: {profile.get('rolling_7d_count')}",
            f"Средний rolling 30d count: {profile.get('rolling_30d_count')}",
            f"Топ контрагентов: {', '.join(profile.get('top_counterparties') or [])}",
            f"Концентрация на топ-контрагенте: {profile.get('counterparty_concentration')}",
            f"Burst дней: {profile.get('burst_days')}",
            f"Behavior drift score: {profile.get('behavior_drift_score')}",
            f"Timezone basis: {profile.get('timezone_basis')}",
            f"Обычные часы (оценка p10–p90): {profile.get('usual_hours_start')}–{profile.get('usual_hours_end')}",
        ]
        for n in profile.get("profile_notes") or []:
            lines.append(f"Заметка профиля: {n}")
        return "\n".join(lines)

    @staticmethod
    def _normalize_ts(ts):
        if ts is None:
            return None
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
