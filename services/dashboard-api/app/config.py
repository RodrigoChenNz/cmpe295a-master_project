"""Settings. Everything comes from the environment; nothing is hardcoded here
that differs between a laptop and the compose network."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://retail:retail_dev_only@localhost:5432/retail_pricing"

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_client_id: str = "dashboard-api"

    # Whether to connect to MQTT at all. The console is fully usable against
    # Postgres alone, which is how it runs before Layers 1-4 exist.
    mqtt_enabled: bool = True

    # Session cookie. secure=False only because local dev is plain http; the
    # compose file and any deployment must set SESSION_COOKIE_SECURE=true.
    session_cookie_name: str = "shelfiq_session"
    session_cookie_secure: bool = False
    session_ttl_hours: int = 12

    # On startup, set a real password hash for this user from this password, so
    # that no committed file in the repo contains a working credential. Leave
    # the password empty in any shared environment and invite users instead.
    shelfiq_bootstrap_user: str = ""
    shelfiq_bootstrap_password: str = ""

    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
