"""Shared fixtures: an isolated in-memory database per test."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import db

TZ = ZoneInfo("America/Toronto")
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)  # Mon Oct 5 2026, 9 AM Toronto


@pytest.fixture
def session_factory():
    return db.configure("sqlite://")


@pytest.fixture
def session(session_factory):
    s = session_factory()
    yield s
    s.close()
