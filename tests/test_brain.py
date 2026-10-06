import json
from datetime import datetime, timezone

from app import brain
from app.ai.llm import AssistantTurn, ToolCall
from app.models import AcademicItem, ConversationTurn, User
from conftest import NOW


class Script:
    def __init__(self, steps):
        self.steps = list(steps)

    def complete(self, messages, tools):
        return self.steps.pop(0)


def _student(session):
    u = User(phone="+15195550187", institution="laurier", onboarding_step="done", status="active")
    session.add(u)
    session.flush()
    session.add(AcademicItem(user_id=u.id, external_source_id="q5", institution="laurier", course_code="BU111",
                             title="Quiz 5", type="quiz", category="deliverable", org_kind="course",
                             due_at=datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc)))
    session.flush()
    return u


def test_not_configured_without_a_key(session, monkeypatch):
    monkeypatch.setattr(brain, "current_model", lambda: None)
    assert brain.answer(session, _student(session), "what's due", now=NOW) == brain.NOT_CONFIGURED


def test_answer_saves_memory_for_follow_ups(session):
    u = _student(session)
    call = AssistantTurn(None, [ToolCall("c1", "list_deadlines", {"range": "today"}, json.dumps({"range": "today"}))])
    brain.set_model(Script([call, AssistantTurn("BU111 Quiz 5 is due today at 4 PM."),
                            AssistantTurn("Yep, BU111 Quiz 5 at 4 PM today.")]))
    try:
        assert brain.answer(session, u, "what's due today", now=NOW) == "BU111 Quiz 5 is due today at 4 PM."
        # follow-up in a later text: the earlier tool result is in memory, so restating 4 PM is grounded
        assert brain.answer(session, u, "when was it again", now=NOW) == "Yep, BU111 Quiz 5 at 4 PM today."
        assert session.query(ConversationTurn).filter_by(user_id=u.id).count() == 2
    finally:
        brain.set_model(None)
