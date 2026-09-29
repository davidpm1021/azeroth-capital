from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from statistics import mean, median

from .storage import Storage
from .temporal import signal_from_history


AH_CUT_RATE = 0.05


@dataclass(frozen=True)
class PaperResult:
    strategy: str
    group: str
    observed_at: str
    item_id: int
    rank: int
    universe_size: int
    feature_value: float
    entry_price: int
    horizon_hours: int
    future_at: str
    future_price: int
    gross_return_pct: float
    net_return_pct: float


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return parsedate_to_datetime(value)


def _observed_at(row: dict) -> str:
    return row.get("observed_at") or row.get("source_modified_at") or row["started_at"]


def _reference_price(row: dict) -> int:
    return int(row.get("reference_price") or row["best_price"])


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return ((current - previous) / previous) * 100.0


def compression_gap(signal) -> float:
    return -signal.baseline_depth_5_change_pct - max(signal.baseline_price_change_pct, 0.0)


def scan_compression_gap(
    storage: Storage,
    expansion: str = "Midnight",
    history_window: int = 5,
    top_fraction: float = 0.20,
    min_quantity: int = 100,
    min_market_value_g: int = 10_000,
    min_listings: int = 50,
    min_price_levels: int = 5,
) -> tuple[int, int, str | None]:
    expansion_ids = storage.expansion_item_ids(expansion)
    if not expansion_ids:
        return 0, 0, None

    status = storage.status()
    latest = status.get("latest") or {}
    latest_at = latest.get("source_modified_at") or latest.get("started_at")
    if not latest_at:
        return 0, 0, None

    histories = storage.market_histories("commodity", snapshots=history_window)
    candidates: list[tuple[float, object]] = []

    for (item_id, _), rows in histories.items():
        if item_id not in expansion_ids or len(rows) < history_window:
            continue
        current = rows[-1]
        if _observed_at(current) != latest_at:
            continue
        if int(current.get("total_quantity") or 0) < min_quantity:
            continue
        if int(current.get("approx_market_value") or 0) < min_market_value_g * 10_000:
            continue
        if int(current.get("listing_count") or 0) < min_listings:
            continue
        if int(current.get("price_level_count") or 0) < min_price_levels:
            continue

        signal = signal_from_history(rows)
        candidates.append((compression_gap(signal), signal))

    if not candidates:
        return 0, 0, latest_at

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    take = max(1, int(round(len(candidates) * top_fraction)))
    selected_top = candidates[:take]
    selected_bottom = candidates[-take:]

    rows = []
    top_strategy = f"compression-gap-h{history_window}-q{top_fraction:.2f}"
    bottom_strategy = f"compression-gap-bottom-h{history_window}-q{top_fraction:.2f}"

    for rank, (feature_value, signal) in enumerate(selected_top, start=1):
        rows.append(
            {
                "strategy": top_strategy,
                "observed_at": latest_at,
                "item_id": signal.item_id,
                "feature_value": feature_value,
                "percentile": 1.0 - ((rank - 1) / len(candidates)),
                "rank": rank,
                "universe_size": len(candidates),
                "entry_price": signal.reference_price,
            }
        )

    for reverse_rank, (feature_value, signal) in enumerate(reversed(selected_bottom), start=1):
        rows.append(
            {
                "strategy": bottom_strategy,
                "observed_at": latest_at,
                "item_id": signal.item_id,
                "feature_value": feature_value,
                "percentile": reverse_rank / len(candidates),
                "rank": reverse_rank,
                "universe_size": len(candidates),
                "entry_price": signal.reference_price,
            }
        )

    inserted = storage.insert_paper_signals(rows)
    return inserted, len(candidates), latest_at


def _future_row(
    history: list[dict],
    observed_at: str,
    horizon_hours: int,
    tolerance_hours: float = 1.5,
) -> dict | None:
    start = _parse_timestamp(observed_at)
    target = start + timedelta(hours=horizon_hours)

    candidates = [
        row for row in history
        if _parse_timestamp(_observed_at(row)) > start
    ]
    if not candidates:
        return None

    future = min(
        candidates,
        key=lambda row: abs((_parse_timestamp(_observed_at(row)) - target).total_seconds()),
    )
    error_hours = abs((_parse_timestamp(_observed_at(future)) - target).total_seconds()) / 3600.0
    return future if error_hours <= tolerance_hours else None


def evaluate_paper(
    storage: Storage,
    strategy_prefix: str = "compression-gap",
    horizons: tuple[int, ...] = (3, 6, 12),
) -> list[PaperResult]:
    histories = storage.all_market_histories("commodity")
    signals = [
        row for row in storage.paper_signals()
        if row["strategy"].startswith(strategy_prefix)
    ]
    results: list[PaperResult] = []

    for signal in signals:
        history = histories.get((int(signal["item_id"]), 0))
        if not history:
            continue
        for horizon in horizons:
            future = _future_row(history, signal["observed_at"], horizon)
            if future is None:
                continue
            future_price = _reference_price(future)
            gross = _pct_change(future_price, int(signal["entry_price"]))
            net = (
                (future_price * (1.0 - AH_CUT_RATE) - int(signal["entry_price"]))
                / int(signal["entry_price"])
                * 100.0
            )
            results.append(
                PaperResult(
                    strategy=signal["strategy"],
                    group="bottom" if "-bottom-" in signal["strategy"] else "top",
                    observed_at=signal["observed_at"],
                    item_id=int(signal["item_id"]),
                    rank=int(signal["rank"]),
                    universe_size=int(signal["universe_size"]),
                    feature_value=float(signal["feature_value"]),
                    entry_price=int(signal["entry_price"]),
                    horizon_hours=horizon,
                    future_at=_observed_at(future),
                    future_price=future_price,
                    gross_return_pct=gross,
                    net_return_pct=net,
                )
            )
    return results


def summarize_paper(results: list[PaperResult]) -> list[dict]:
    summaries = []
    horizons = sorted({row.horizon_hours for row in results})
    for horizon in horizons:
        top = [
            row for row in results
            if row.horizon_hours == horizon and row.group == "top"
        ]
        bottom = [
            row for row in results
            if row.horizon_hours == horizon and row.group == "bottom"
        ]
        if not top:
            continue

        top_gross = [row.gross_return_pct for row in top]
        top_net = [row.net_return_pct for row in top]
        bottom_gross = [row.gross_return_pct for row in bottom]

        summaries.append(
            {
                "horizon_hours": horizon,
                "samples": len(top),
                "bottom_samples": len(bottom),
                "gross_avg": mean(top_gross),
                "gross_median": median(top_gross),
                "bottom_gross_avg": mean(bottom_gross) if bottom_gross else None,
                "gross_spread": (
                    mean(top_gross) - mean(bottom_gross)
                    if bottom_gross else None
                ),
                "net_avg": mean(top_net),
                "net_median": median(top_net),
                "net_positive_rate": sum(value > 0 for value in top_net) / len(top_net) * 100.0,
                "net_5pct_rate": sum(value >= 5 for value in top_net) / len(top_net) * 100.0,
            }
        )
    return summaries
