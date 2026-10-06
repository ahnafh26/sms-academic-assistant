"""Database engine/session. SQLite locally, Postgres when deployed (DATABASE_URL)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from .config import DATA_DIR, get_settings


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Always store UTC and always return timezone-aware UTC datetimes.

    SQLite has no timezone support, so without this, aware datetimes come
    back naive and comparisons with "now" silently break.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime given to UTCDateTime; use timezone-aware datetimes")
        return value.astimezone(timezone.utc).replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    if url.startswith("sqlite"):
        if url in ("sqlite://", "sqlite:///:memory:"):
            return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def configure(url: str | None = None) -> sessionmaker[Session]:
    """(Re)bind the global engine; tests call this with an in-memory URL."""
    global _engine, _SessionLocal
    _engine = make_engine(url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    from . import models  # noqa: F401  (register tables)

    # MVP: create tables directly. Alembic migrations come with the first deploy.
    Base.metadata.create_all(_engine)
    return _SessionLocal


def session_factory() -> sessionmaker[Session]:
    return _SessionLocal or configure()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session
