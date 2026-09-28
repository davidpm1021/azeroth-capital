from collections import defaultdict
from math import ceil
from typing import Iterable


REFERENCE_MIN_UNITS = 20
REFERENCE_FRACTION = 0.01


def commodity_levels(auctions: Iterable[dict]) -> list[dict]:
    quantities: dict[tuple[int, int], int] = defaultdict(int)
    listings: dict[tuple[int, int], int] = defaultdict(int)
    for auction in auctions:
        item_id = int(auction["item"]["id"])
        unit_price = int(auction["unit_price"])
        quantity = int(auction.get("quantity", 1))
        key = (item_id, unit_price)
        quantities[key] += quantity
        listings[key] += 1

    return [
        {
            "item_id": item_id,
            "unit_price": unit_price,
            "quantity": quantity,
            "listing_count": listings[(item_id, unit_price)],
        }
        for (item_id, unit_price), quantity in sorted(quantities.items())
    ]


def robust_market_fields(rows: list[tuple[int, int]]) -> dict:
    """Return bait-resistant price/depth fields for one commodity.

    The reference price is the price required to buy at least the greater of
    20 units or 1% of visible inventory. This prevents one tiny undercut from
    defining the entire market price.
    """
    rows = sorted(rows)
    total_quantity = sum(q for _, q in rows)
    if total_quantity <= 0:
        return {
            "reference_price": None,
            "reference_quantity": 0,
            "reference_depth_5pct": 0,
            "approx_market_value": 0,
        }

    target = min(
        total_quantity,
        max(REFERENCE_MIN_UNITS, ceil(total_quantity * REFERENCE_FRACTION)),
    )
    cumulative = 0
    reference_price = rows[-1][0]
    for price, quantity in rows:
        cumulative += quantity
        if cumulative >= target:
            reference_price = price
            break

    ceiling = reference_price * 1.05
    reference_depth_5pct = sum(q for p, q in rows if p <= ceiling)

    return {
        "reference_price": reference_price,
        "reference_quantity": target,
        "reference_depth_5pct": reference_depth_5pct,
        "approx_market_value": reference_price * total_quantity,
    }


def snapshot_metrics(levels: Iterable[dict]) -> list[dict]:
    grouped: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for row in levels:
        grouped[int(row["item_id"])].append(
            (
                int(row["unit_price"]),
                int(row["quantity"]),
                int(row.get("listing_count", 1)),
            )
        )

    result = []
    for item_id, rows in grouped.items():
        rows.sort()
        best = rows[0][0]
        total_quantity = sum(q for _, q, _ in rows)
        total_value = sum(p * q for p, q, _ in rows)
        listing_count = sum(n for _, _, n in rows)
        price_level_count = len(rows)

        def depth(percent: float) -> int:
            ceiling = best * (1 + percent)
            return sum(q for p, q, _ in rows if p <= ceiling)

        robust = robust_market_fields([(p, q) for p, q, _ in rows])
        result.append(
            {
                "item_id": item_id,
                "best_price": best,
                "quantity_at_best": sum(q for p, q, _ in rows if p == best),
                "listing_count": listing_count,
                "price_level_count": price_level_count,
                "total_quantity": total_quantity,
                "depth_1pct": depth(0.01),
                "depth_5pct": depth(0.05),
                "depth_10pct": depth(0.10),
                "weighted_price": total_value / total_quantity if total_quantity else None,
                **robust,
            }
        )
    return result
