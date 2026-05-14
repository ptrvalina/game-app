"""
AnomalyDetector: anomaly_score 0–100, причины, гипотеза новой схемы (эвристики + pandas-профиль).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
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

        historical = sorted(historical, key=lambda tx: (self._normalize_ts(tx.ts) is None, self._normalize_ts(tx.ts), tx.id or ""))
        focus = sorted(focus, key=lambda tx: (self._normalize_ts(tx.ts) is None, self._normalize_ts(tx.ts), tx.id or ""))
        evidence: list[dict] = []

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

        focus_day_counts = self._count_by_day(focus)
        focus_peak = max(focus_day_counts.values()) if focus_day_counts else 0
        avg_daily = profile.get("avg_daily_count")
        if avg_daily and focus_peak > max(4.0, avg_daily * 1.8):
            peak_day = max(focus_day_counts, key=focus_day_counts.get)
            evidence.append(
                self._evidence(
                    code="velocity_daily",
                    label=f"Пиковая активность в alert-окне: до {focus_peak} операций в день при исторической норме ~{avg_daily:.1f}",
                    category="velocity_spike",
                    observed=focus_peak,
                    baseline=round(avg_daily, 2),
                    threshold=max(4.0, round(avg_daily * 1.8, 2)),
                    contribution=20,
                    tx_refs=self._tx_refs([t for t in focus if t.ts and self._normalize_ts(t.ts).date().isoformat() == peak_day]),
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

        focus_burst_threshold = max(6.0, float(avg_daily or 0) * 2.0)
        if focus_peak >= focus_burst_threshold:
            peak_day = max(focus_day_counts, key=focus_day_counts.get)
            evidence.append(
                self._evidence(
                    code="burst_pattern",
                    label="В alert-окне зафиксирован burst активности относительно исторической нормы",
                    category="burst_activity",
                    observed=focus_peak,
                    baseline=round(avg_daily, 2) if avg_daily else 0,
                    threshold=round(focus_burst_threshold, 2),
                    contribution=10,
                    tx_refs=self._tx_refs([t for t in focus if t.ts and self._normalize_ts(t.ts).date().isoformat() == peak_day]),
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
            dormant = self._detect_dormant_activation(historical, focus)
            if dormant:
                evidence.append(dormant)
            burst_cp = self._detect_new_counterparty_burst(hist_cp, focus)
            if burst_cp:
                evidence.append(burst_cp)
            salary = self._detect_salary_mismatch(profile, historical, focus, declared_monthly_income)
            if salary:
                evidence.append(salary)
            mule = self._detect_mule_indicators(focus)
            if mule:
                evidence.append(mule)
            salary_pass = self._detect_salary_pass_through(historical, focus)
            if salary_pass:
                evidence.append(salary_pass)
            rapid_cash_out = self._detect_rapid_cash_out(focus)
            if rapid_cash_out:
                evidence.append(rapid_cash_out)
            funnel = self._detect_funnel_account_behavior(focus)
            if funnel:
                evidence.append(funnel)

        if mode == "cross":
            cross_signal = self._cross_layer_signal(historical, focus)
            if cross_signal:
                evidence.append(cross_signal)
            cash_crypto = self._detect_cash_to_crypto_outflow(historical, focus)
            if cash_crypto:
                evidence.append(cash_crypto)
            timing = self._detect_timing_correlation(historical, focus)
            if timing:
                evidence.append(timing)
            window_sig = self._detect_transition_window(historical, focus)
            if window_sig:
                evidence.append(window_sig)
            crossing = self._detect_repeated_exchange_boundary_crossing(historical, focus)
            if crossing:
                evidence.append(crossing)
            clusters = self._detect_time_linked_transition_clusters(historical, focus)
            if clusters:
                evidence.append(clusters)

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
            hopping = self._detect_exchange_hopping(historical, focus)
            if hopping:
                evidence.append(hopping)
            fan = self._detect_wallet_fan_out(focus)
            if fan:
                evidence.append(fan)
            fan_in = self._detect_wallet_fan_in(focus)
            if fan_in:
                evidence.append(fan_in)
            micro = self._detect_micro_splitting(focus)
            if micro:
                evidence.append(micro)
            bridge = self._detect_bridge_behavior(historical, focus)
            if bridge:
                evidence.append(bridge)
            bridge_seq = self._detect_bridge_sequencing(historical, focus)
            if bridge_seq:
                evidence.append(bridge_seq)
            rapid_fc = self._detect_rapid_fiat_to_crypto(historical, focus)
            if rapid_fc:
                evidence.append(rapid_fc)
            peel = self._detect_peel_chains(focus)
            if peel:
                evidence.append(peel)
            stable = self._detect_stablecoin_bursts(focus)
            if stable:
                evidence.append(stable)

        categories = self._unique_categories(evidence)
        score = max(0, min(100, sum(item["contribution"] for item in evidence)))
        confidence = self._confidence_score(profile, evidence, historical, focus)
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
        focus_refs = {id(t) for t in focus if t.ts}
        fiat_out = [t for t in all_tx if t.asset_type == "fiat" and t.direction == "out"]
        crypto_any = [t for t in all_tx if t.asset_type == "crypto"]
        if not fiat_out or not crypto_any:
            return None

        best_delta: float | None = None
        best_pair: tuple[TransactionRecord, TransactionRecord] | None = None
        for f in fiat_out:
            for c in crypto_any:
                if id(f) not in focus_refs and id(c) not in focus_refs:
                    continue
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

    def _detect_dormant_activation(self, historical: list[TransactionRecord], focus: list[TransactionRecord]) -> dict | None:
        if not historical:
            return None
        dated_hist = [self._normalize_ts(t.ts) for t in historical if t.ts]
        if not dated_hist:
            return None
        last_hist = max(dated_hist)
        focus_ts = [self._normalize_ts(t.ts) for t in focus if t.ts]
        if not focus_ts:
            return None
        first_focus = min(focus_ts)
        gap_days = (first_focus - last_hist).total_seconds() / 86400
        if gap_days >= 45 and len(focus) >= 2:
            return self._evidence(
                code="dormant_gap",
                label=f"Долгий инертный интервал (~{int(gap_days)} дн.) перед активностью в alert-окне",
                category="dormant_activation",
                observed=int(gap_days),
                baseline="регулярная активность",
                threshold=">= 45 дней без операций",
                contribution=14,
                tx_refs=self._tx_refs(focus[:8]),
            )
        return None

    def _detect_new_counterparty_burst(self, hist_cp: set[str], focus: list[TransactionRecord]) -> dict | None:
        new_ids = {
            (t.counterparty or "").strip()
            for t in focus
            if t.counterparty and (t.counterparty or "").strip() and (t.counterparty or "").strip() not in hist_cp
        }
        if len(new_ids) >= 3:
            return self._evidence(
                code="new_cp_burst",
                label=f"Всплеск новых контрагентов в окне: {len(new_ids)} уникальных",
                category="new_counterparty_burst",
                observed=len(new_ids),
                baseline="обычно 0–1 новых в кратком окне",
                threshold=">= 3 новых",
                contribution=16,
                tx_refs=self._tx_refs([t for t in focus if (t.counterparty or "").strip() in new_ids]),
            )
        return None

    def _detect_salary_mismatch(
        self,
        _profile: dict,
        historical: list[TransactionRecord],
        focus: list[TransactionRecord],
        declared: Decimal | None,
    ) -> dict | None:
        if not declared or declared <= 0:
            return None
        dated_in = [float(t.amount) for t in historical if t.ts and t.direction in ("in", "unknown")]
        if len(dated_in) < 5:
            return None
        first_ts = min(self._normalize_ts(t.ts) for t in historical if t.ts)
        last_ts = max(self._normalize_ts(t.ts) for t in historical if t.ts)
        span_days = max(14, (last_ts - first_ts).days + 1)
        months = max(1.0, span_days / 30.0)
        typical_monthly = sum(dated_in) / months
        focus_in = sum(float(t.amount) for t in focus if t.direction in ("in", "unknown"))
        if typical_monthly < float(declared) * 0.85 and focus_in > float(declared) * 4:
            return self._evidence(
                code="salary_mismatch",
                label="Типичные поступления ниже декларируемого дохода, но окно alert содержит крупный приток",
                category="salary_mismatch",
                observed=round(focus_in, 2),
                baseline=f"declared={declared}, typical_monthly≈{typical_monthly:.0f}",
                threshold="focus_in > 4x declared при типичных поступлениях ниже declared",
                contribution=16,
                tx_refs=self._tx_refs([t for t in focus if t.direction in ("in", "unknown")]),
            )
        return None

    _EXCHANGE_TOKENS = (
        "binance",
        "coinbase",
        "kraken",
        "okx",
        "bybit",
        "bitfinex",
        "kucoin",
        "gemini",
        "htx",
        "gate.io",
        "exchange",
    )

    def _looks_exchange_like(self, tx: TransactionRecord) -> bool:
        bag = f"{tx.counterparty or ''} {tx.narrative or ''} {tx.channel or ''}".lower()
        return any(tok in bag for tok in self._EXCHANGE_TOKENS)

    def _detect_exchange_hopping(self, historical: list[TransactionRecord], focus: list[TransactionRecord]) -> dict | None:
        pool = [t for t in historical + focus if t.ts and self._looks_exchange_like(t)]
        if len(pool) < 6:
            return None
        pool.sort(key=lambda t: self._normalize_ts(t.ts) or datetime.min.replace(tzinfo=timezone.utc))
        window_start = next((self._normalize_ts(t.ts) for t in focus if t.ts), None)
        if not window_start:
            return None
        win_end = window_start + timedelta(days=14)
        recent = [t for t in pool if self._normalize_ts(t.ts) and window_start <= self._normalize_ts(t.ts) <= win_end]
        names: set[str] = set()
        for t in recent:
            key = (t.counterparty or "").strip() or (t.narrative or "")[:48]
            if key:
                names.add(key)
        if len(names) >= 3:
            return self._evidence(
                code="exchange_hopping",
                label="Серия операций с различными биржевыми/обменными контрагентами за короткий период",
                category="exchange_hopping",
                observed=len(names),
                baseline="<= 2 различных обменных контрагента",
                threshold=">= 3 за 14 дней",
                contribution=14,
                tx_refs=self._tx_refs(recent[:12]),
            )
        return None

    def _detect_wallet_fan_out(self, focus: list[TransactionRecord]) -> dict | None:
        outs = [t for t in focus if t.asset_type == "crypto" and t.direction == "out" and (t.counterparty or "").strip()]
        uniq = {t.counterparty.strip() for t in outs if t.counterparty}
        if len(outs) >= 6 and len(uniq) >= 5:
            return self._evidence(
                code="wallet_fan_out",
                label="Множественные исходящие крипто-переводы на разные адреса/контрагентов в одном окне",
                category="wallet_fan_out",
                observed=len(uniq),
                baseline="узкий набор получателей",
                threshold=">= 5 уникальных получателей",
                contribution=16,
                tx_refs=self._tx_refs(outs[:12]),
            )
        return None

    def _detect_micro_splitting(self, focus: list[TransactionRecord]) -> dict | None:
        small = [t for t in focus if t.asset_type == "crypto" and float(t.amount) < 150]
        if len(small) >= 8:
            return self._evidence(
                code="micro_splitting",
                label="Серия мелких крипто-операций похожа на дробление/micro-splitting",
                category="micro_splitting",
                observed=len(small),
                baseline="< 8 мелких tx",
                threshold="amount < 150, count >= 8",
                contribution=14,
                tx_refs=self._tx_refs(small[:12]),
            )
        return None

    def _detect_bridge_behavior(self, historical: list[TransactionRecord], focus: list[TransactionRecord]) -> dict | None:
        keywords = (
            "bridge",
            "wormhole",
            "layerzero",
            "cross-chain",
            "cross chain",
            "l2_",
            "stargate",
            "hop protocol",
            "relay",
        )
        flagged = [
            t
            for t in historical + focus
            if t.narrative and any(k in t.narrative.lower() for k in keywords)
        ]
        if len(flagged) >= 2:
            return self._evidence(
                code="bridge_like",
                label="В описаниях операций встречаются признаки кросс-чейн/bridge-потока",
                category="bridge_behavior",
                observed=len(flagged),
                baseline="без bridge-тематики",
                threshold=">= 2 операции",
                contribution=12,
                tx_refs=self._tx_refs(flagged[:10]),
            )
        return None

    def _detect_rapid_fiat_to_crypto(self, historical: list[TransactionRecord], focus: list[TransactionRecord]) -> dict | None:
        all_tx = [t for t in historical + focus if t.ts]
        fiat_in = [t for t in all_tx if t.asset_type == "fiat" and t.direction in ("in", "unknown")]
        crypto_tx = [t for t in all_tx if t.asset_type == "crypto"]
        best: tuple[float, TransactionRecord, TransactionRecord] | None = None
        focus_ids = {id(t) for t in focus}
        for f in fiat_in:
            for c in crypto_tx:
                if id(f) not in focus_ids and id(c) not in focus_ids:
                    continue
                dt = abs((self._normalize_ts(c.ts) - self._normalize_ts(f.ts)).total_seconds())
                if best is None or dt < best[0]:
                    best = (dt, f, c)
        if best and best[0] <= 7200:
            return self._evidence(
                code="rapid_fiat_crypto",
                label="Быстрый переход фиатного притока в крипто-операции (on-ramp-like window)",
                category="cross_transition",
                observed=f"{int(best[0] // 60)} мин",
                baseline="разнесённые контуры",
                threshold="<= 120 минут",
                contribution=12,
                tx_refs=self._tx_refs([best[1], best[2]]),
            )
        return None

    def _detect_cash_to_crypto_outflow(self, historical: list[TransactionRecord], focus: list[TransactionRecord]) -> dict | None:
        all_tx = [t for t in historical + focus if t.ts]
        cash_in = [
            t
            for t in all_tx
            if t.asset_type == "fiat"
            and t.direction in ("in", "unknown")
            and (t.channel or "").lower() in {"cash", "atm", "cashin", "cassette"}
        ]
        crypto_out = [t for t in all_tx if t.asset_type == "crypto" and t.direction == "out"]
        best: tuple[float, TransactionRecord, TransactionRecord] | None = None
        focus_ids = {id(t) for t in focus}
        for ci in cash_in:
            for co in crypto_out:
                if id(ci) not in focus_ids and id(co) not in focus_ids:
                    continue
                delta = (self._normalize_ts(co.ts) - self._normalize_ts(ci.ts)).total_seconds()
                if 0 <= delta <= 259200:
                    if best is None or delta < best[0]:
                        best = (delta, ci, co)
        if best:
            return self._evidence(
                code="cash_to_crypto",
                label="Наличный/ATM-приток с последующим крипто-out в окне до 72ч",
                category="cash_to_crypto_outflow",
                observed=f"{int(best[0] // 3600)} ч",
                baseline="нет быстрой связки cash→crypto",
                threshold="<= 72 часа",
                contribution=18,
                tx_refs=self._tx_refs([best[1], best[2]]),
            )
        return None

    def _detect_timing_correlation(self, historical: list[TransactionRecord], focus: list[TransactionRecord]) -> dict | None:
        all_tx = sorted([t for t in historical + focus if t.ts], key=lambda t: self._normalize_ts(t.ts))
        gaps_hours: list[float] = []
        for i in range(len(all_tx) - 1):
            a, b = all_tx[i], all_tx[i + 1]
            if a.asset_type == "fiat" and a.direction == "out" and b.asset_type == "crypto":
                sec = (self._normalize_ts(b.ts) - self._normalize_ts(a.ts)).total_seconds()
                if 0 < sec < 86400:
                    gaps_hours.append(sec / 3600)
        if len(gaps_hours) < 3:
            return None
        rounded = [round(g, 1) for g in gaps_hours]
        top, cnt = Counter(rounded).most_common(1)[0]
        if cnt >= 3:
            return self._evidence(
                code="timing_correlation",
                label="Повторяющиеся интервалы между фиат-out и крипто-операциями (похожая задержка в часах)",
                category="timing_correlation",
                observed=f"gap≈{top}h count={cnt}",
                baseline="случайные интервалы",
                threshold=">= 3 совпадения округлённого интервала",
                contribution=14,
                tx_refs=self._tx_refs(all_tx[-8:]),
            )
        return None

    def _detect_transition_window(self, historical: list[TransactionRecord], focus: list[TransactionRecord]) -> dict | None:
        all_tx = [t for t in historical + focus if t.ts]
        best_delta: float | None = None
        focus_ids = {id(t) for t in focus}
        for f in (t for t in all_tx if t.asset_type == "fiat" and t.direction == "out"):
            for c in (t for t in all_tx if t.asset_type == "crypto"):
                if id(f) not in focus_ids and id(c) not in focus_ids:
                    continue
                delta = abs((self._normalize_ts(c.ts) - self._normalize_ts(f.ts)).total_seconds())
                if 3600 < delta <= 172800 and (best_delta is None or delta < best_delta):
                    best_delta = delta
        if best_delta is not None:
            return self._evidence(
                code="transition_window",
                label="Связка фиат-out → крипто в расширенном окне (1–48ч), требует усиленной проверки",
                category="transition_window",
                observed=f"{int(best_delta // 3600)} ч",
                baseline="разнесённые слои",
                threshold="1ч < gap <= 48ч",
                contribution=10,
                tx_refs=self._tx_refs(focus[:6]),
            )
        return None

    def _detect_mule_indicators(self, focus: list[TransactionRecord]) -> dict | None:
        incoming = [t for t in focus if t.direction in ("in", "unknown") and (t.counterparty or "").strip()]
        outgoing = [t for t in focus if t.direction == "out"]
        unique_in = {(t.counterparty or "").strip() for t in incoming if (t.counterparty or "").strip()}
        if len(unique_in) < 3 or len(outgoing) < 2:
            return None
        total_in = sum(float(t.amount) for t in incoming)
        total_out = sum(float(t.amount) for t in outgoing)
        if total_in <= 0 or total_out / total_in < 0.75:
            return None
        return self._evidence(
            code="mule_indicator",
            label="Многочисленные входящие от разных контрагентов быстро конвертируются в исходящий поток",
            category="mule_account_indicators",
            observed=f"in_cp={len(unique_in)} outflow_ratio={total_out / total_in:.2f}",
            baseline="ограниченный набор отправителей и более медленное удержание средств",
            threshold=">= 3 входящих контрагента и outflow/inflow >= 0.75",
            contribution=15,
            tx_refs=self._tx_refs(incoming[:6] + outgoing[:6]),
        )

    def _detect_salary_pass_through(self, historical: list[TransactionRecord], focus: list[TransactionRecord]) -> dict | None:
        salary_like = [
            t
            for t in historical + focus
            if t.direction in ("in", "unknown")
            and any(token in (t.counterparty or "").lower() or token in (t.narrative or "").lower() for token in ("salary", "payroll", "зарп"))
        ]
        if not salary_like:
            return None
        focus_salary = [t for t in focus if t in salary_like]
        if not focus_salary:
            return None
        outgoing = [t for t in focus if t.direction == "out" and t.ts]
        if not outgoing:
            return None
        salary_total = sum(float(t.amount) for t in focus_salary)
        passed_through = 0.0
        refs: list[TransactionRecord] = []
        for inc in focus_salary:
            inc_ts = self._normalize_ts(inc.ts)
            if not inc_ts:
                continue
            for out in outgoing:
                out_ts = self._normalize_ts(out.ts)
                if out_ts and 0 <= (out_ts - inc_ts).total_seconds() <= 86400:
                    passed_through += float(out.amount)
                    refs.extend([inc, out])
        if salary_total > 0 and passed_through / salary_total >= 0.8:
            return self._evidence(
                code="salary_pass_through",
                label="Поступления, похожие на зарплатные, почти полностью уходят в течение 24 часов",
                category="salary_pass_through",
                observed=f"{passed_through:.2f}/{salary_total:.2f}",
                baseline="частичное расходование после удержания средств",
                threshold="outflow >= 80% salary-like inflow in 24h",
                contribution=14,
                tx_refs=self._tx_refs(refs[:10]),
            )
        return None

    def _detect_rapid_cash_out(self, focus: list[TransactionRecord]) -> dict | None:
        inflow = [t for t in focus if t.direction in ("in", "unknown") and t.ts]
        outflow = [t for t in focus if t.direction == "out" and t.ts]
        refs: list[TransactionRecord] = []
        for inc in inflow:
            inc_ts = self._normalize_ts(inc.ts)
            matched_out = [
                out
                for out in outflow
                if 0 <= (self._normalize_ts(out.ts) - inc_ts).total_seconds() <= 43200
            ]
            if matched_out and sum(float(t.amount) for t in matched_out) >= float(inc.amount) * 0.8:
                refs.extend([inc, *matched_out])
        if refs:
            return self._evidence(
                code="rapid_cash_out",
                label="Входящие средства быстро уходят исходящими операциями в пределах 12 часов",
                category="rapid_cash_out",
                observed=len({t.id for t in refs if t.id}),
                baseline="более длительное удержание средств",
                threshold=">= 80% outflow within 12h",
                contribution=14,
                tx_refs=self._tx_refs(refs[:12]),
            )
        return None

    def _detect_funnel_account_behavior(self, focus: list[TransactionRecord]) -> dict | None:
        incoming = [t for t in focus if t.direction in ("in", "unknown") and (t.counterparty or "").strip()]
        outgoing = [t for t in focus if t.direction == "out" and (t.counterparty or "").strip()]
        uniq_in = {(t.counterparty or "").strip() for t in incoming}
        uniq_out = {(t.counterparty or "").strip() for t in outgoing}
        if len(uniq_in) >= 4 and 1 <= len(uniq_out) <= 2:
            return self._evidence(
                code="funnel_behavior",
                label="Средства от многих входящих контрагентов сходятся и уходят к одному/двум получателям",
                category="funnel_account_behavior",
                observed=f"in={len(uniq_in)} out={len(uniq_out)}",
                baseline="более распределённый выходной поток",
                threshold=">= 4 входящих контрагента и <= 2 исходящих контрагента",
                contribution=15,
                tx_refs=self._tx_refs(incoming[:8] + outgoing[:4]),
            )
        return None

    def _detect_wallet_fan_in(self, focus: list[TransactionRecord]) -> dict | None:
        incoming = [t for t in focus if t.asset_type == "crypto" and t.direction in ("in", "unknown") and (t.counterparty or "").strip()]
        uniq = {(t.counterparty or "").strip() for t in incoming}
        if len(incoming) >= 6 and len(uniq) >= 5:
            return self._evidence(
                code="wallet_fan_in",
                label="Множественные входящие крипто-переводы с разных адресов сходятся в одном окне",
                category="fan_in",
                observed=len(uniq),
                baseline="ограниченный набор источников",
                threshold=">= 5 уникальных источников",
                contribution=14,
                tx_refs=self._tx_refs(incoming[:12]),
            )
        return None

    def _detect_peel_chains(self, focus: list[TransactionRecord]) -> dict | None:
        outgoing = [t for t in focus if t.asset_type == "crypto" and t.direction == "out" and t.ts]
        if len(outgoing) < 4:
            return None
        outgoing = sorted(outgoing, key=lambda tx: self._normalize_ts(tx.ts))
        descending = 0
        for prev, cur in zip(outgoing, outgoing[1:]):
            if float(cur.amount) < float(prev.amount):
                descending += 1
        if descending >= 3:
            return self._evidence(
                code="peel_chain",
                label="Последовательные уменьшающиеся крипто-out операции выглядят как peel chain",
                category="peel_chains",
                observed=descending + 1,
                baseline="несвязанные по объёму отправки",
                threshold=">= 4 последовательные уменьшающиеся операции",
                contribution=15,
                tx_refs=self._tx_refs(outgoing[:12]),
            )
        return None

    def _detect_stablecoin_bursts(self, focus: list[TransactionRecord]) -> dict | None:
        stable = [
            t for t in focus if (t.currency or "").upper() in {"USDT", "USDC"} and t.asset_type == "crypto"
        ]
        if len(stable) >= 6:
            return self._evidence(
                code="stablecoin_burst",
                label="Кластер частых stablecoin-операций в одном alert-окне",
                category="stablecoin_bursts",
                observed=len(stable),
                baseline="менее 6 stablecoin tx",
                threshold=">= 6 операций USDT/USDC",
                contribution=12,
                tx_refs=self._tx_refs(stable[:12]),
            )
        return None

    def _detect_bridge_sequencing(self, historical: list[TransactionRecord], focus: list[TransactionRecord]) -> dict | None:
        keywords = ("bridge", "wormhole", "layerzero", "stargate", "hop protocol", "relay")
        bridge = [
            t
            for t in historical + focus
            if t.ts and t.narrative and any(token in t.narrative.lower() for token in keywords)
        ]
        if len(bridge) < 3:
            return None
        bridge = sorted(bridge, key=lambda tx: self._normalize_ts(tx.ts))
        span_seconds = (self._normalize_ts(bridge[-1].ts) - self._normalize_ts(bridge[0].ts)).total_seconds()
        if span_seconds <= 21600:
            return self._evidence(
                code="bridge_sequence",
                label="Несколько bridge-like операций сгруппированы в плотную последовательность",
                category="bridge_sequencing",
                observed=f"{len(bridge)} tx / {int(span_seconds // 60)} min",
                baseline="разрозненные bridge операции",
                threshold=">= 3 операции в пределах 6h",
                contribution=13,
                tx_refs=self._tx_refs(bridge[:12]),
            )
        return None

    def _detect_repeated_exchange_boundary_crossing(
        self,
        historical: list[TransactionRecord],
        focus: list[TransactionRecord],
    ) -> dict | None:
        all_tx = sorted([t for t in historical + focus if t.ts], key=lambda tx: self._normalize_ts(tx.ts))
        pairs = 0
        refs: list[TransactionRecord] = []
        for prev, cur in zip(all_tx, all_tx[1:]):
            prev_exchange = self._looks_exchange_like(prev)
            cur_exchange = self._looks_exchange_like(cur)
            boundary = prev.asset_type != cur.asset_type and (prev_exchange or cur_exchange)
            delta = abs((self._normalize_ts(cur.ts) - self._normalize_ts(prev.ts)).total_seconds())
            if boundary and delta <= 86400:
                pairs += 1
                refs.extend([prev, cur])
        if pairs >= 3:
            return self._evidence(
                code="exchange_boundary_crossing",
                label="Повторяющиеся переходы через exchange boundary между фиатным и крипто-контурами",
                category="repeated_exchange_boundary_crossing",
                observed=pairs,
                baseline="единичные переходы",
                threshold=">= 3 boundary crossing within 24h windows",
                contribution=15,
                tx_refs=self._tx_refs(refs[:12]),
            )
        return None

    def _detect_time_linked_transition_clusters(
        self,
        historical: list[TransactionRecord],
        focus: list[TransactionRecord],
    ) -> dict | None:
        all_tx = [t for t in historical + focus if t.ts]
        clusters = 0
        refs: list[TransactionRecord] = []
        for f in (t for t in all_tx if t.asset_type == "fiat" and t.direction in ("in", "out")):
            f_ts = self._normalize_ts(f.ts)
            linked = [
                c
                for c in all_tx
                if c.asset_type == "crypto"
                and c is not f
                and abs((self._normalize_ts(c.ts) - f_ts).total_seconds()) <= 10800
            ]
            if linked:
                clusters += 1
                refs.extend([f, *linked[:2]])
        if clusters >= 3:
            return self._evidence(
                code="time_linked_cluster",
                label="Сформирован повторяющийся кластер близких по времени fiat/crypto переходов",
                category="time_linked_transition_clusters",
                observed=clusters,
                baseline="единичные time-linked transitions",
                threshold=">= 3 transition clusters in <= 3h windows",
                contribution=14,
                tx_refs=self._tx_refs(refs[:12]),
            )
        return None

    def _confidence_score(
        self,
        profile: dict,
        evidence: list[dict],
        historical: list[TransactionRecord],
        focus: list[TransactionRecord],
    ) -> int:
        base = 20
        if historical:
            base += min(30, len(historical) // 25)
        if profile.get("activity_days"):
            base += min(10, int(profile["activity_days"]))
        if evidence:
            base += min(12, len(evidence) * 3)
        if len(historical) < 20:
            base -= 10
        if len(historical) < 8:
            base -= 10
        missing_ts = sum(1 for tx in historical + focus if tx.ts is None)
        base -= min(15, missing_ts * 3)
        weak_categories = {"new_counterparty", "crypto_keyword", "off_hours"}
        if evidence and all(item["category"] in weak_categories for item in evidence):
            base -= 8
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
