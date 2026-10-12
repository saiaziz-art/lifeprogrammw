"""Runtime configuration, read from environment variables (and an optional .env file).

Secrets that integrations need (API keys, IMAP/SMTP passwords) are NOT configured here:
they are stored encrypted in the database (see plot.security.crypto) and managed from the
admin screen. The only secret required in the environment is PLOT_SECRET_KEY, the master
key used to encrypt those credentials.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PLOT_", extra="ignore")

    env: str = Field("production", description="production | development | test | sandbox")
    # Public URL of the web app (used for OAuth redirect URIs: <public_url>/api/mail/oauth/callback)
    public_url: str = "http://localhost:3000"
    database_url: str = "postgresql+psycopg://postgres@127.0.0.1:5432/plot"

    # Master key (Fernet, urlsafe base64 32 bytes) for encrypting stored credentials.
    # Generate with: plot keygen
    secret_key: str | None = None

    # Sessions
    session_days: int = 14
    cookie_secure: bool | None = None  # defaults to True outside development

    # Outbound HTTP etiquette for public data APIs
    http_timeout_seconds: float = 30.0
    http_max_retries: int = 3
    http_contact: str = "private research use"

    # Scheduler / worker
    scheduler_tick_seconds: int = 30
    scheduler_misfire_grace_hours: int = 8
    worker_poll_seconds: float = 5.0
    worker_stale_lock_minutes: int = 30

    # Where the default daily run is scheduled (editable in the admin screen afterwards)
    daily_cron: str = "0 7 * * *"
    daily_timezone: str = "Europe/London"

    @property
    def is_dev(self) -> bool:
        return self.env in ("development", "test")

    @property
    def is_sandbox(self) -> bool:
        """Separate test environment: clearly labelled mock contacts, no real mailboxes or postal APIs."""
        return self.env == "sandbox"

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return not self.is_dev


@lru_cache
def get_settings() -> Settings:
    return Settings()
