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
    selected = candidates[:take]

    rows = []
    for rank, (feature_value, signal) in enumerate(selected, start=1):
        rows.append(
            {
                "strategy": f"compression-gap-h{history_window}-q{top_fraction:.2f}",
                "observed_at": latest_at,
                "item_id": signal.item_id,
                "feature_value": feature_value,
                "percentile": 1.0 - ((rank - 1) / len(candidates)),
                "rank": rank,
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
    for horizon in sorted({row.horizon_hours for row in results}):
        subset = [row for row in results if row.horizon_hours == horizon]
        if not subset:
            continue
        gross = [row.gross_return_pct for row in subset]
        net = [row.net_return_pct for row in subset]
        summaries.append(
            {
                "horizon_hours": horizon,
                "samples": len(subset),
                "gross_avg": mean(gross),
                "gross_median": median(gross),
                "net_avg": mean(net),
                "net_median": median(net),
                "net_positive_rate": sum(value > 0 for value in net) / len(net) * 100.0,
                "net_5pct_rate": sum(value >= 5 for value in net) / len(net) * 100.0,
            }
        )
    return summaries
