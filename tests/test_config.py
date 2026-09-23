"""
tests/test_config.py

Regression test for a real crash: CORS_ORIGINS was documented in
.env.example as a plain comma-separated value ("http://a,http://b"), but
config.Settings.cors_origins was typed as list[str]. pydantic-settings
JSON-decodes any list/dict-typed field's raw environment string, so a
plain comma-separated value raised SettingsError -- as soon as anything
imported config (including `alembic upgrade head`, since alembic/env.py
calls get_settings()).

cors_origins is now a computed property parsed from the plain-string
field cors_origins_raw (env alias CORS_ORIGINS); these tests cover every
shape that field accepts.
"""

import os

import pytest

from config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings() is lru_cache'd process-wide; without clearing it,
    the env vars set in one test would leak into the next."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    os.environ.pop("CORS_ORIGINS", None)


def test_cors_origins_defaults_when_unset():
    """Isolated from any real .env file on disk (e.g. a local dev .env
    with its own CORS_ORIGINS) -- this checks the code default only."""
    assert Settings(_env_file=None).cors_origins == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_cors_origins_accepts_comma_separated_string():
    """The exact format documented in .env.example -- this is the value
    that crashed `alembic upgrade head` before the fix."""
    os.environ["CORS_ORIGINS"] = "http://localhost:5173,http://localhost:4173"
    assert get_settings().cors_origins == ["http://localhost:5173", "http://localhost:4173"]


def test_cors_origins_strips_whitespace_around_commas():
    os.environ["CORS_ORIGINS"] = " http://a , http://b "
    assert get_settings().cors_origins == ["http://a", "http://b"]


def test_cors_origins_accepts_single_origin_with_no_comma():
    os.environ["CORS_ORIGINS"] = "http://localhost:5173"
    assert get_settings().cors_origins == ["http://localhost:5173"]


def test_cors_origins_accepts_json_array():
    """Also accepted, for programmatic env injection (Docker/CI)."""
    os.environ["CORS_ORIGINS"] = '["http://a", "http://b"]'
    assert get_settings().cors_origins == ["http://a", "http://b"]


def test_settings_construction_never_raises_settingserror():
    """The actual crash: Settings() raised pydantic_settings.SettingsError
    as soon as CORS_ORIGINS was a plain string. Constructing Settings
    directly (bypassing the get_settings cache) reproduces exactly what
    alembic/env.py does on every `alembic upgrade head`."""
    os.environ["CORS_ORIGINS"] = "http://localhost:5173,http://localhost:4173"
    Settings()  # must not raise