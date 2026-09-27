from pathlib import Path

from azeroth_capital.blizzard import ApiDocument
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
        self.last_modified = "Sun, 27 Sep 2026 16:00:00 GMT"

    def commodities(self, if_modified_since: str | None = None):
        if if_modified_since == self.last_modified:
            return ApiDocument(None, self.last_modified, not_modified=True)
        return ApiDocument(self.payload, self.last_modified)

    def realm_auctions(
        self,
        connected_realm_id: int,
        if_modified_since: str | None = None,
    ):
        return ApiDocument(
            {
                "auctions": [
                    {
                        "id": 99,
                        "item": {"id": 77},
                        "quantity": 1,
                        "buyout": 50000,
                        "time_left": "LONG",
                    }
                ]
            },
            "Sun, 27 Sep 2026 16:00:00 GMT",
        )


def test_repeated_commodity_collection_uses_conditional_get(tmp_path: Path):
    storage = Storage(tmp_path / "test.db", tmp_path / "raw")
    storage.init()
    collector = Collector(FakeClient(), storage, "us")

    first = collector.commodities()
    second = collector.commodities()

    info = storage.status()
    assert first.run_id is not None
    assert second.not_modified is True
    assert second.run_id is None
    assert info["runs"] == 1
    assert info["raw_snapshots"] == 1
    assert info["observations"] == 1
    assert storage.last_modified_for_source("commodities") == "Sun, 27 Sep 2026 16:00:00 GMT"


def test_realm_collection_retains_listing(tmp_path: Path):
    storage = Storage(tmp_path / "test.db", tmp_path / "raw")
    storage.init()
    collector = Collector(FakeClient(), storage, "us")

    result = collector.realm(60)

    assert result.source == "realm:60"
    assert result.auctions == 1
    assert storage.status()["runs"] == 1
