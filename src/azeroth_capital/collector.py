from dataclasses import dataclass

from .blizzard import ApiDocument, BlizzardClient
from .metrics import commodity_levels, snapshot_metrics
from .storage import Storage, payload_hash


@dataclass
class CollectionResult:
    source: str
    run_id: int | None
    auctions: int
    observations: int
    payload_hash: str | None
    not_modified: bool = False
    source_modified_at: str | None = None


class Collector:
    def __init__(self, client: BlizzardClient, storage: Storage, region: str):
        self.client = client
        self.storage = storage
        self.region = region

    def _prepare(
        self,
        source: str,
        payload: dict,
        source_modified_at: str | None,
    ) -> tuple[str, int]:
        digest = payload_hash(payload)
        raw_path = self.storage.save_raw(source, digest, payload)
        run_id = self.storage.begin_run(
            self.region,
            source,
            digest,
            raw_path,
            source_modified_at=source_modified_at,
        )
        return digest, run_id

    @staticmethod
    def _coerce_document(value: ApiDocument | dict) -> ApiDocument:
        if isinstance(value, ApiDocument):
            return value
        return ApiDocument(data=value, last_modified=None, not_modified=False)

    def commodities(self) -> CollectionResult:
        source = "commodities"
        last_modified = self.storage.last_modified_for_source(source)
        document = self._coerce_document(
            self.client.commodities(if_modified_since=last_modified)
        )

        if document.not_modified:
            return CollectionResult(
                source=source,
                run_id=None,
                auctions=0,
                observations=0,
                payload_hash=None,
                not_modified=True,
                source_modified_at=document.last_modified,
            )

        payload = document.data or {}
        digest, run_id = self._prepare(source, payload, document.last_modified)

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
                source_modified_at=document.last_modified,
            )
        except Exception as exc:
            self.storage.finish_run(run_id, str(exc))
            raise

    def realm(self, connected_realm_id: int) -> CollectionResult:
        source = f"realm:{connected_realm_id}"
        last_modified = self.storage.last_modified_for_source(source)
        document = self._coerce_document(
            self.client.realm_auctions(
                connected_realm_id,
                if_modified_since=last_modified,
            )
        )

        if document.not_modified:
            return CollectionResult(
                source=source,
                run_id=None,
                auctions=0,
                observations=0,
                payload_hash=None,
                not_modified=True,
                source_modified_at=document.last_modified,
            )

        payload = document.data or {}
        digest, run_id = self._prepare(source, payload, document.last_modified)

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
                source_modified_at=document.last_modified,
            )
        except Exception as exc:
            self.storage.finish_run(run_id, str(exc))
            raise
