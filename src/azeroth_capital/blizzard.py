import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .auth import BlizzardAuth
from .config import Settings


class BlizzardClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.http = httpx.Client(timeout=settings.ac_timeout_seconds)
        self.auth = BlizzardAuth(settings, self.http)

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get(self, path: str, namespace: str) -> dict:
        response = self.http.get(
            f"{self.settings.api_base}{path}",
            params={"namespace": namespace, "locale": self.settings.wow_locale},
            headers={"Authorization": f"Bearer {self.auth.access_token()}"},
        )
        response.raise_for_status()
        return response.json()

    def commodities(self) -> dict:
        return self._get("/data/wow/auctions/commodities", self.settings.dynamic_namespace)

    def connected_realm_index(self) -> dict:
        return self._get("/data/wow/connected-realm/index", self.settings.dynamic_namespace)

    def realm_auctions(self, connected_realm_id: int) -> dict:
        return self._get(
            f"/data/wow/connected-realm/{connected_realm_id}/auctions",
            self.settings.dynamic_namespace,
        )

    def item(self, item_id: int) -> dict:
        return self._get(f"/data/wow/item/{item_id}", self.settings.static_namespace)
