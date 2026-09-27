from pathlib import Path

from azeroth_capital.collector import Collector
from azeroth_capital.storage import Storage


class FakeClient:
    def __init__(self):
        self.payload = {
            "auctions": [
                {"id": 1, "item": {"id": 42}, "unit_price": 100, "quantity": 5},
                {"id": 2, "item": {"id": 42}, "unit_price": 105, "quantity": 10},
            ]
        }

    def commodities(self):
        return self.payload

    def realm_auctions(self, connected_realm_id: int):
        return {
            "auctions": [
                {
                    "id": 99,
                    "item": {"id": 77},
                    "quantity": 1,
                    "buyout": 50000,
                    "time_left": "LONG",
                }
            ]
        }


def test_repeated_commodity_collection_records_two_polls(tmp_path: Path):
    storage = Storage(tmp_path / "test.db", tmp_path / "raw")
    storage.init()
    collector = Collector(FakeClient(), storage, "us")

    first = collector.commodities()
    second = collector.commodities()

    info = storage.status()
    assert first.run_id != second.run_id
    assert first.payload_hash == second.payload_hash
    assert info["runs"] == 2
    assert info["raw_snapshots"] == 1
    assert info["observations"] == 2
    assert len(storage.latest_market_pairs()) == 1


def test_realm_collection_retains_listing(tmp_path: Path):
    storage = Storage(tmp_path / "test.db", tmp_path / "raw")
    storage.init()
    collector = Collector(FakeClient(), storage, "us")

    result = collector.realm(60)

    assert result.source == "realm:60"
    assert result.auctions == 1
    assert storage.status()["runs"] == 1
