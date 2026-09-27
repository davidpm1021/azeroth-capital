from azeroth_capital.metrics import commodity_levels, snapshot_metrics


def test_commodity_levels_aggregate_same_price():
    auctions = [
        {"item": {"id": 42}, "unit_price": 100, "quantity": 2},
        {"item": {"id": 42}, "unit_price": 100, "quantity": 3},
        {"item": {"id": 42}, "unit_price": 110, "quantity": 5},
    ]
    assert commodity_levels(auctions) == [
        {"item_id": 42, "unit_price": 100, "quantity": 5},
        {"item_id": 42, "unit_price": 110, "quantity": 5},
    ]


def test_snapshot_depth_metrics():
    levels = [
        {"item_id": 42, "unit_price": 100, "quantity": 5},
        {"item_id": 42, "unit_price": 105, "quantity": 10},
        {"item_id": 42, "unit_price": 120, "quantity": 20},
    ]
    metric = snapshot_metrics(levels)[0]
    assert metric["best_price"] == 100
    assert metric["quantity_at_best"] == 5
    assert metric["total_quantity"] == 35
    assert metric["depth_1pct"] == 5
    assert metric["depth_5pct"] == 15
    assert metric["depth_10pct"] == 15
