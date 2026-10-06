"""Conversation loop: student message -> LLM -> tools -> grounded SMS-style reply.

The database (here: the spike's normalized items) is the source of truth;
the LLM only decides which tools to call and phrases the answer. A
grounding check rejects replies that state a date, time, or weekday that
no tool returned, the one hallucination this product can't afford.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from .llm import AssistantTurn, ChatModel
from .timeutil import fmt_when
from .tools import Toolbox

SYSTEM_PROMPT = """You are a texting assistant for a university student. You help them keep track of \
deadlines, quizzes and exams from their Brightspace courses.

Current local time: {now} ({tz}).

Rules:
- Facts about deadlines, dates, times, courses and exams come ONLY from tool results. Never guess or \
invent them. If the tools return nothing, say so plainly.
- Call a tool before answering any question about the student's courses or deadlines, including \
follow-ups like "what about tomorrow?" or "when is it due?".
- Copy dates and times exactly as the tools give them (e.g. "Mon Oct 5, 4:00 PM"). You may shorten \
to "4 PM", "today" or "tomorrow" when that's unambiguous.
- Use the range parameter for today/tomorrow/this week/next week. For specific days ("before \
Friday"), work out the YYYY-MM-DD dates from the current time above and use range=custom.
- When the student refers to "that" or "it", use the item from the conversation (tool results \
include refs like i3, so pass the ref). Never show refs to the student.
- If a tool returns needs_clarification, ask which one they mean. Don't act yet.
- If a course has no deadlines in the calendar feed, say so and suggest checking the course outline.
- For "what should I work on", use prioritize, and say when you don't know how long things take. \
Never claim to know difficulty, effort or grade weights unless a tool or the student said so.
- Reminders, morning texts and syllabus/grade questions aren't available in this test version. \
Say they're coming in the SMS version.
- Style: like a text message. Short, friendly, plain text. Use "•" bullets for lists, no markdown \
headings or bold. Keep it to a few lines unless you're listing items. Use emoji sparingly (✅ for \
confirmations is fine)."""

FALLBACK_REPLY = ("Sorry, I couldn't double-check that against your calendar. "
                  "Try asking it another way, like \"what's due this week\".")
STUCK_REPLY = "Sorry, I got stuck on that one. Try rephrasing?"

# --- grounding -----------------------------------------------------------------

_TIME = re.compile(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s?([ap])\.?m\.?\b", re.I)
_MONTH_DAY = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
# capitalised only, so verbs like "I sat down" don't count as Saturday
_WEEKDAY = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day|s|sday|nesday|r|rs|rsday|urday|\.)?\b")


def extract_facts(text: str) -> set[tuple]:
    facts: set[tuple] = set()
    for h, m, ap in _TIME.findall(text):
        facts.add(("time", int(h) % 12, int(m or 0), ap.lower()))
    for mon, day in _MONTH_DAY.findall(text):
        facts.add(("date", mon.lower()[:3], int(day)))
    for wd in _WEEKDAY.findall(text):
        facts.add(("weekday", wd.lower()[:3]))
    return facts


def ungrounded_facts(reply: str, evidence: list[str]) -> set[tuple]:
    allowed = set().union(*(extract_facts(e) for e in evidence)) if evidence else set()
    return extract_facts(reply) - allowed


class Agent:
    def __init__(
        self,
        model: ChatModel,
        toolbox: Toolbox,
        tz: ZoneInfo,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        *,
        max_rounds: int = 6,
        history_turns: int = 6,
        on_event: Callable[[str, object], None] | None = None,
    ):
        self.model = model
        self.toolbox = toolbox
        self.tz = tz
        self.now_fn = now_fn
        self.max_rounds = max_rounds
        self.history_turns = history_turns
        self.on_event = on_event or (lambda kind, payload: None)
        self.turns: list[list[dict]] = []  # each turn: user msg, tool traffic, final assistant msg

    def reset(self) -> None:
        self.turns.clear()

    def _system(self) -> dict:
        now = self.now_fn()
        return {"role": "system", "content": SYSTEM_PROMPT.format(now=fmt_when(now, self.tz), tz=self.tz.key)}

    def reply(self, text: str) -> str:
        system = self._system()
        turn: list[dict] = [{"role": "user", "content": text}]
        retried_grounding = False

        for _ in range(self.max_rounds):
            history = [m for t in self.turns[-self.history_turns:] for m in t]
            result: AssistantTurn = self.model.complete([system, *history, *turn], self.toolbox.schemas())

            if result.tool_calls:
                turn.append({
                    "role": "assistant",
                    "content": result.content or None,
                    "tool_calls": [c.as_message_part() for c in result.tool_calls],
                })
                for call in result.tool_calls:
                    self.on_event("tool_call", {"name": call.name, "arguments": call.arguments})
                    output = self.toolbox.call(call.name, call.arguments)
                    self.on_event("tool_result", {"name": call.name, "result": output})
                    turn.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(output, ensure_ascii=False)})
                continue

            reply = (result.content or "").strip()
            # tool output is the evidence; the student's own words may be echoed ("before Friday")
            evidence = [system["content"]] + [m["content"] for t in [*self.turns, turn] for m in t
                                              if m["role"] == "tool" or (m["role"] == "user" and m["content"])]
            bad = ungrounded_facts(reply, evidence)
            if bad:
                self.on_event("ungrounded", sorted(bad))
                if not retried_grounding:
                    retried_grounding = True
                    turn.append({"role": "assistant", "content": reply})
                    turn.append({"role": "user", "content": (
                        "[automatic check] Your last reply mentioned dates/times that don't appear in any tool "
                        "result. Call the right tool and answer again using only what the tools return.")})
                    continue
                reply = FALLBACK_REPLY
            turn.append({"role": "assistant", "content": reply})
            self.turns.append(turn)
            return reply

        self.turns.append(turn + [{"role": "assistant", "content": STUCK_REPLY}])
        return STUCK_REPLY
