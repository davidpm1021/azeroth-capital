from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from statistics import mean, median

from .temporal import MarketSignal, signal_from_history


@dataclass(frozen=True)
class ForwardResult:
    item_id: int
    signal_at: str
    pressure: float
    reference_price: int
    horizon_hours: int
    future_at: str
    future_price: int
    forward_return_pct: float


@dataclass(frozen=True)
class HorizonSummary:
    horizon_hours: int
    samples: int
    average_return_pct: float
    median_return_pct: float
    positive_rate_pct: float
    return_5pct_rate_pct: float
    return_10pct_rate_pct: float


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


def _future_row(
    history: list[dict],
    signal_index: int,
    horizon_hours: int,
    tolerance_hours: float = 1.5,
) -> dict | None:
    signal_time = _parse_timestamp(_observed_at(history[signal_index]))
    target = signal_time + timedelta(hours=horizon_hours)

    candidates = history[signal_index + 1 :]
    if not candidates:
        return None

    future = min(
        candidates,
        key=lambda row: abs((_parse_timestamp(_observed_at(row)) - target).total_seconds()),
    )
    delta_hours = abs((_parse_timestamp(_observed_at(future)) - target).total_seconds()) / 3600.0
    if delta_hours > tolerance_hours:
        return None
    return future


def qualifies_strategy(
    signal: MarketSignal,
    strategy: str,
    min_pressure: float,
) -> bool:
    if strategy == "pressure":
        return signal.pressure_score >= min_pressure
    if strategy == "prebreak":
        return (
            -5.0 <= signal.price_change_pct <= 10.0
            and -5.0 <= signal.baseline_price_change_pct <= 15.0
            and signal.baseline_quantity_change_pct <= -10.0
            and signal.baseline_depth_5_change_pct <= -30.0
            and signal.persistence_ratio >= 0.35
        )
    raise ValueError(f"Unknown strategy: {strategy}")


def backtest_history(
    history: list[dict],
    history_window: int = 5,
    horizons: tuple[int, ...] = (3, 6, 12, 24),
    strategy: str = "pressure",
    min_pressure: float = 30.0,
    min_quantity: int = 100,
    min_market_value_g: int = 10_000,
    min_listings: int = 50,
    min_price_levels: int = 5,
    cooldown_hours: float = 6.0,
) -> tuple[list[MarketSignal], list[ForwardResult], list[ForwardResult]]:
    """Return qualifying signals, their forward returns, and all eligible-market returns.

    The baseline uses every market observation that met the exact same liquidity
    rules at that historical moment, regardless of Pressure. This lets us ask
    whether Pressure adds predictive information beyond the market's own drift.
    """
    signals: list[MarketSignal] = []
    signal_results: list[ForwardResult] = []
    baseline_results: list[ForwardResult] = []
    in_event = False
    last_event_at: datetime | None = None

    if len(history) < history_window:
        return signals, signal_results, baseline_results

    for idx in range(history_window - 1, len(history)):
        window = history[idx - history_window + 1 : idx + 1]
        current = window[-1]

        if int(current.get("total_quantity") or 0) < min_quantity:
            continue
        if int(current.get("approx_market_value") or 0) < min_market_value_g * 10_000:
            continue
        if int(current.get("listing_count") or 0) < min_listings:
            continue
        if int(current.get("price_level_count") or 0) < min_price_levels:
            continue

        signal = signal_from_history(window)
        qualifies = qualifies_strategy(signal, strategy, min_pressure)
        signal_time = _parse_timestamp(signal.current_at)

        cooldown_ok = (
            last_event_at is None
            or (signal_time - last_event_at).total_seconds() / 3600.0 >= cooldown_hours
        )
        new_event = qualifies and not in_event and cooldown_ok

        if new_event:
            signals.append(signal)
            last_event_at = signal_time

        for horizon in horizons:
            future = _future_row(history, idx, horizon)
            if future is None:
                continue
            future_price = _reference_price(future)
            result = ForwardResult(
                item_id=signal.item_id,
                signal_at=signal.current_at,
                pressure=signal.pressure_score,
                reference_price=signal.reference_price,
                horizon_hours=horizon,
                future_at=_observed_at(future),
                future_price=future_price,
                forward_return_pct=_pct_change(future_price, signal.reference_price),
            )
            baseline_results.append(result)
            if new_event:
                signal_results.append(result)

        in_event = qualifies

    return signals, signal_results, baseline_results


def summarize_results(results: list[ForwardResult]) -> list[HorizonSummary]:
    summaries: list[HorizonSummary] = []
    horizons = sorted({result.horizon_hours for result in results})

    for horizon in horizons:
        values = [
            result.forward_return_pct
            for result in results
            if result.horizon_hours == horizon
        ]
        if not values:
            continue
        summaries.append(
            HorizonSummary(
                horizon_hours=horizon,
                samples=len(values),
                average_return_pct=mean(values),
                median_return_pct=median(values),
                positive_rate_pct=sum(v > 0 for v in values) / len(values) * 100.0,
                return_5pct_rate_pct=sum(v >= 5 for v in values) / len(values) * 100.0,
                return_10pct_rate_pct=sum(v >= 10 for v in values) / len(values) * 100.0,
            )
        )
    return summaries


def result_row(result: ForwardResult) -> dict:
    return asdict(result)
