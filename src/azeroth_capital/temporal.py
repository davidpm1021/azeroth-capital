from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median

from .timestamps import parse_timestamp as _parse_timestamp


@dataclass(frozen=True)
class MarketSignal:
    item_id: int
    current_at: str
    previous_at: str
    best_price: int
    reference_price: int
    total_quantity: int
    reference_depth_5pct: int
    approx_market_value: int
    listing_count: int
    price_level_count: int
    price_change_pct: float
    quantity_change_pct: float
    depth_5_change_pct: float
    baseline_price_change_pct: float
    baseline_quantity_change_pct: float
    baseline_depth_5_change_pct: float
    observed_depletion_per_hour: float
    depth_5_eta_hours: float | None
    floor_gap_pct: float
    tightening_intervals: int
    interval_count: int
    persistence_ratio: float
    pressure_score: float


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return ((current - previous) / previous) * 100.0


def _observed_at(row: dict) -> str:
    return row.get("observed_at") or row.get("source_modified_at") or row["started_at"]


def _reference_price(row: dict) -> int:
    return int(row.get("reference_price") or row["best_price"])


def _reference_depth(row: dict) -> int:
    return int(row.get("reference_depth_5pct") or row["depth_5pct"])


def signal_from_history(history: list[dict]) -> MarketSignal:
    if len(history) < 2:
        raise ValueError("At least two market snapshots are required")

    current = history[-1]
    previous = history[-2]
    prior = history[:-1]

    current_stamp = _observed_at(current)
    previous_stamp = _observed_at(previous)
    current_at = _parse_timestamp(current_stamp)
    previous_at = _parse_timestamp(previous_stamp)
    elapsed_hours = max((current_at - previous_at).total_seconds() / 3600.0, 1 / 3600)

    current_reference = _reference_price(current)
    previous_reference = _reference_price(previous)
    current_depth = _reference_depth(current)
    previous_depth = _reference_depth(previous)

    price_change = _pct_change(current_reference, previous_reference)
    quantity_change = _pct_change(current["total_quantity"], previous["total_quantity"])
    depth_change = _pct_change(current_depth, previous_depth)

    baseline_reference = median(_reference_price(row) for row in prior)
    baseline_quantity = median(int(row["total_quantity"]) for row in prior)
    baseline_depth = median(_reference_depth(row) for row in prior)

    baseline_price_change = _pct_change(current_reference, baseline_reference)
    baseline_quantity_change = _pct_change(current["total_quantity"], baseline_quantity)
    baseline_depth_change = _pct_change(current_depth, baseline_depth)

    depletion = max(float(previous["total_quantity"] - current["total_quantity"]), 0.0) / elapsed_hours
    depth_depletion = max(float(previous_depth - current_depth), 0.0) / elapsed_hours

    eta = None
    if depth_depletion > 0:
        candidate = current_depth / depth_depletion
        if isfinite(candidate) and candidate >= 0:
            eta = candidate

    tightening = 0
    for left, right in zip(history, history[1:]):
        left_price = _reference_price(left)
        right_price = _reference_price(right)
        left_depth = _reference_depth(left)
        right_depth = _reference_depth(right)
        if right_price >= left_price and right_depth < left_depth:
            tightening += 1

    interval_count = len(history) - 1
    persistence = tightening / interval_count if interval_count else 0.0

    best_price = int(current["best_price"])
    floor_gap = _pct_change(best_price, current_reference)

    # Bounded and persistence-aware. Latest movement matters, but repeated
    # tightening across several Blizzard snapshots matters more.
    persistence_evidence = min(interval_count / 4.0, 1.0)
    pressure = (
        min(max(price_change, 0.0), 100.0) * 0.30
        + min(max(baseline_price_change, 0.0), 100.0) * 0.35
        + min(max(-depth_change, 0.0), 100.0) * 0.20
        + min(max(-baseline_depth_change, 0.0), 100.0) * 0.25
        + min(max(-quantity_change, 0.0), 100.0) * 0.10
        + min(max(-baseline_quantity_change, 0.0), 100.0) * 0.10
        + persistence * 30.0 * persistence_evidence
    )

    return MarketSignal(
        item_id=int(current["item_id"]),
        current_at=current_stamp,
        previous_at=previous_stamp,
        best_price=best_price,
        reference_price=current_reference,
        total_quantity=int(current["total_quantity"]),
        reference_depth_5pct=current_depth,
        approx_market_value=int(
            current.get("approx_market_value")
            or (current_reference * int(current["total_quantity"]))
        ),
        listing_count=int(current.get("listing_count") or 0),
        price_level_count=int(current.get("price_level_count") or 0),
        price_change_pct=price_change,
        quantity_change_pct=quantity_change,
        depth_5_change_pct=depth_change,
        baseline_price_change_pct=baseline_price_change,
        baseline_quantity_change_pct=baseline_quantity_change,
        baseline_depth_5_change_pct=baseline_depth_change,
        observed_depletion_per_hour=depletion,
        depth_5_eta_hours=eta,
        floor_gap_pct=floor_gap,
        tightening_intervals=tightening,
        interval_count=interval_count,
        persistence_ratio=persistence,
        pressure_score=pressure,
    )


def signal_from_pair(current: dict, previous: dict) -> MarketSignal:
    return signal_from_history([previous, current])
