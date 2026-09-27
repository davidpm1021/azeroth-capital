from dataclasses import dataclass

from .blizzard import BlizzardClient
from .metrics import commodity_levels, snapshot_metrics
from .storage import Storage, payload_hash


@dataclass
class CollectionResult:
    source: str
    run_id: int | None
    skipped: bool
    auctions: int
    observations: int


class Collector:
    def __init__(self, client: BlizzardClient, storage: Storage, region: str):
        self.client = client
        self.storage = storage
        self.region = region

    def _prepare(self, source: str, payload: dict) -> tuple[str, int | None]:
        digest = payload_hash(payload)
        if self.storage.has_payload(source, digest):
            return digest, None
        raw_path = self.storage.save_raw(source, digest, payload)
        run_id = self.storage.begin_run(self.region, source, digest, raw_path)
        return digest, run_id

    def commodities(self) -> CollectionResult:
        source = "commodities"
        payload = self.client.commodities()
        _, run_id = self._prepare(source, payload)
        if run_id is None:
            return CollectionResult(source, None, True, len(payload.get("auctions", [])), 0)

        try:
            levels = commodity_levels(payload.get("auctions", []))
            metrics = snapshot_metrics(levels)
            self.storage.insert_commodity_levels(run_id, levels)
            self.storage.insert_observations(run_id, metrics, "commodity")
            self.storage.finish_run(run_id)
            return CollectionResult(source, run_id, False, len(payload.get("auctions", [])), len(metrics))
        except Exception as exc:
            self.storage.finish_run(run_id, str(exc))
            raise

    def realm(self, connected_realm_id: int) -> CollectionResult:
        source = f"realm:{connected_realm_id}"
        payload = self.client.realm_auctions(connected_realm_id)
        _, run_id = self._prepare(source, payload)
        if run_id is None:
            return CollectionResult(source, None, True, len(payload.get("auctions", [])), 0)

        try:
            auctions = payload.get("auctions", [])
            self.storage.insert_realm_auctions(run_id, connected_realm_id, auctions)
            self.storage.finish_run(run_id)
            return CollectionResult(source, run_id, False, len(auctions), 0)
        except Exception as exc:
            self.storage.finish_run(run_id, str(exc))
            raise
