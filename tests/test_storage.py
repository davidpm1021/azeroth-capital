from pathlib import Path

from azeroth_capital.storage import Storage, payload_hash


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
