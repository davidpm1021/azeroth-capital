from pathlib import Path

from azeroth_capital.storage import Storage, payload_hash


def test_http_dates_order_across_weekday_and_month_boundaries(tmp_path):
    storage = Storage(tmp_path / "test.db", tmp_path / "raw")
    storage.init()
    times = ["Sun, 27 Sep 2026 23:00:00 GMT", "Mon, 28 Sep 2026 00:00:00 GMT",
             "Wed, 30 Sep 2026 23:00:00 GMT", "Thu, 01 Oct 2026 00:00:00 GMT"]
    # Insert out of order so run IDs cannot accidentally substitute for time.
    for index in [2, 0, 3, 1]:
        run = storage.begin_run("us", "commodities", str(index), tmp_path / str(index), times[index])
        storage.insert_observations(run, [{"item_id": 42, "best_price": 100,
            "total_quantity": 1000, "quantity_at_best": 100, "depth_1pct": 100,
            "depth_5pct": 200, "depth_10pct": 300, "weighted_price": 100.0}], "commodity")
        storage.finish_run(run)
        storage.insert_paper_signals([dict(strategy="test", observed_at=times[index], item_id=42,
                                          feature_value=1, percentile=1, rank=1, universe_size=5, entry_price=100)])
    assert [r["observed_at"] for r in storage.all_market_histories()[(42, 0)]] == times
    assert [r["observed_at"] for r in storage.market_histories(snapshots=2)[(42, 0)]] == times[-2:]
    assert [r["observed_at"] for r in storage.paper_signals()] == times
    assert storage.paper_status()["first_at"] == times[0]
    assert storage.paper_status()["last_at"] == times[-1]
    import csv
    output = tmp_path / "history.csv"
    storage.export_observations(output)
    with output.open() as f:
        assert [r["observed_at"] for r in csv.DictReader(f)] == times


def test_repeated_poll_keeps_time_series_but_reuses_raw_blob(tmp_path: Path):
    storage = Storage(tmp_path / "test.db", tmp_path / "raw")
    storage.init()
    payload = {"auctions": [{"id": 1}]}
    digest = payload_hash(payload)

    raw1 = storage.save_raw("commodities", digest, payload)
    run1 = storage.begin_run("us", "commodities", digest, raw1)
    storage.finish_run(run1)

    raw2 = storage.save_raw("commodities", digest, payload)
    run2 = storage.begin_run("us", "commodities", digest, raw2)
    storage.finish_run(run2)

    info = storage.status()
    assert raw1 == raw2
    assert run1 != run2
    assert info["runs"] == 2
    assert info["raw_snapshots"] == 1


def test_item_cache_round_trip(tmp_path: Path):
    storage = Storage(tmp_path / "test.db", tmp_path / "raw")
    storage.init()
    storage.upsert_item(
        42,
        {
            "name": "Test Herb",
            "quality": {"name": "Common"},
            "item_class": {"name": "Tradeskill"},
            "item_subclass": {"name": "Herb"},
        },
    )

    item = storage.get_item(42)
    assert item is not None
    assert item["name"] == "Test Herb"
    assert item["item_subclass"] == "Herb"


def test_commodity_book_requires_exact_time_and_successful_commodity_source(tmp_path):
    storage = Storage(tmp_path/'test.db', tmp_path/'raw')
    storage.init()
    stamp = 'Thu, 01 Oct 2026 01:00:00 GMT'
    for source, time, error, price in [
        ('commodities', stamp, 'failed', 1),
        ('realm:1', stamp, None, 2),
        ('commodities', stamp, None, 100),
        ('commodities', '2026-10-01T01:00:00+00:00', None, 200),
        ('commodities', '2026-10-01T02:00:00+00:00', None, 300),
    ]:
        run = storage.begin_run('us', source, str(price), tmp_path/str(price), time)
        storage.insert_commodity_levels(run, [dict(item_id=42, unit_price=price, quantity=17)])
        storage.finish_run(run, error)
    assert storage.commodity_book(42, '2026-10-01T01:00:00+00:00') == [(100,17)]
    assert storage.commodity_book(42, '2026-10-01T00:59:59+00:00') == []
    assert storage.commodity_book(43, stamp) == []
