from __future__ import annotations

import shutil
from pathlib import Path

from .blizzard import ApiDocument
from .collector import Collector
from .report import build_report
from .storage import Storage


class DemoClient:
    def __init__(self):
        self.documents = [
            ApiDocument(
                data={
                    "auctions": [
                        {"id": 1, "item": {"id": 1001}, "unit_price": 100_000, "quantity": 500},
                        {"id": 2, "item": {"id": 1001}, "unit_price": 102_000, "quantity": 300},
                        {"id": 3, "item": {"id": 1001}, "unit_price": 120_000, "quantity": 2000},
                        {"id": 4, "item": {"id": 1002}, "unit_price": 80_000, "quantity": 1000},
                        {"id": 5, "item": {"id": 1002}, "unit_price": 82_000, "quantity": 500},
                    ]
                },
                last_modified="Sun, 27 Sep 2026 16:00:00 GMT",
            ),
            ApiDocument(
                data={
                    "auctions": [
                        {"id": 6, "item": {"id": 1001}, "unit_price": 100_000, "quantity": 220},
                        {"id": 7, "item": {"id": 1001}, "unit_price": 102_000, "quantity": 130},
                        {"id": 8, "item": {"id": 1001}, "unit_price": 120_000, "quantity": 2000},
                        {"id": 9, "item": {"id": 1002}, "unit_price": 80_000, "quantity": 980},
                        {"id": 10, "item": {"id": 1002}, "unit_price": 82_000, "quantity": 520},
                    ]
                },
                last_modified="Sun, 27 Sep 2026 17:00:00 GMT",
            ),
            ApiDocument(
                data={
                    "auctions": [
                        {"id": 11, "item": {"id": 1001}, "unit_price": 120_000, "quantity": 1900},
                        {"id": 12, "item": {"id": 1001}, "unit_price": 125_000, "quantity": 500},
                        {"id": 13, "item": {"id": 1002}, "unit_price": 80_000, "quantity": 970},
                        {"id": 14, "item": {"id": 1002}, "unit_price": 82_000, "quantity": 530},
                    ]
                },
                last_modified="Sun, 27 Sep 2026 18:00:00 GMT",
            ),
        ]
        self.index = 0

    def commodities(self, if_modified_since: str | None = None) -> ApiDocument:
        document = self.documents[self.index]
        self.index += 1
        return document


def create_demo(root: Path, reset: bool = True) -> tuple[Storage, Path]:
    if reset and root.exists():
        shutil.rmtree(root)

    storage = Storage(root / "azeroth_capital_demo.db", root / "raw")
    storage.init()
    client = DemoClient()
    collector = Collector(client, storage, "us")

    for _ in range(3):
        collector.commodities()

    storage.upsert_item(
        1001,
        {
            "name": "Demo Volatile Reagent",
            "quality": {"name": "Rare"},
            "item_class": {"name": "Tradeskill"},
            "item_subclass": {"name": "Other"},
        },
    )
    storage.upsert_item(
        1002,
        {
            "name": "Demo Stable Reagent",
            "quality": {"name": "Common"},
            "item_class": {"name": "Tradeskill"},
            "item_subclass": {"name": "Other"},
        },
    )

    report_path = build_report(storage, root / "report.html")
    return storage, report_path
