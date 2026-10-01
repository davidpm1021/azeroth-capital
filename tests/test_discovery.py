from datetime import UTC, datetime, timedelta

from azeroth_capital.discovery import discover_features


def _history(item_id: int, hours: int, price_slope: int, depth_slope: int) -> list[dict]:
    start = datetime(2026, 9, 27, 0, 0, tzinfo=UTC)
    rows = []
    for hour in range(hours):
        observed = start + timedelta(hours=hour)
        price = 10_000 + hour * price_slope
        quantity = 2_000 - hour * 10
        depth = max(1_000 + hour * depth_slope, 100)
        rows.append(
            {
                "item_id": item_id,
                "started_at": observed.isoformat(),
                "observed_at": observed.isoformat(),
                "best_price": price,
                "reference_price": price,
                "total_quantity": quantity,
                "depth_5pct": depth,
                "reference_depth_5pct": depth,
                "approx_market_value": price * quantity,
                "listing_count": 100,
                "price_level_count": 20,
            }
        )
    return rows


def test_discovery_finds_directional_feature_spread():
    histories = {}
    for item_id in range(20):
        # Higher item IDs have stronger positive momentum and better future returns.
        slope = item_id * 10
        histories[(item_id, 0)] = _history(
            item_id=item_id,
            hours=20,
            price_slope=slope,
            depth_slope=-2,
        )

    results = discover_features(
        histories,
        item_ids=None,
        history_window=5,
        horizons=(3,),
        min_quantity=0,
        min_market_value_g=0,
        min_listings=0,
        min_price_levels=0,
    )

    price_result = next(
        row for row in results
        if row.feature == "price_1h" and row.horizon_hours == 3
    )
    assert price_result.slices > 0
    assert price_result.spread_pct > 0
    assert price_result.average_rank_correlation > 0
