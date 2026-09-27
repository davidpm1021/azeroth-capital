from dataclasses import dataclass

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .auth import BlizzardAuth
from .config import Settings


@dataclass(frozen=True)
class ApiDocument:
    data: dict | None
    last_modified: str | None
    not_modified: bool = False


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


class BlizzardClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.http = httpx.Client(
            timeout=settings.ac_timeout_seconds,
            headers={"User-Agent": "AzerothCapital/0.2"},
        )
        self.auth = BlizzardAuth(settings, self.http)

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @retry(
        retry=retry_if_exception(_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get_document(
        self,
        path: str,
        namespace: str,
        if_modified_since: str | None = None,
    ) -> ApiDocument:
        headers = {"Authorization": f"Bearer {self.auth.access_token()}"}
        if if_modified_since:
            headers["If-Modified-Since"] = if_modified_since

        response = self.http.get(
            f"{self.settings.api_base}{path}",
            params={"namespace": namespace, "locale": self.settings.wow_locale},
            headers=headers,
        )

        if response.status_code == 304:
            return ApiDocument(
                data=None,
                last_modified=response.headers.get("Last-Modified") or if_modified_since,
                not_modified=True,
            )

        response.raise_for_status()
        return ApiDocument(
            data=response.json(),
            last_modified=response.headers.get("Last-Modified"),
            not_modified=False,
        )

    def _get(self, path: str, namespace: str) -> dict:
        document = self._get_document(path, namespace)
        return document.data or {}

    def commodities(self, if_modified_since: str | None = None) -> ApiDocument:
        return self._get_document(
            "/data/wow/auctions/commodities",
            self.settings.dynamic_namespace,
            if_modified_since=if_modified_since,
        )

    def connected_realm_index(self) -> dict:
        return self._get("/data/wow/connected-realm/index", self.settings.dynamic_namespace)

    def connected_realm(self, connected_realm_id: int) -> dict:
        return self._get(
            f"/data/wow/connected-realm/{connected_realm_id}",
            self.settings.dynamic_namespace,
        )

    def realm_auctions(
        self,
        connected_realm_id: int,
        if_modified_since: str | None = None,
    ) -> ApiDocument:
        return self._get_document(
            f"/data/wow/connected-realm/{connected_realm_id}/auctions",
            self.settings.dynamic_namespace,
            if_modified_since=if_modified_since,
        )

    def item(self, item_id: int) -> dict:
        return self._get(f"/data/wow/item/{item_id}", self.settings.static_namespace)
