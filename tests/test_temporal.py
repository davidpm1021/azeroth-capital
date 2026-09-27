from azeroth_capital.temporal import signal_from_history, signal_from_pair


def test_pressure_signal_detects_tightening_market():
    previous = {
        "item_id": 42,
        "started_at": "2026-09-27T12:00:00+00:00",
        "best_price": 100,
        "total_quantity": 1000,
        "depth_5pct": 500,
    }
    current = {
        "item_id": 42,
        "started_at": "2026-09-27T13:00:00+00:00",
        "best_price": 110,
        "total_quantity": 800,
        "depth_5pct": 300,
    }

    signal = signal_from_pair(current, previous)

    assert signal.price_change_pct == 10
    assert signal.quantity_change_pct == -20
    assert signal.depth_5_change_pct == -40
    assert signal.observed_depletion_per_hour == 200
    assert signal.depth_5_eta_hours == 1.5
    assert signal.pressure_score > 0


def test_pressure_uses_reference_price_not_bait_floor():
    previous = {
        "item_id": 42,
        "started_at": "2026-09-27T12:00:00+00:00",
        "best_price": 1,
        "reference_price": 10_000,
        "total_quantity": 1000,
        "depth_5pct": 1,
        "reference_depth_5pct": 500,
        "approx_market_value": 10_000_000,
    }
    current = {
        "item_id": 42,
        "started_at": "2026-09-27T13:00:00+00:00",
        "best_price": 10_000,
        "reference_price": 10_100,
        "total_quantity": 990,
        "depth_5pct": 490,
        "reference_depth_5pct": 490,
        "approx_market_value": 9_999_000,
    }

    signal = signal_from_pair(current, previous)

    assert round(signal.price_change_pct, 1) == 1.0
    assert signal.pressure_score < 10


def test_multi_snapshot_signal_rewards_persistent_tightening():
    history = [
        {
            "item_id": 42, "started_at": "2026-09-27T10:00:00+00:00",
            "best_price": 100, "reference_price": 100,
            "total_quantity": 1000, "depth_5pct": 500, "reference_depth_5pct": 500,
            "approx_market_value": 100000,
        },
        {
            "item_id": 42, "started_at": "2026-09-27T11:00:00+00:00",
            "best_price": 100, "reference_price": 100,
            "total_quantity": 900, "depth_5pct": 420, "reference_depth_5pct": 420,
            "approx_market_value": 90000,
        },
        {
            "item_id": 42, "started_at": "2026-09-27T12:00:00+00:00",
            "best_price": 105, "reference_price": 105,
            "total_quantity": 800, "depth_5pct": 340, "reference_depth_5pct": 340,
            "approx_market_value": 84000,
        },
        {
            "item_id": 42, "started_at": "2026-09-27T13:00:00+00:00",
            "best_price": 110, "reference_price": 110,
            "total_quantity": 700, "depth_5pct": 260, "reference_depth_5pct": 260,
            "approx_market_value": 77000,
        },
        {
            "item_id": 42, "started_at": "2026-09-27T14:00:00+00:00",
            "best_price": 120, "reference_price": 120,
            "total_quantity": 600, "depth_5pct": 180, "reference_depth_5pct": 180,
            "approx_market_value": 72000,
        },
    ]

    signal = signal_from_history(history)

    assert signal.tightening_intervals == 4
    assert signal.interval_count == 4
    assert signal.persistence_ratio == 1.0
    assert signal.baseline_price_change_pct > 0
    assert signal.baseline_quantity_change_pct < 0
    assert signal.baseline_depth_5_change_pct < 0
