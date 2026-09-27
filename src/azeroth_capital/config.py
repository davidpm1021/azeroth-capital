from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    blizzard_client_id: str = ""
    blizzard_client_secret: str = ""
    wow_region: str = "us"
    wow_locale: str = "en_US"
    ac_database_path: Path = Path("data/azeroth_capital.db")
    ac_raw_dir: Path = Path("data/raw")
    ac_timeout_seconds: float = 60.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def dynamic_namespace(self) -> str:
        return f"dynamic-{self.wow_region}"

    @property
    def static_namespace(self) -> str:
        return f"static-{self.wow_region}"

    @property
    def api_base(self) -> str:
        return f"https://{self.wow_region}.api.blizzard.com"

    @property
    def oauth_url(self) -> str:
        return "https://oauth.battle.net/token"
