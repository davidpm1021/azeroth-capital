"""A fixed comparison of market premises, not an optimized trading strategy."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import mean, median

from .paper import AH_CUT_RATE, compression_gap
from .temporal import signal_from_history
from .timestamps import future_row, observed_at, parse_timestamp


ARMS = ("compression", "discount", "depth", "market")
ENTRY_MODES = ("source_quote", "next_snapshot")


def strategy_names(history=5, fraction=0.20):
    suffix = f"h{history}-q{fraction:.2f}"
    return dict(zip(ARMS, (f"compression-gap-v2-{suffix}",
                          f"price-discount-v1-{suffix}",
                          f"depth-only-v1-{suffix}",
                          f"eligible-market-v1-{suffix}")))


def premise_rows(signals, timestamp, history=5, fraction=0.20):
    """Rank the entry universe before consulting any future observations."""
    names = strategy_names(history, fraction)
    take = max(1, round(len(signals) * fraction))
    scores = {
        "compression": compression_gap,
        "discount": lambda s: -s.baseline_price_change_pct,
        "depth": lambda s: -s.baseline_depth_5_change_pct,
        "market": lambda s: 0.0,
    }
    rows = []
    for arm, score in scores.items():
        ordered = sorted(signals, key=lambda s: (-score(s), s.item_id))
        selected = ordered if arm == "market" else ordered[:take]
        for rank, signal in enumerate(selected, 1):
            rows.append(dict(strategy=names[arm], observed_at=timestamp,
                             item_id=signal.item_id, feature_value=score(signal),
                             percentile=1-(rank-1)/len(signals), rank=rank,
                             universe_size=len(signals), entry_price=signal.reference_price))
    return rows


def historical_cohorts(histories, item_ids, history=5, fraction=0.20):
    """Exploratory replay; never insert reconstructed rows into paper storage."""
    by_time = defaultdict(list)
    known_at = {}
    for (item_id, _), rows in histories.items():
        if item_id not in item_ids:
            continue
        rows = sorted(rows, key=lambda r: parse_timestamp(observed_at(r)))
        for i in range(history-1, len(rows)):
            row = rows[i]
            if (int(row.get("total_quantity") or 0) < 100
                    or int(row.get("approx_market_value") or 0) < 100_000_000
                    or int(row.get("listing_count") or 0) < 50
                    or int(row.get("price_level_count") or 0) < 5):
                continue
            stamp = observed_at(row)
            by_time[stamp].append(signal_from_history(rows[i-history+1:i+1]))
            # Actual collection completion, not the retrospectively available source time.
            collected = row.get("completed_at") or row.get("started_at")
            if collected is None:
                raise ValueError("Historical latency research requires collection timestamps")
            available = max(parse_timestamp(stamp), parse_timestamp(collected))
            known_at[stamp] = max(known_at.get(stamp, available), available)
    frozen = []
    for stamp, signals in by_time.items():
        if len(signals) < 10:
            continue
        for row in premise_rows(signals, stamp, history, fraction):
            row["created_at"] = known_at[stamp].isoformat()
            frozen.append(row)
    return frozen


@dataclass(frozen=True)
class PremiseResult:
    cohort_at: str
    available_at: str
    arm: str
    mode: str
    item_id: int
    horizon_hours: int
    entry_at: str
    entry_price: int
    exit_at: str
    exit_price: int
    gross_pct: float
    net_pct: float


@dataclass
class PremiseEvaluation:
    cohorts_seen: int = 0
    cohorts_with_all_arms: int = 0
    cohorts_complete: int = 0
    results: list[PremiseResult] = field(default_factory=list)


def evaluate_premises(frozen, histories, horizon=6, history=5, fraction=0.20):
    names = {v: k for k, v in strategy_names(history, fraction).items()}
    cohorts = defaultdict(lambda: defaultdict(list))
    for row in frozen:
        if row["strategy"] in names:
            cohorts[parse_timestamp(row["observed_at"])][names[row["strategy"]]].append(row)
    evaluation = PremiseEvaluation(cohorts_seen=len(cohorts))
    for stamp, arms in sorted(cohorts.items()):
        if set(arms) != set(ARMS):
            continue
        size = int(arms["market"][0]["universe_size"])
        take = max(1, round(size * fraction))
        if any(len(rows) != (size if arm == "market" else take)
               or len({r["item_id"] for r in rows}) != len(rows)
               or any(int(r["universe_size"]) != size for r in rows)
               for arm, rows in arms.items()):
            continue
        evaluation.cohorts_with_all_arms += 1
        available = max(stamp, *(parse_timestamp(r["created_at"]) for rows in arms.values() for r in rows))
        cohort_results = []
        for arm in ARMS:
            for signal in arms[arm]:
                rows = histories.get((int(signal["item_id"]), 0), [])
                for mode in ENTRY_MODES:
                    entry_at, price = stamp, int(signal["entry_price"])
                    if mode == "next_snapshot":
                        # Use a strictly later publication, available after all four
                        # decisions were frozen. This is a latency probe, not a fill.
                        later = [r for r in rows if stamp < parse_timestamp(observed_at(r))
                                 and available <= parse_timestamp(observed_at(r)) <= available+timedelta(hours=1.5)]
                        entry = min(later, key=lambda r: parse_timestamp(observed_at(r)), default=None)
                        if entry is None:
                            continue
                        entry_at = parse_timestamp(observed_at(entry))
                        price = int(entry.get("reference_price") or entry["best_price"])
                    exit_row = future_row(rows, entry_at.isoformat(), horizon)
                    if exit_row is None or price <= 0:
                        continue
                    exit_price = int(exit_row.get("reference_price") or exit_row["best_price"])
                    if exit_price <= 0:
                        continue
                    cohort_results.append(PremiseResult(
                        stamp.isoformat(), available.isoformat(), arm, mode,
                        int(signal["item_id"]), horizon, entry_at.isoformat(), price,
                        observed_at(exit_row), exit_price, (exit_price/price-1)*100,
                        (exit_price*(1-AH_CUT_RATE)/price-1)*100))
        expected = sum(len(rows) for rows in arms.values()) * len(ENTRY_MODES)
        # Use the exact same complete cohorts for all arms AND both latency modes.
        # Missing exits cannot silently change the ranking or remove only losers.
        if len(cohort_results) == expected:
            evaluation.cohorts_complete += 1
            evaluation.results.extend(cohort_results)
    return evaluation


def summarize_premises(evaluation):
    groups = defaultdict(list)
    for row in evaluation.results:
        groups[(row.mode, row.arm, row.cohort_at)].append(row.net_pct)
    cohorts = sorted({r.cohort_at for r in evaluation.results})
    summaries = []
    for mode in ENTRY_MODES:
        for arm in ARMS:
            if not cohorts:
                continue
            means = [mean(groups[(mode, arm, t)]) for t in cohorts]
            values = [v for t in cohorts for v in groups[(mode, arm, t)]]
            spreads = [mean(groups[(mode, arm, t)])-mean(groups[(mode, "discount", t)]) for t in cohorts]
            market = [mean(groups[(mode, arm, t)])-mean(groups[(mode, "market", t)]) for t in cohorts]
            summaries.append(dict(mode=mode, arm=arm, cohorts=len(cohorts), candidates=len(values),
                net_avg=mean(means), candidate_median=median(values), cohort_median=median(means),
                positive_pct=sum(v>0 for v in values)/len(values)*100,
                excess_market_pp=mean(market), excess_discount_pp=mean(spreads)))
    return summaries
