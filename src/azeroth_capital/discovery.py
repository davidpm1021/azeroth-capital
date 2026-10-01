from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, median

from .timestamps import future_row, parse_timestamp as _parse_timestamp
from .temporal import signal_from_history


@dataclass(frozen=True)
class FeatureResult:
    feature: str
    horizon_hours: int
    slices: int
    markets: int
    top_return_pct: float
    bottom_return_pct: float
    spread_pct: float
    median_slice_spread_pct: float
    positive_spread_rate_pct: float
    average_rank_correlation: float


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
    index: int,
    horizon_hours: int,
    tolerance_hours: float = 1.5,
) -> dict | None:
    return future_row(history, _observed_at(history[index]), horizon_hours, tolerance_hours)


def _feature_values(signal) -> dict[str, float]:
    depth_compression = -signal.baseline_depth_5_change_pct
    quantity_compression = -signal.baseline_quantity_change_pct
    price_extension = signal.baseline_price_change_pct

    return {
        "price_1h": signal.price_change_pct,
        "price_vs_baseline": signal.baseline_price_change_pct,
        "quantity_vs_baseline": signal.baseline_quantity_change_pct,
        "depth_vs_baseline": signal.baseline_depth_5_change_pct,
        "persistence": signal.persistence_ratio * 100.0,
        "pressure": signal.pressure_score,
        "compression_gap": depth_compression - max(price_extension, 0.0),
        "supply_compression": quantity_compression,
        "depth_compression": depth_compression,
        "breadth_listings": float(signal.listing_count),
        "breadth_levels": float(signal.price_level_count),
    }


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j][1] == ordered[i][1]:
            j += 1
        average_rank = ((i + 1) + j) / 2.0
        for k in range(i, j):
            ranks[ordered[k][0]] = average_rank
        i = j
    return ranks


def _pearson(x: list[float], y: list[float]) -> float:
    if len(x) < 3 or len(y) != len(x):
        return 0.0
    mx = mean(x)
    my = mean(y)
    numerator = sum((a - mx) * (b - my) for a, b in zip(x, y))
    denom_x = sqrt(sum((a - mx) ** 2 for a in x))
    denom_y = sqrt(sum((b - my) ** 2 for b in y))
    if denom_x == 0 or denom_y == 0:
        return 0.0
    return numerator / (denom_x * denom_y)


def _spearman(feature_values: list[float], returns: list[float]) -> float:
    return _pearson(_average_ranks(feature_values), _average_ranks(returns))


def discover_features(
    histories: dict[tuple[int, int], list[dict]],
    item_ids: set[int] | None,
    history_window: int = 5,
    horizons: tuple[int, ...] = (3, 6, 12),
    min_quantity: int = 100,
    min_market_value_g: int = 10_000,
    min_listings: int = 50,
    min_price_levels: int = 5,
    quantile_fraction: float = 0.20,
) -> list[FeatureResult]:
    records: dict[tuple[str, int], dict[str, list[tuple[float, float]]]] = {}

    for (item_id, _), history in histories.items():
        if item_ids is not None and item_id not in item_ids:
            continue
        if len(history) < history_window:
            continue

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
            features = _feature_values(signal)
            timestamp = signal.current_at

            for horizon in horizons:
                future = _future_row(history, idx, horizon)
                if future is None:
                    continue
                future_return = _pct_change(
                    _reference_price(future),
                    signal.reference_price,
                )
                for feature_name, feature_value in features.items():
                    records.setdefault((feature_name, horizon), {}).setdefault(timestamp, []).append(
                        (feature_value, future_return)
                    )

    results: list[FeatureResult] = []
    for (feature_name, horizon), slices in records.items():
        slice_spreads: list[float] = []
        correlations: list[float] = []
        top_returns_all: list[float] = []
        bottom_returns_all: list[float] = []
        market_count = 0

        for observations in slices.values():
            if len(observations) < 10:
                continue
            ordered = sorted(observations, key=lambda pair: pair[0])
            bucket = max(1, int(len(ordered) * quantile_fraction))
            bottom = ordered[:bucket]
            top = ordered[-bucket:]

            bottom_returns = [ret for _, ret in bottom]
            top_returns = [ret for _, ret in top]
            spread = mean(top_returns) - mean(bottom_returns)

            slice_spreads.append(spread)
            top_returns_all.extend(top_returns)
            bottom_returns_all.extend(bottom_returns)
            market_count += len(observations)

            correlations.append(
                _spearman(
                    [feature for feature, _ in observations],
                    [ret for _, ret in observations],
                )
            )

        if not slice_spreads:
            continue

        results.append(
            FeatureResult(
                feature=feature_name,
                horizon_hours=horizon,
                slices=len(slice_spreads),
                markets=market_count,
                top_return_pct=mean(top_returns_all),
                bottom_return_pct=mean(bottom_returns_all),
                spread_pct=mean(slice_spreads),
                median_slice_spread_pct=median(slice_spreads),
                positive_spread_rate_pct=sum(v > 0 for v in slice_spreads) / len(slice_spreads) * 100.0,
                average_rank_correlation=mean(correlations) if correlations else 0.0,
            )
        )

    return sorted(
        results,
        key=lambda row: (
            row.horizon_hours,
            -abs(row.spread_pct),
        ),
    )
