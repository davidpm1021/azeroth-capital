from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite


@dataclass(frozen=True)
class MarketSignal:
    item_id: int
    current_at: str
    previous_at: str
    best_price: int
    total_quantity: int
    depth_5pct: int
    price_change_pct: float
    quantity_change_pct: float
    depth_5_change_pct: float
    observed_depletion_per_hour: float
    depth_5_eta_hours: float | None
    pressure_score: float


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return ((current - previous) / previous) * 100.0


def _observed_at(row: dict) -> str:
    return row.get("observed_at") or row.get("source_modified_at") or row["started_at"]


def signal_from_pair(current: dict, previous: dict) -> MarketSignal:
    current_stamp = _observed_at(current)
    previous_stamp = _observed_at(previous)
    current_at = datetime.fromisoformat(current_stamp)
    previous_at = datetime.fromisoformat(previous_stamp)
    elapsed_hours = max((current_at - previous_at).total_seconds() / 3600.0, 1 / 3600)

    price_change = _pct_change(current["best_price"], previous["best_price"])
    quantity_change = _pct_change(current["total_quantity"], previous["total_quantity"])
    depth_change = _pct_change(current["depth_5pct"], previous["depth_5pct"])

    depletion = max(float(previous["total_quantity"] - current["total_quantity"]), 0.0) / elapsed_hours
    depth_depletion = max(float(previous["depth_5pct"] - current["depth_5pct"]), 0.0) / elapsed_hours

    eta = None
    if depth_depletion > 0:
        candidate = current["depth_5pct"] / depth_depletion
        if isfinite(candidate) and candidate >= 0:
            eta = candidate

    pressure = (
        max(price_change, 0.0) * 1.5
        + max(-quantity_change, 0.0) * 0.4
        + max(-depth_change, 0.0) * 0.8
    )

    return MarketSignal(
        item_id=int(current["item_id"]),
        current_at=current_stamp,
        previous_at=previous_stamp,
        best_price=int(current["best_price"]),
        total_quantity=int(current["total_quantity"]),
        depth_5pct=int(current["depth_5pct"]),
        price_change_pct=price_change,
        quantity_change_pct=quantity_change,
        depth_5_change_pct=depth_change,
        observed_depletion_per_hour=depletion,
        depth_5_eta_hours=eta,
        pressure_score=pressure,
    )
