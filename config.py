"""
config.py

Central application settings, loaded from environment variables (and
optionally a .env file — see .env.example for the recognized keys).
Every other module that needs a configurable value (session TTL, CORS
origins, the database URL) should read it from here via get_settings()
rather than hardcoding it.
"""

import json
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # SQLAlchemy connection string. Defaults to a local PostgreSQL
    # instance; db/connection.py also supports sqlite:// URLs (used by
    # the test suite) via a conditional connect_args.
    database_url: str = "postgresql://cna_user:cna_password@localhost:5432/cna"

    # How long an issued session token stays valid (api/auth.py).
    session_ttl_hours: int = 8

    # Origins allowed to call the API with credentials — the dashboard
    # dev server's ports by default (see dashboard/vite.config.js).
    #
    # The CORS_ORIGINS env var accepts EITHER a plain comma-separated
    # string (documented in .env.example, e.g. "http://a,http://b" — no
    # quoting needed) OR a JSON array (e.g. '["http://a","http://b"]',
    # handy when a value is injected programmatically, such as a
    # Docker/CI env var).
    #
    # This field is declared as `str` (not `list[str]`) on purpose:
    # pydantic-settings JSON-decodes the raw environment string for any
    # list/dict-typed field BEFORE any validator on that field runs, so a
    # validator can never intercept a plain comma-separated value — it
    # would already have raised SettingsError by then. Declaring it as
    # `str` skips that built-in JSON-decode step; the `cors_origins`
    # property below does the actual parsing, and is what every caller
    # (api/main.py) uses — nothing outside this file reads the raw field.
    cors_origins_raw: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        validation_alias="CORS_ORIGINS",
    )

    @property
    def cors_origins(self) -> list[str]:
        value = self.cors_origins_raw.strip()
        if value.startswith("["):
            return json.loads(value)
        return [origin.strip() for origin in value.split(",") if origin.strip()]

    # Login brute-force protection (api/ratelimit.py).
    login_max_failures: int = 5          # failed attempts per badge id ...
    login_window_seconds: int = 900      # ... within this window ...
    login_lockout_seconds: int = 900     # ... locks that badge for this long

    # PBKDF2 iteration count for password hashing (schema/user.py).
    # Kept here so it can be tuned without touching hashing code.
    pbkdf2_iterations: int = 260_000


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings instance, built once and cached.
    Safe to call repeatedly from anywhere — subsequent calls are free.
    """
    return Settings()