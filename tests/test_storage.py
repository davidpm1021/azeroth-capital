from pathlib import Path

from azeroth_capital.storage import Storage, payload_hash


def test_successful_payload_is_idempotent(tmp_path: Path):
    storage = Storage(tmp_path / "test.db", tmp_path / "raw")
    storage.init()
    payload = {"auctions": [{"id": 1}]}
    digest = payload_hash(payload)
    raw = storage.save_raw("commodities", digest, payload)
    run_id = storage.begin_run("us", "commodities", digest, raw)
    storage.finish_run(run_id)

    assert storage.has_payload("commodities", digest)
