"""Agent loop tests with a scripted fake LLM (no network)."""

import json

import pytest

from chat.agent import FALLBACK_REPLY, STUCK_REPLY, Agent, extract_facts, ungrounded_facts
from chat.llm import AssistantTurn, ToolCall
from chat.tools import Toolbox
from conftest import NOW, TZ


class FakeModel:
    """Returns scripted turns; records every message list it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        step = self.script.pop(0)
        return step(messages) if callable(step) else step


def call(name, **args):
    return AssistantTurn(None, [ToolCall(id=f"c-{name}", name=name, arguments=args, raw_arguments=json.dumps(args))])


def say(text):
    return AssistantTurn(text)


@pytest.fixture
def make_agent(store):
    def build(script):
        model = FakeModel(script)
        events = []
        agent = Agent(model, Toolbox(store, TZ, now_fn=lambda: NOW), TZ, now_fn=lambda: NOW,
                      on_event=lambda k, p: events.append((k, p)))
        return agent, model, events
    return build


def test_tool_then_grounded_answer(make_agent):
    agent, model, events = make_agent([
        call("list_deadlines", range="today"),
        say("You've got 3 things due today:\n• BU111 Quiz 5 — 4 PM\n• CS135 A4 — 10 PM\n• EC120 EOC — 11:59 PM"),
    ])
    reply = agent.reply("what's due today")
    assert reply.startswith("You've got 3 things")
    assert events[0] == ("tool_call", {"name": "list_deadlines", "arguments": {"range": "today"}})
    tool_msg = next(m for m in model.seen[1] if m["role"] == "tool")
    assert "Mon Oct 5, 4:00 PM" in tool_msg["content"]
    assert "Current local time: Mon Oct 5, 9:00 AM" in model.seen[0][0]["content"]


def test_invented_time_triggers_retry_then_fallback(make_agent):
    agent, model, events = make_agent([
        call("list_deadlines", range="today"),
        say("BU111 Quiz 5 is due at 3 PM."),          # tool said 4:00 PM
        say("Still, I think it's 2:30 PM."),           # still wrong
    ])
    assert agent.reply("when's the quiz") == FALLBACK_REPLY
    assert events[-1][0] == "ungrounded"
    assert "[automatic check]" in model.seen[2][-1]["content"]


def test_retry_can_recover(make_agent):
    agent, _, _ = make_agent([
        say("Your quiz is Tuesday at 4 PM."),           # no tool called at all
        call("next_deadlines", count=1),
        say("BU111 Quiz 5 is due today at 4 PM."),
    ])
    assert agent.reply("when's my quiz") == "BU111 Quiz 5 is due today at 4 PM."


def test_follow_up_uses_history_and_refs(make_agent, store):
    def mark_with_ref_from_history(messages):
        tool_out = next(json.loads(m["content"]) for m in messages if m["role"] == "tool")
        ref = next(i["ref"] for i in tool_out["items"] if i["course"] == "CS135")
        return call("mark_complete", item=ref)

    agent, model, _ = make_agent([
        call("list_deadlines", range="today"),
        say("3 things today: BU111 Quiz 5 (4 PM), CS135 Assignment 4 (10 PM), EC120 EOC (11:59 PM)."),
        mark_with_ref_from_history,
        say("Marked CS135 Assignment 4 complete ✅"),
    ])
    agent.reply("what's due today")
    assert agent.reply("I finished the cs one") == "Marked CS135 Assignment 4 complete ✅"
    assert store.get("cs-a4").completed
    assert any((m.get("content") or "").startswith("3 things today") for m in model.seen[2] if m["role"] == "assistant")


def test_echoing_students_words_is_allowed(make_agent):
    agent, _, _ = make_agent([
        call("list_deadlines", range="custom", start_date="2026-10-05", end_date="2026-10-08"),
        say("Before Friday you have 4 things due."),
    ])
    assert agent.reply("do I have anything before Friday") == "Before Friday you have 4 things due."


def test_stuck_loop(make_agent):
    agent, _, _ = make_agent([call("get_courses")] * 6)
    assert agent.reply("hi") == STUCK_REPLY


def test_fact_extraction():
    assert extract_facts("due Mon Oct 5, 11:59 PM") == {("weekday", "mon"), ("date", "oct", 5), ("time", 11, 59, "p")}
    assert ("time", 4, 0, "p") in extract_facts("at 4pm")
    assert extract_facts("I sat down and may finish 3 tasks") == set()
    assert ungrounded_facts("Due Thursday at 7 PM", ['{"starts": "Thu Oct 15, 7:00 PM"}']) == set()
    assert ungrounded_facts("Due Friday", ['{"starts": "Thu Oct 15, 7:00 PM"}']) == {("weekday", "fri")}
