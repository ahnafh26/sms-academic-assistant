"""The brain over the database: tools see only this student's actionable rows; refs survive across texts."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.ai.agent import FALLBACK_REPLY, Agent
from app.ai.llm import AssistantTurn, ToolCall, model_from_settings
from app.ai.store import Store
from app.ai.tools import Toolbox
from app.models import AcademicItem, Course, User
from conftest import NOW, TZ


def _ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


@pytest.fixture
def student(session):
    me, other = User(phone="+15195550187", institution="laurier"), User(phone="+15195550999", institution="laurier")
    session.add_all([me, other])
    session.flush()
    def item(user, ext, code, title, typ, due, **kw):
        session.add(AcademicItem(user_id=user.id, external_source_id=ext, institution="laurier", course_code=code,
                                 course_label=code, title=title, type=typ, org_kind="course",
                                 category=kw.pop("category", "deliverable"), due_at=_ts(due), **kw))
    item(me, "q5", "BU111", "Quiz 5", "quiz", "2026-10-05T20:00:00")                       # today 4 PM
    item(me, "eoc", "EC120", "Ch. 5 EOC Problems", "assignment", "2026-10-06T14:00:00")    # tomorrow 10 AM
    item(me, "old", "EC120", "Old midterm", "midterm", "2024-10-04T22:00:00", in_term=False, category="exam")
    item(me, "rub", "BU111", "Presentation rubric", "other", "2026-10-07T17:00:00", category="content")
    item(other, "x", "CS135", "Someone else's A4", "assignment", "2026-10-05T22:00:00")
    session.add(Course(user_id=me.id, institution="laurier", code="CS115", label="CS 115", has_calendar_items=False))
    session.flush()
    return me


def toolbox(session, user):
    store = Store(session, user.id)
    store.load()
    return Toolbox(store, TZ, now_fn=lambda: NOW)


def test_store_only_sees_own_actionable_items(session, student):
    tb = toolbox(session, student)
    titles = [i["title"] for i in tb.call("list_deadlines", {"range": "this_week"})["items"]]
    assert titles == ["Quiz 5", "Ch. 5 EOC Problems"]
    assert "CS115" in tb.call("list_deadlines", {"range": "this_week", "course": "CS115"})["error"]


def test_refs_stable_across_separate_toolboxes(session, student):
    ref = toolbox(session, student).call("find_items", {"query": "econ"})["candidates"][0]["ref"]
    later = toolbox(session, student)  # a later text builds a fresh toolbox
    r = later.call("mark_complete", {"item": ref})
    assert r["marked_complete"]["title"] == "Ch. 5 EOC Problems"
    assert session.query(AcademicItem).filter_by(external_source_id="eoc").one().completed_at == NOW


def test_cannot_touch_another_students_item(session, student):
    other_row = session.query(AcademicItem).filter_by(external_source_id="x").one()
    fake_ref = "i" + other_row.id.replace("-", "")[:8]
    assert "unknown ref" in toolbox(session, student).call("mark_complete", {"item": fake_ref})["error"]


class Script:
    def __init__(self, steps):
        self.steps = list(steps)

    def complete(self, messages, tools):
        return self.steps.pop(0)


def test_agent_with_persisted_history(session, student):
    call = AssistantTurn(None, [ToolCall("c1", "list_deadlines", {"range": "today"}, json.dumps({"range": "today"}))])
    first = Agent(Script([call, AssistantTurn("1 thing today: BU111 Quiz 5 at 4 PM.")]),
                  toolbox(session, student), TZ, now_fn=lambda: NOW)
    assert first.reply("what's due today") == "1 thing today: BU111 Quiz 5 at 4 PM."
    saved = first.turns[-1]
    # a later text: history restored from the database; inventing a time is still caught
    second = Agent(Script([AssistantTurn("It's at 3 PM."), AssistantTurn("Still 3 PM.")]),
                   toolbox(session, student), TZ, now_fn=lambda: NOW, history=[saved])
    assert second.reply("when is it again") == FALLBACK_REPLY
    assert len(second.turns) == 2


def test_model_from_settings():
    s = lambda **kw: SimpleNamespace(**{"llm_provider": "gemini", "llm_api_key": None, "llm_model": None, "llm_base_url": None, **kw})
    assert model_from_settings(s()) is None
    assert model_from_settings(s(llm_api_key="paste-your-key-here")) is None
    assert model_from_settings(s(llm_api_key="k")).model == "gemini-2.5-flash"
    assert model_from_settings(s(llm_provider="ollama")).model == "qwen2.5:7b"
