from collections import defaultdict
from typing import Iterable


def commodity_levels(auctions: Iterable[dict]) -> list[dict]:
    levels: dict[tuple[int, int], int] = defaultdict(int)
    for auction in auctions:
        item_id = int(auction["item"]["id"])
        unit_price = int(auction["unit_price"])
        quantity = int(auction.get("quantity", 1))
        levels[(item_id, unit_price)] += quantity

    return [
        {"item_id": item_id, "unit_price": unit_price, "quantity": quantity}
        for (item_id, unit_price), quantity in sorted(levels.items())
    ]


def snapshot_metrics(levels: Iterable[dict]) -> list[dict]:
    grouped: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for row in levels:
        grouped[int(row["item_id"])].append((int(row["unit_price"]), int(row["quantity"])))

    result = []
    for item_id, rows in grouped.items():
        rows.sort()
        best = rows[0][0]
        total_quantity = sum(q for _, q in rows)
        total_value = sum(p * q for p, q in rows)

        def depth(percent: float) -> int:
            ceiling = best * (1 + percent)
            return sum(q for p, q in rows if p <= ceiling)

        result.append(
            {
                "item_id": item_id,
                "best_price": best,
                "quantity_at_best": sum(q for p, q in rows if p == best),
                "total_quantity": total_quantity,
                "depth_1pct": depth(0.01),
                "depth_5pct": depth(0.05),
                "depth_10pct": depth(0.10),
                "weighted_price": total_value / total_quantity if total_quantity else None,
            }
        )
    return result
