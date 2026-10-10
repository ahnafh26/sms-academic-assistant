import pytest

from app import security
from app.config import get_settings
from app.db import normalize_url


def test_railway_postgres_urls_get_the_psycopg_driver():
    assert normalize_url("postgres://u:p@host:5432/db") == "postgresql+psycopg://u:p@host:5432/db"
    assert normalize_url("postgresql://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert normalize_url("postgresql+psycopg://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert normalize_url("sqlite:///data/app.db") == "sqlite:///data/app.db"


def test_a_real_database_needs_a_real_encryption_key(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    security._fernet.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
            security.encrypt("https://example.com/feed")
    finally:
        get_settings.cache_clear()
        security._fernet.cache_clear()
