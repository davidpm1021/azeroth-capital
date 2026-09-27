from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from math import isfinite


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
    price_change_pct: float
    quantity_change_pct: float
    depth_5_change_pct: float
    observed_depletion_per_hour: float
    depth_5_eta_hours: float | None
    floor_gap_pct: float
    pressure_score: float


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return ((current - previous) / previous) * 100.0


def _observed_at(row: dict) -> str:
    return row.get("observed_at") or row.get("source_modified_at") or row["started_at"]


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return parsedate_to_datetime(value)


def signal_from_pair(current: dict, previous: dict) -> MarketSignal:
    current_stamp = _observed_at(current)
    previous_stamp = _observed_at(previous)
    current_at = _parse_timestamp(current_stamp)
    previous_at = _parse_timestamp(previous_stamp)
    elapsed_hours = max((current_at - previous_at).total_seconds() / 3600.0, 1 / 3600)

    current_reference = int(current.get("reference_price") or current["best_price"])
    previous_reference = int(previous.get("reference_price") or previous["best_price"])
    current_depth = int(current.get("reference_depth_5pct") or current["depth_5pct"])
    previous_depth = int(previous.get("reference_depth_5pct") or previous["depth_5pct"])

    price_change = _pct_change(current_reference, previous_reference)
    quantity_change = _pct_change(current["total_quantity"], previous["total_quantity"])
    depth_change = _pct_change(current_depth, previous_depth)

    depletion = max(float(previous["total_quantity"] - current["total_quantity"]), 0.0) / elapsed_hours
    depth_depletion = max(float(previous_depth - current_depth), 0.0) / elapsed_hours

    eta = None
    if depth_depletion > 0:
        candidate = current_depth / depth_depletion
        if isfinite(candidate) and candidate >= 0:
            eta = candidate

    best_price = int(current["best_price"])
    floor_gap = _pct_change(best_price, current_reference)

    # Bounded heuristic. A 100x move should attract attention, but it should not
    # numerically overwhelm every other market signal.
    price_component = min(max(price_change, 0.0), 100.0) * 0.8
    quantity_component = min(max(-quantity_change, 0.0), 100.0) * 0.25
    depth_component = min(max(-depth_change, 0.0), 100.0) * 0.6
    pressure = price_component + quantity_component + depth_component

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
        price_change_pct=price_change,
        quantity_change_pct=quantity_change,
        depth_5_change_pct=depth_change,
        observed_depletion_per_hour=depletion,
        depth_5_eta_hours=eta,
        floor_gap_pct=floor_gap,
        pressure_score=pressure,
    )
