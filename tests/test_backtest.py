from datetime import UTC, datetime, timedelta

from azeroth_capital.backtest import backtest_history, summarize_results


def _row(hour: int) -> dict:
    start = datetime(2026, 9, 27, 0, 0, tzinfo=UTC)
    observed = start + timedelta(hours=hour)

    if hour < 5:
        price = 10_000
    else:
        price = 10_000 + (hour - 4) * 500

    quantity = max(2_000 - hour * 40, 500)
    depth = max(1_000 - hour * 35, 150)

    return {
        "item_id": 42,
        "started_at": observed.isoformat(),
        "observed_at": observed.isoformat(),
        "best_price": price,
        "reference_price": price,
        "total_quantity": quantity,
        "depth_5pct": depth,
        "reference_depth_5pct": depth,
        "approx_market_value": price * quantity,
        "listing_count": 200,
        "price_level_count": 25,
    }


def test_backtest_generates_timestamp_aware_forward_returns():
    history = [_row(hour) for hour in range(30)]

    signals, results, baseline_results = backtest_history(
        history,
        history_window=5,
        strategy="pressure",
        min_pressure=0,
        min_quantity=0,
        min_market_value_g=0,
        min_listings=0,
        min_price_levels=0,
    )

    assert signals
    assert results
    assert baseline_results
    assert len(baseline_results) >= len(results)
    assert {result.horizon_hours for result in results} == {3, 6, 12, 24}

    first_3h = next(
        result
        for result in results
        if result.signal_at == history[4]["observed_at"]
        and result.horizon_hours == 3
    )
    assert first_3h.future_at == history[7]["observed_at"]
    assert first_3h.forward_return_pct > 0


def test_backtest_summary_reports_positive_hit_rate():
    history = [_row(hour) for hour in range(30)]

    _, results, baseline_results = backtest_history(
        history,
        history_window=5,
        strategy="pressure",
        min_pressure=0,
        min_quantity=0,
        min_market_value_g=0,
        min_listings=0,
        min_price_levels=0,
    )
    summaries = summarize_results(results)
    baseline_summaries = summarize_results(baseline_results)

    summary_6h = next(summary for summary in summaries if summary.horizon_hours == 6)
    baseline_6h = next(
        summary for summary in baseline_summaries if summary.horizon_hours == 6
    )
    assert summary_6h.samples > 0
    assert summary_6h.average_return_pct > 0
    assert summary_6h.positive_rate_pct > 0
    assert baseline_6h.samples >= summary_6h.samples


def test_prebreak_requires_supply_compression_without_large_price_extension():
    history = []
    start = datetime(2026, 9, 27, 0, 0, tzinfo=UTC)
    prices = [10_000, 10_000, 10_100, 10_100, 10_200, 10_300, 10_400]
    quantities = [2000, 1900, 1750, 1600, 1450, 1300, 1150]
    depths = [1000, 900, 760, 620, 480, 350, 250]

    for hour, (price, quantity, depth) in enumerate(zip(prices, quantities, depths)):
        observed = start + timedelta(hours=hour)
        history.append({
            "item_id": 77,
            "started_at": observed.isoformat(),
            "observed_at": observed.isoformat(),
            "best_price": price,
            "reference_price": price,
            "total_quantity": quantity,
            "depth_5pct": depth,
            "reference_depth_5pct": depth,
            "approx_market_value": price * quantity,
            "listing_count": 200,
            "price_level_count": 25,
        })

    signals, _, _ = backtest_history(
        history,
        history_window=5,
        strategy="prebreak",
        min_quantity=0,
        min_market_value_g=0,
        min_listings=0,
        min_price_levels=0,
        cooldown_hours=6,
    )

    assert len(signals) == 1
    assert signals[0].baseline_quantity_change_pct <= -10
    assert signals[0].baseline_depth_5_change_pct <= -30


def test_contiguous_pressure_run_counts_as_one_event():
    history = [_row(hour) for hour in range(20)]

    signals, _, _ = backtest_history(
        history,
        history_window=5,
        strategy="pressure",
        min_pressure=0,
        min_quantity=0,
        min_market_value_g=0,
        min_listings=0,
        min_price_levels=0,
        cooldown_hours=6,
    )

    assert len(signals) == 1
