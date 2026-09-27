from dataclasses import dataclass
from time import monotonic

import httpx

from .config import Settings


@dataclass
class Token:
    access_token: str
    expires_at: float


class BlizzardAuth:
    def __init__(self, settings: Settings, client: httpx.Client):
        self.settings = settings
        self.client = client
        self._token: Token | None = None

    def access_token(self) -> str:
        now = monotonic()
        if self._token and now < self._token.expires_at - 30:
            return self._token.access_token

        if not self.settings.blizzard_client_id or not self.settings.blizzard_client_secret:
            raise RuntimeError("Missing BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET in .env")

        response = self.client.post(
            self.settings.oauth_url,
            data={"grant_type": "client_credentials"},
            auth=(self.settings.blizzard_client_id, self.settings.blizzard_client_secret),
        )
        response.raise_for_status()
        payload = response.json()
        self._token = Token(
            access_token=payload["access_token"],
            expires_at=now + int(payload.get("expires_in", 86399)),
        )
        return self._token.access_token
