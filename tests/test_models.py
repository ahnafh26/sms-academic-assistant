from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import AcademicItem, User
from conftest import NOW


def test_user_phone_unique(session):
    session.add(User(phone="+15195550187"))
    session.commit()
    session.add(User(phone="+15195550187"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_datetimes_round_trip_as_aware_utc(session):
    user = User(phone="+15195550100")
    session.add(user)
    session.flush()
    session.add(AcademicItem(user_id=user.id, external_source_id="x1", institution="laurier", title="Quiz 5",
                             type="quiz", category="deliverable", due_at=NOW))
    session.commit()
    session.expire_all()
    item = session.query(AcademicItem).one()
    assert item.due_at == NOW and item.due_at.tzinfo is not None
    assert item.due_at.utcoffset() == timedelta(0)
    assert item.actionable


def test_naive_datetimes_rejected(session):
    user = User(phone="+15195550101", consent_at=datetime(2026, 10, 5, 9))
    session.add(user)
    with pytest.raises(Exception, match="naive datetime"):
        session.commit()


def test_actionable_rules(session):
    base = dict(user_id="u", external_source_id="e", institution="x", title="t", type="quiz", org_kind="course",
                due_at=datetime.now(timezone.utc))
    assert not AcademicItem(**base, category="content").actionable
    assert not AcademicItem(**base, category="deliverable", in_term=False).actionable
    assert not AcademicItem(**base, category="deliverable", cancelled=True).actionable
    assert AcademicItem(**base, category="deliverable", missed_syncs=1).actionable   # one glitchy sync isn't enough
    assert not AcademicItem(**base, category="deliverable", missed_syncs=2).actionable
    assert AcademicItem(**base, category="exam").actionable
    assert not AcademicItem(**{**base, "org_kind": "institution"}, category="deliverable").actionable
