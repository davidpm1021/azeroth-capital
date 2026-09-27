from dataclasses import dataclass

from .blizzard import BlizzardClient
from .metrics import commodity_levels, snapshot_metrics
from .storage import Storage, payload_hash


@dataclass
class CollectionResult:
    source: str
    run_id: int
    auctions: int
    observations: int
    payload_hash: str


class Collector:
    def __init__(self, client: BlizzardClient, storage: Storage, region: str):
        self.client = client
        self.storage = storage
        self.region = region

    def _prepare(self, source: str, payload: dict) -> tuple[str, int]:
        digest = payload_hash(payload)
        raw_path = self.storage.save_raw(source, digest, payload)
        run_id = self.storage.begin_run(self.region, source, digest, raw_path)
        return digest, run_id

    def commodities(self) -> CollectionResult:
        source = "commodities"
        payload = self.client.commodities()
        digest, run_id = self._prepare(source, payload)

        try:
            levels = commodity_levels(payload.get("auctions", []))
            metrics = snapshot_metrics(levels)
            self.storage.insert_commodity_levels(run_id, levels)
            self.storage.insert_observations(run_id, metrics, "commodity")
            self.storage.finish_run(run_id)
            return CollectionResult(
                source=source,
                run_id=run_id,
                auctions=len(payload.get("auctions", [])),
                observations=len(metrics),
                payload_hash=digest,
            )
        except Exception as exc:
            self.storage.finish_run(run_id, str(exc))
            raise

    def realm(self, connected_realm_id: int) -> CollectionResult:
        source = f"realm:{connected_realm_id}"
        payload = self.client.realm_auctions(connected_realm_id)
        digest, run_id = self._prepare(source, payload)

        try:
            auctions = payload.get("auctions", [])
            self.storage.insert_realm_auctions(run_id, connected_realm_id, auctions)
            self.storage.finish_run(run_id)
            return CollectionResult(
                source=source,
                run_id=run_id,
                auctions=len(auctions),
                observations=0,
                payload_hash=digest,
            )
        except Exception as exc:
            self.storage.finish_run(run_id, str(exc))
            raise
