"""
AnomalyDetector: anomaly_score 0–100, причины, гипотеза новой схемы (эвристики + pandas-профиль).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from app.models.schemas import AnomalyCategory, Mode, TransactionRecord


class AnomalyDetector:
    """Сравнивает focus-транзакции с профилем нормы."""

    def score(
        self,
        *,
        mode: Mode,
        profile: dict,
        historical: list[TransactionRecord],
        focus: list[TransactionRecord],
        declared_monthly_income: Decimal | None,
    ) -> dict:
        if not focus:
            return {
                "anomaly_score": 0,
                "confidence_score": 0,
                "categories": [],
                "reasons": ["Нет focus-транзакций"],
                "evidence": [],
                "new_pattern_hypothesis": None,
            }

        evidence: list[dict] = []
        categories: list[AnomalyCategory] = []

        max_focus = max(float(t.amount) for t in focus)
        max_hist = profile.get("max_amount")
        p95_hist = profile.get("p95_amount")
        median_hist = profile.get("median_amount")

        if max_hist and max_focus > max_hist * 1.5:
            evidence.append(
                self._evidence(
                    code="amount_vs_max",
                    label=f"Сумма focus выше исторического максимума примерно на {int((max_focus / max_hist - 1) * 100)}%",
                    category="amount_spike",
                    observed=max_focus,
                    baseline=max_hist,
                    threshold=round(max_hist * 1.5, 2),
                    contribution=24,
                    tx_refs=self._tx_refs(focus),
                )
            )
        elif p95_hist and max_focus > p95_hist * 1.8:
            evidence.append(
                self._evidence(
                    code="amount_vs_p95",
                    label="Сумма существенно выше p95 исторического распределения",
                    category="amount_spike",
                    observed=max_focus,
                    baseline=p95_hist,
                    threshold=round(p95_hist * 1.8, 2),
                    contribution=18,
                    tx_refs=self._tx_refs(focus),
                )
            )

        day_counts = self._count_by_day(historical + focus)
        today_max = max(day_counts.values()) if day_counts else 0
        avg_daily = profile.get("avg_daily_count")
        if avg_daily and today_max > max(4.0, avg_daily * 1.8):
            evidence.append(
                self._evidence(
                    code="velocity_daily",
                    label=f"Пиковая активность: до {today_max} операций в день при исторической норме ~{avg_daily:.1f}",
                    category="velocity_spike",
                    observed=today_max,
                    baseline=round(avg_daily, 2),
                    threshold=max(4.0, round(avg_daily * 1.8, 2)),
                    contribution=20,
                    tx_refs=self._tx_refs(focus),
                )
            )

        hist_cp = {
            (t.counterparty or "").strip()
            for t in historical
            if t.counterparty and (t.counterparty or "").strip()
        }
        new_cp = sorted(
            {
                (t.counterparty or "").strip()
                for t in focus
                if t.counterparty and (t.counterparty or "").strip() and (t.counterparty or "").strip() not in hist_cp
            }
        )
        if new_cp:
            evidence.append(
                self._evidence(
                    code="new_counterparty",
                    label=f"Новый контрагент относительно истории: {', '.join(new_cp[:3])}",
                    category="new_counterparty",
                    observed=", ".join(new_cp[:3]),
                    baseline="исторически не наблюдался",
                    threshold=None,
                    contribution=14,
                    tx_refs=self._tx_refs([t for t in focus if (t.counterparty or '').strip() in new_cp]),
                )
            )

        unusual_hours = self._unusual_hours(focus, profile.get("usual_hours_start"), profile.get("usual_hours_end"))
        if unusual_hours:
            evidence.append(
                self._evidence(
                    code="off_hours",
                    label=f"Операции в нетипичное время: {', '.join(unusual_hours[:3])}",
                    category="off_hours",
                    observed=", ".join(unusual_hours[:3]),
                    baseline=f"{profile.get('usual_hours_start')}–{profile.get('usual_hours_end')}",
                    threshold=None,
                    contribution=10,
                    tx_refs=self._tx_refs(focus),
                )
            )

        if declared_monthly_income and declared_monthly_income > 0:
            rolling = float(sum(t.amount for t in focus if t.direction in ("in", "unknown")))
            if rolling > declared_monthly_income * 5:
                income = float(declared_monthly_income)
                evidence.append(
                    self._evidence(
                        code="income_mismatch",
                        label=(
                            "Оборот/внесения по focus существенно выше задекларированного дохода "
                            f"({rolling:.0f} vs {income:.0f}/мес)"
                        ),
                        category="income_mismatch",
                        observed=round(rolling, 2),
                        baseline=round(income, 2),
                        threshold=round(income * 5, 2),
                        contribution=18,
                        tx_refs=self._tx_refs([t for t in focus if t.direction in ("in", "unknown")]),
                    )
                )

        burst_days = profile.get("burst_days") or 0
        if burst_days >= 1 and len(focus) >= 3:
            evidence.append(
                self._evidence(
                    code="burst_pattern",
                    label="Профиль уже знает burst-поведение, а focus содержит новую волну высокой активности",
                    category="burst_activity",
                    observed=len(focus),
                    baseline=burst_days,
                    threshold=None,
                    contribution=8,
                    tx_refs=self._tx_refs(focus),
                )
            )

        if mode == "fiat":
            structuring = self._detect_structuring(focus, median_hist or p95_hist or 0)
            if structuring:
                evidence.append(structuring)
            smurfing = self._detect_smurfing(focus)
            if smurfing:
                evidence.append(smurfing)
            circular = self._detect_circular_transfers(focus)
            if circular:
                evidence.append(circular)

        if mode == "cross":
            cross_signal = self._cross_layer_signal(historical, focus)
            if cross_signal:
                evidence.append(cross_signal)

        if mode == "crypto":
            for t in focus:
                n = (t.narrative or "").lower()
                if any(k in n for k in ("mixer", "tornado", "sanction", "ofac", "pep")):
                    evidence.append(
                        self._evidence(
                            code="crypto_keyword",
                            label="Ключевые слова повышенного риска в описании операции",
                            category="crypto_keyword",
                            observed=t.narrative,
                            baseline=None,
                            threshold=None,
                            contribution=12,
                            tx_refs=self._tx_refs([t]),
                        )
                    )
                    break

        categories = self._unique_categories(evidence)
        score = max(0, min(100, sum(item["contribution"] for item in evidence)))
        confidence = self._confidence_score(profile, evidence, historical)
        reasons = [item["label"] for item in evidence] or ["Существенных отклонений от профиля не выявлено"]
        hypothesis = self._hypothesis(mode, categories)

        return {
            "anomaly_score": int(score),
            "confidence_score": confidence,
            "categories": categories,
            "reasons": reasons,
            "evidence": evidence,
            "new_pattern_hypothesis": hypothesis,
        }

    def _count_by_day(self, txs: Iterable[TransactionRecord]) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in txs:
            if not t.ts:
                continue
            d = self._normalize_ts(t.ts).date().isoformat()
            out[d] = out.get(d, 0) + 1
        return out

    def _unusual_hours(
        self,
        focus: list[TransactionRecord],
        h_start: int | None,
        h_end: int | None,
    ) -> list[str]:
        bad: list[str] = []
        if h_start is None or h_end is None:
            for t in focus:
                if not t.ts:
                    continue
                ts = self._normalize_ts(t.ts)
                if ts and (ts.hour < 7 or ts.hour > 22):
                    bad.append(f"{ts.strftime('%H:%M')}")
            return bad[:5]
        for t in focus:
            if not t.ts:
                continue
            ts = self._normalize_ts(t.ts)
            hr = ts.hour
            if h_start <= h_end:
                ok = h_start <= hr <= h_end
            else:
                ok = hr >= h_start or hr <= h_end
            if not ok:
                bad.append(ts.strftime("%H:%M"))
        return bad[:5]

    def _cross_layer_signal(
        self,
        historical: list[TransactionRecord],
        focus: list[TransactionRecord],
    ) -> dict | None:
        all_tx = [t for t in historical + focus if t.ts]
        fiat_out = [t for t in all_tx if t.asset_type == "fiat" and t.direction == "out"]
        crypto_any = [t for t in all_tx if t.asset_type == "crypto"]
        if not fiat_out or not crypto_any:
            return None

        best_delta: float | None = None
        best_pair: tuple[TransactionRecord, TransactionRecord] | None = None
        for f in fiat_out:
            for c in crypto_any:
                delta = abs((self._normalize_ts(c.ts) - self._normalize_ts(f.ts)).total_seconds())
                if best_delta is None or delta < best_delta:
                    best_delta = delta
                    best_pair = (f, c)
        if best_delta is None or best_pair is None:
            return None
        if best_delta <= 3600:
            return self._evidence(
                code="cross_transition_fast",
                label=f"Короткий интервал (~{int(best_delta // 60)} мин) между фиатной отдачей и крипто-операцией",
                category="cross_transition",
                observed=f"{int(best_delta // 60)} минут",
                baseline="разнесённые по времени контуры",
                threshold="<= 60 минут",
                contribution=16,
                tx_refs=self._tx_refs([best_pair[0], best_pair[1]]),
            )
        if best_delta <= 86400:
            return self._evidence(
                code="cross_transition_day",
                label="Интервал менее 24 ч между фиатной отдачей и крипто-операцией",
                category="cross_transition",
                observed=f"{int(best_delta // 3600)} часов",
                baseline="разнесённые по времени контуры",
                threshold="<= 24 часа",
                contribution=10,
                tx_refs=self._tx_refs([best_pair[0], best_pair[1]]),
            )
        return None

    def _detect_structuring(self, focus: list[TransactionRecord], baseline: float) -> dict | None:
        incoming = [t for t in focus if t.direction in ("in", "unknown")]
        if len(incoming) < 2:
            return None
        amounts = sorted(float(t.amount) for t in incoming)
        if amounts[-1] < max(1000.0, baseline):
            return None
        band_ratio = amounts[-1] / max(amounts[0], 1.0)
        if band_ratio <= 1.05:
            return self._evidence(
                code="structuring_band",
                label="Серия близких по сумме входящих операций выглядит как дробление под порог/контроль",
                category="structuring",
                observed=f"{len(incoming)} операций в диапазоне {amounts[0]:.2f}–{amounts[-1]:.2f}",
                baseline=round(baseline, 2) if baseline else None,
                threshold="разброс <= 5%",
                contribution=18,
                tx_refs=self._tx_refs(incoming),
            )
        return None

    def _detect_smurfing(self, focus: list[TransactionRecord]) -> dict | None:
        per_day: dict[str, list[TransactionRecord]] = defaultdict(list)
        for tx in focus:
            if tx.ts:
                per_day[self._normalize_ts(tx.ts).date().isoformat()].append(tx)
        for items in per_day.values():
            if len(items) >= 4 and all(float(t.amount) <= 1000 for t in items):
                return self._evidence(
                    code="smurfing_day",
                    label="Много мелких операций в один день — признак smurfing/размытия потока",
                    category="smurfing",
                    observed=len(items),
                    baseline="обычно менее 4 операций",
                    threshold=">= 4 мелких операции",
                    contribution=14,
                    tx_refs=self._tx_refs(items),
                )
        return None

    def _detect_circular_transfers(self, focus: list[TransactionRecord]) -> dict | None:
        pairs: dict[str, set[str]] = defaultdict(set)
        tx_refs: list[str] = []
        for tx in focus:
            cp = (tx.counterparty or "").strip()
            if not cp:
                continue
            pairs[cp].add(tx.direction)
            if tx.id:
                tx_refs.append(tx.id)
        for cp, dirs in pairs.items():
            if "in" in dirs and "out" in dirs:
                return self._evidence(
                    code="circular_counterparty",
                    label=f"Есть двусторонний поток с контрагентом {cp}, похожий на круговые переводы",
                    category="circular_transfers",
                    observed=cp,
                    baseline="однонаправленный поток",
                    threshold="вход и выход в одном алерте",
                    contribution=12,
                    tx_refs=tx_refs[:10],
                )
        return None

    def _confidence_score(self, profile: dict, evidence: list[dict], historical: list[TransactionRecord]) -> int:
        base = 35
        if historical:
            base += min(35, len(historical) // 20)
        if profile.get("activity_days"):
            base += min(15, int(profile["activity_days"]))
        if evidence:
            base += min(15, len(evidence) * 3)
        return max(5, min(95, base))

    def _unique_categories(self, evidence: list[dict]) -> list[AnomalyCategory]:
        seen: list[AnomalyCategory] = []
        for item in evidence:
            cat = item["category"]
            if cat not in seen:
                seen.append(cat)
        return seen

    def _evidence(
        self,
        *,
        code: str,
        label: str,
        category: AnomalyCategory,
        observed,
        baseline,
        threshold,
        contribution: int,
        tx_refs: list[str],
    ) -> dict:
        return {
            "code": code,
            "label": label,
            "category": category,
            "observed_value": observed,
            "baseline_value": baseline,
            "threshold_value": threshold,
            "contribution": contribution,
            "tx_refs": tx_refs[:20],
        }

    def _tx_refs(self, txs: list[TransactionRecord]) -> list[str]:
        refs = [t.id for t in txs if t.id]
        if refs:
            return refs
        return [f"idx-{i}" for i, _ in enumerate(txs, start=1)]

    def _hypothesis(self, mode: Mode, categories: list[AnomalyCategory]) -> str | None:
        if not categories:
            return None
        if mode == "fiat":
            if "structuring" in categories or "smurfing" in categories:
                return (
                    "Гипотеза: вероятно дробление операций под контрольные пороги или размытие потока; "
                    "нужна проверка источника средств и логики разбиения платежей."
                )
            return (
                "Гипотеза: зафиксировано поведенческое отклонение в фиатном контуре; "
                "требуется усиленная проверка документов, контрагента и экономического смысла операций."
            )
        if mode == "crypto":
            return (
                "Гипотеза: нестандартная связка каналов/контрагентов; проверьте санкционные списки и источник средств."
            )
        return (
            "Гипотеза: возможна связка фиатного контура с цифровыми активами в сжатые сроки; "
            "проверьте цепочку и назначение платежей."
        )

    def _normalize_ts(self, ts: datetime | None) -> datetime | None:
        if ts is None:
            return None
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
