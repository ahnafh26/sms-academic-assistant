"""Tools the LLM calls to read and change the student's academic data.

Design rules (from docs/architecture.md):
- the LLM never sees or chooses a user/student id; the toolbox is bound
  to one student's store
- every fact the assistant states must come from a tool result
- dates are resolved and formatted here, never computed by the model
- items are referred to by short stable refs (i + 8 hex chars of the db id) so follow-ups
  like "mark that done" resolve to an exact record, even in a later text
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .store import Item, Store
from .timeutil import RANGES, RangeError, fmt_day, fmt_relative, fmt_when, resolve_range

ITEM_TYPES = ["assignment", "quiz", "exam", "midterm", "lab", "discussion", "presentation", "reading", "lecture", "other"]

# casual names -> subject prefixes seen in Ontario course codes
COURSE_ALIASES: dict[str, tuple[str, ...]] = {
    "econ": ("EC", "ECON"), "economics": ("EC", "ECON"), "micro": ("EC", "ECON"), "macro": ("EC", "ECON"),
    "business": ("BU", "BUS", "COMMERCE"), "bus": ("BU", "BUS"),
    "math": ("MA", "MATH", "MTHEL"), "calc": ("MA", "MATH"), "calculus": ("MA", "MATH"), "algebra": ("MA", "MATH"),
    "cs": ("CS", "CP", "CISC", "COMPSCI", "COMP"), "compsci": ("CS", "CP", "CISC", "COMPSCI", "COMP"),
    "comp": ("CS", "CP", "COMP"), "stats": ("ST", "STAT", "STATS"), "psych": ("PS", "PSYC", "PSYCH"),
    "bio": ("BI", "BIOL", "BIO"), "chem": ("CH", "CHEM"), "phys": ("PH", "PHYS"), "english": ("EN", "ENGL"),
}


class ToolError(Exception):
    pass


def _subject(code: str) -> str:
    m = re.match(r"[A-Z]+", code or "")
    return m.group(0) if m else ""


def course_matches(item_course: str, item_course_name: str | None, query: str) -> bool:
    q = query.strip().lower().replace(" ", "")
    if not q:
        return True
    code = (item_course or "").lower()
    if q == code or code.startswith(q):
        return True
    if q in COURSE_ALIASES and _subject(item_course) in COURSE_ALIASES[q]:
        return True
    name = (item_course_name or "").lower()
    return len(q) >= 4 and q in name.replace(" ", "")


class Toolbox:
    def __init__(
        self,
        store: Store,
        tz: ZoneInfo,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        refresh_fn: Callable[[], str] | None = None,
    ):
        self.store = store
        self.tz = tz
        self.now_fn = now_fn
        self.refresh_fn = refresh_fn
        self._tools: dict[str, tuple[Callable[..., dict], dict]] = {}
        self._last_refresh: datetime | None = None
        self._register_query_tools()
        self._register_action_tools()
        self._register_reminder_tools()

    # ------------------------------------------------------------ plumbing

    def schemas(self) -> list[dict]:
        return [{"type": "function", "function": schema} for _, schema in self._tools.values()]

    def call(self, name: str, args: dict[str, Any]) -> dict:
        if name not in self._tools:
            return {"error": f"unknown tool '{name}'"}
        fn, _ = self._tools[name]
        try:
            return fn(**(args or {}))
        except (ToolError, RangeError) as exc:
            return {"error": str(exc)}
        except TypeError as exc:  # bad/missing arguments from the model
            return {"error": f"bad arguments for {name}: {exc}"}

    def _tool(self, name: str, description: str, properties: dict, required: list[str] | None = None):
        def register(fn):
            self._tools[name] = (fn, {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required or []},
            })
            return fn
        return register

    def ref(self, item: Item) -> str:
        """Stable across texts: derived from the item's database id, not a per-session counter."""
        return "i" + item.id.replace("-", "")[:8]

    def _item_for_ref(self, ref: str) -> Item | None:
        key = ref[1:].lower()
        return next((i for i in self.store.items if i.id.replace("-", "").startswith(key)), None)

    def view(self, item: Item, now: datetime) -> dict:
        when = item.when
        v: dict[str, Any] = {"ref": self.ref(item), "course": item.course, "title": item.title, "type": item.type}
        key = "due" if item.due else "starts"
        v[key] = fmt_when(when, self.tz, item.all_day)
        v["when"] = fmt_relative(when, now)
        if item.completed:
            v["status"] = "done"
        elif when < now:
            v["status"] = "passed" if item.type in ("exam", "midterm") else "overdue"
        else:
            v["status"] = "upcoming"
        if item.estimate_minutes:
            v["estimate"] = f"{item.estimate_minutes / 60:g}h"
        return v

    def _filter(self, course: str | None, institution: str | None, types: list[str] | None) -> list[Item]:
        items = self.store.items
        if course:
            items = [i for i in items if course_matches(i.course, i.course_name, course)]
            if not items:
                quiet = [c for _, c in self.store.quiet_courses if course_matches(c, None, course)]
                if quiet:
                    raise ToolError(
                        f"{', '.join(quiet)} is in your calendar feed but has no deadlines published there. "
                        "Its deadlines are probably on the course outline or another site."
                    )
                known = ", ".join(sorted({c for _, c, _ in self.store.courses()}))
                raise ToolError(f"no course matches '{course}'. Known courses: {known}")
        if institution:
            inst = institution.strip().lower()
            items = [i for i in items if i.institution.startswith(inst)]
        if types:
            wanted = {t.lower() for t in types}
            if "exam" in wanted:
                wanted.add("midterm")
            items = [i for i in items if i.type in wanted]
        return items

    # ------------------------------------------------------------ query tools

    def _register_query_tools(self) -> None:
        range_props = {
            "range": {"type": "string", "enum": RANGES,
                      "description": "Named period. Use 'custom' with start_date/end_date for anything else."},
            "start_date": {"type": "string", "description": "YYYY-MM-DD, only for range=custom"},
            "end_date": {"type": "string", "description": "YYYY-MM-DD inclusive, only for range=custom"},
        }
        filter_props = {
            "course": {"type": "string", "description": "Course code or casual name, e.g. 'BU111', 'econ', 'cs'"},
            "institution": {"type": "string", "description": "e.g. 'waterloo' or 'laurier'"},
            "types": {"type": "array", "items": {"type": "string", "enum": ITEM_TYPES},
                      "description": "Only these item types ('exam' also includes midterms)"},
        }

        @self._tool(
            "list_deadlines",
            "List deadlines and exams in a period. Use for 'what's due today/tomorrow/this week', "
            "'anything before Friday', 'what's due for BU111', 'what exams do I have'.",
            {**range_props, **filter_props,
             "include_completed": {"type": "boolean", "description": "Also show items marked done"}},
            ["range"],
        )
        def list_deadlines(range: str = "this_week", start_date: str | None = None, end_date: str | None = None,
                           course: str | None = None, institution: str | None = None,
                           types: list[str] | None = None, include_completed: bool = False) -> dict:
            now = self.now_fn()
            start, end, label = resolve_range(range, now, self.tz, start_date, end_date)
            if range in ("today", "tomorrow"):
                label += f" ({fmt_day(start.date())})"
            items = [i for i in self._filter(course, institution, types) if start <= i.when < end]
            done = [i for i in items if i.completed]
            if not include_completed:
                items = [i for i in items if not i.completed]
            return {"period": label, "count": len(items), "items": [self.view(i, now) for i in items],
                    "hidden_completed": 0 if include_completed else len(done)}

        @self._tool(
            "next_deadlines",
            "The next upcoming incomplete deadlines/exams. Use for 'what's next', 'when's my next midterm'.",
            {"count": {"type": "integer", "description": "How many (default 3)"}, **filter_props},
        )
        def next_deadlines(count: int = 3, course: str | None = None, institution: str | None = None,
                           types: list[str] | None = None) -> dict:
            now = self.now_fn()
            items = [i for i in self._filter(course, institution, types) if i.when >= now and not i.completed]
            return {"count": min(len(items), count), "items": [self.view(i, now) for i in items[: max(1, count)]]}

        @self._tool(
            "find_items",
            "Find specific items from a casual description ('econ', 'cs a4', 'the case report'). "
            "Returns candidates with refs; use before acting on an item if unsure which one.",
            {"query": {"type": "string"}},
            ["query"],
        )
        def find_items(query: str) -> dict:
            now = self.now_fn()
            ranked = self._rank(query, now)
            return {"query": query, "candidates": [self.view(i, now) for _, i in ranked[:5]]}

        @self._tool(
            "get_courses",
            "List the student's courses, how many upcoming items each has, and courses whose deadlines "
            "aren't in the calendar feed.",
            {},
        )
        def get_courses() -> dict:
            now = self.now_fn()
            upcoming = Counter((i.institution, i.course) for i in self.store.items if i.when >= now and not i.completed)
            courses = [{"institution": inst, "course": code, "name": name, "upcoming": upcoming[(inst, code)]}
                       for inst, code, name in self.store.courses()]
            quiet = [{"institution": inst, "course": code,
                      "note": "no deadlines published in the calendar; check the course outline"}
                     for inst, code in self.store.quiet_courses]
            as_of = self.store.data_as_of
            return {"courses": courses, "courses_without_calendar_deadlines": quiet,
                    "data_last_synced": fmt_when(as_of, self.tz) if as_of else None}

        @self._tool(
            "get_workload",
            "Per-day load for a period and the busiest day. Use for 'what's my busiest day', "
            "'anything bad this week', 'how does next week look'.",
            range_props,
            ["range"],
        )
        def get_workload(range: str = "this_week", start_date: str | None = None,
                         end_date: str | None = None) -> dict:
            now = self.now_fn()
            start, end, label = resolve_range(range, now, self.tz, start_date, end_date)
            items = [i for i in self.store.items if start <= i.when < end and not i.completed]
            by_day: dict[str, list[Item]] = {}
            for i in items:
                by_day.setdefault(fmt_day(i.when.astimezone(self.tz).date()), []).append(i)
            days = [{"day": day, "count": len(its), "items": [f"{i.course} {i.title} ({self.ref(i)})" for i in its],
                     "exams": sum(i.type in ("exam", "midterm") for i in its)}
                    for day, its in by_day.items()]
            busiest = max(days, key=lambda d: (d["count"] + 2 * d["exams"]), default=None)
            return {"period": label, "total": len(items),
                    "exams": sum(i.type in ("exam", "midterm") for i in items),
                    "days": days, "busiest_day": busiest["day"] if busiest else None}

    # ------------------------------------------------------------ action tools

    REFRESH_COOLDOWN = timedelta(minutes=10)

    def _resolve(self, spec: str, *, want_completed: bool) -> Item | dict:
        """A ref ("i3f9a01c2") or a casual description ("econ") -> one item, or a
        dict asking the model to clarify. Prefers items near today that are
        in the state the action expects (e.g. not yet done for mark_complete)."""
        spec = (spec or "").strip()
        if re.fullmatch(r"i[0-9a-fA-F]{8}", spec):
            item = self._item_for_ref(spec)
            if item is None:
                raise ToolError(f"unknown ref '{spec}'; use find_items or list_deadlines first")
            return item
        now = self.now_fn()
        ranked = [(s, i) for s, i in self._rank(spec, now)
                  if i.completed == want_completed and i.when >= now - timedelta(days=7)]
        if not ranked:
            raise ToolError(f"nothing matches '{spec}'" + ("" if want_completed else " that isn't already done"))
        top = ranked[0][0]
        best = [i for s, i in ranked if s == top]
        soonest = sorted(best, key=lambda i: abs((i.when - now).total_seconds()))
        # one clear winner, or several equal matches where only one is due within 2 days
        near = [i for i in soonest if abs((i.when - now).total_seconds()) <= 2 * 86400]
        if len(best) == 1 or len(near) == 1:
            return near[0] if len(near) == 1 else best[0]
        return {"needs_clarification": True, "query": spec,
                "candidates": [self.view(i, now) for i in soonest[:4]],
                "instruction": "Ask the student which one they mean; do not act yet."}

    def _register_action_tools(self) -> None:
        item_prop = {"item": {"type": "string",
                              "description": "Item ref like 'i3f9a01c2', or the student's words like 'econ' or 'cs a4'"}}

        @self._tool("mark_complete", "Mark an item done ('I finished econ', 'mark BU111 done', 'done with that').",
                    item_prop, ["item"])
        def mark_complete(item: str) -> dict:
            found = self._resolve(item, want_completed=False)
            if isinstance(found, dict):
                return found
            now = self.now_fn()
            self.store.set_completed(found.id, True, now)
            return {"marked_complete": self.view(found, now)}

        @self._tool("mark_incomplete", "Undo a done mark ('actually I didn't finish it').", item_prop, ["item"])
        def mark_incomplete(item: str) -> dict:
            found = self._resolve(item, want_completed=True)
            if isinstance(found, dict):
                return found
            now = self.now_fn()
            self.store.set_completed(found.id, False, now)
            return {"marked_incomplete": self.view(found, now)}

        @self._tool("set_estimate", "Store how long an item will take ('CS will probably take 3 hours').",
                    {**item_prop, "hours": {"type": "number", "description": "Estimated hours of work left"}},
                    ["item", "hours"])
        def set_estimate(item: str, hours: float) -> dict:
            if not 0 < hours <= 200:
                raise ToolError("hours must be between 0 and 200")
            found = self._resolve(item, want_completed=False)
            if isinstance(found, dict):
                return found
            self.store.set_estimate(found.id, round(hours * 60))
            return {"estimate_saved": self.view(found, self.now_fn())}

        @self._tool(
            "prioritize",
            "Rank what to work on now ('what should I work on rn', 'what do I need to worry about'). "
            "Uses due times and the student's own time estimates; reports what's unknown.",
            {"horizon_days": {"type": "integer", "description": "How far ahead to consider (default 3)"}},
        )
        def prioritize(horizon_days: int = 3) -> dict:
            now = self.now_fn()
            horizon = now + timedelta(days=max(1, min(horizon_days, 14)))
            pool = [i for i in self.store.items
                    if not i.completed and now - timedelta(days=2) <= i.when <= horizon]
            ranked = []
            for i in pool:
                hours_left = (i.when - now).total_seconds() / 3600
                est = (i.estimate_minutes or 0) / 60
                slack = hours_left - (est or 1.0)  # unknown effort counted as ~1h, flagged below
                reasons = [f"{'due' if i.due else 'starts'} {fmt_relative(i.when, now)}"]
                reasons.append(f"you estimated {est:g}h" if est else "no time estimate")
                if i.type in ("exam", "midterm"):
                    reasons.append("exam: needs prep time")
                    slack -= 24
                if hours_left < 0:
                    reasons.append("overdue")
                ranked.append((slack, i, reasons))
            ranked.sort(key=lambda r: r[0])
            missing = [f"{i.course} {i.title} ({self.ref(i)})" for _, i, _ in ranked if not i.estimate_minutes]
            return {
                "order": [{**self.view(i, now), "reasons": reasons} for _, i, reasons in ranked],
                "unknown_estimates": missing,
                "note": "Grade weights and difficulty are unknown unless the student says them. "
                        "If estimates are missing, mention that and offer to track them.",
            }

        @self._tool("refresh_data", "Re-sync from Brightspace now ('refresh', 'check brightspace', "
                    "'sync my courses'). Limited to once every 10 minutes.", {})
        def refresh_data() -> dict:
            if self.refresh_fn is None:
                raise ToolError("refresh isn't available in this setup")
            now = self.now_fn()
            # the last sync of any kind (background syncs count), so the limit holds across texts
            last = max(filter(None, (self._last_refresh, self.store.last_sync_at)), default=None)
            if last and now - last < self.REFRESH_COOLDOWN:
                wait = self.REFRESH_COOLDOWN - (now - last)
                return {"refreshed": False,
                        "reason": f"synced {fmt_relative(last, now)}; try again in "
                                  f"{max(1, round(wait.total_seconds() / 60))} min"}
            before = {i.id: i.when for i in self.store.items}
            summary = self.refresh_fn()
            self._last_refresh = now
            self.store.load()
            after = {i.id: i.when for i in self.store.items}
            new = [i for i in self.store.items if i.id not in before]
            moved = [i for i in self.store.items if i.id in before and before[i.id] != i.when]
            return {"refreshed": True, "sync_summary": summary,
                    "new_items": [self.view(i, now) for i in new],
                    "moved_items": [self.view(i, now) for i in moved],
                    "removed_count": len(set(before) - set(after))}

    # ------------------------------------------------------------ reminders and settings

    MAX_REMINDER_AHEAD = timedelta(days=180)

    def reminder_ref(self, reminder) -> str:
        return "r" + reminder.id.replace("-", "")[:8]

    def _reminder_view(self, reminder, now: datetime) -> dict:
        v: dict[str, Any] = {"ref": self.reminder_ref(reminder), "at": fmt_when(reminder.fire_at, self.tz),
                             "when": fmt_relative(reminder.fire_at, now)}
        if reminder.note:
            v["note"] = reminder.note
        item = self.store.get(reminder.item_id) if reminder.item_id else None
        if item:
            v["about"] = f"{item.course} {item.title}"
        return v

    def _parse_local(self, value: str, now: datetime) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.strip().replace(" ", "T"))
        except (AttributeError, ValueError):
            raise ToolError("time must be local YYYY-MM-DDTHH:MM, e.g. 2026-10-05T20:00") from None
        fire_at = parsed.replace(tzinfo=self.tz) if parsed.tzinfo is None else parsed
        if fire_at <= now:
            raise ToolError(f"{fmt_when(fire_at, self.tz)} has already passed (it's {fmt_when(now, self.tz)} now). "
                            "If they meant the next one (e.g. PM, or tomorrow), use that time instead.")
        if fire_at - now > self.MAX_REMINDER_AHEAD:
            raise ToolError("reminders can be at most 6 months ahead")
        return fire_at

    @staticmethod
    def _clock(hhmm: str) -> str:
        hour, minute = (int(x) for x in hhmm.split(":"))
        return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"

    def _settings_view(self) -> dict:
        user = self.store.user
        minutes = user.due_reminder_minutes
        if not minutes:
            due = "off"
        elif minutes % 1440 == 0:
            due = f"{minutes // 1440} day{'s' if minutes != 1440 else ''} before each deadline"
        else:
            due = f"{minutes / 60:g} hour{'s' if minutes != 60 else ''} before each deadline"
        return {
            "morning_text": f"on, at {self._clock(user.briefing_time)}" if user.briefing_enabled else "off",
            "due_soon_reminders": due,
            "new_and_moved_deadline_alerts": "on" if user.alerts_enabled else "off",
            "quiet_hours": "10:00 PM to 8:00 AM (no automatic texts then, except the morning text and "
                           "reminders they asked for)",
        }

    def _register_reminder_tools(self) -> None:
        @self._tool(
            "create_reminder",
            "Set a reminder text ('remind me at 8 to start the essay', 'remind me about that tomorrow "
            "morning', 'remind me in 20 min'). Give either `at` or `in_minutes`.",
            {
                "at": {"type": "string",
                       "description": "Local time YYYY-MM-DDTHH:MM, worked out from the current local time"},
                "in_minutes": {"type": "integer", "description": "Minutes from now, for 'in 20 min'"},
                "item": {"type": "string",
                         "description": "Optional item ref or the student's words, if it's about a deadline"},
                "note": {"type": "string", "description": "Short note in the student's words, e.g. 'start the essay'"},
            },
        )
        def create_reminder(at: str | None = None, in_minutes: int | None = None, item: str | None = None,
                            note: str | None = None) -> dict:
            now = self.now_fn()
            if in_minutes is not None:
                if not 1 <= in_minutes <= 60 * 24 * 180:
                    raise ToolError("in_minutes must be between 1 and 259200")
                fire_at = now + timedelta(minutes=in_minutes)
            elif at:
                fire_at = self._parse_local(at, now)
            else:
                raise ToolError("no time given; ask the student when to remind them")
            found = None
            if item:
                found = self._resolve(item, want_completed=False)
                if isinstance(found, dict):
                    return found
            note = (note or "").strip()[:300] or None
            if found is None and note is None:
                raise ToolError("a reminder needs a note or an item; ask what it's for")
            reminder = self.store.add_reminder(fire_at, found.id if found else None, note, now)
            return {"reminder_set": self._reminder_view(reminder, now)}

        @self._tool("list_reminders", "The reminders the student has set that haven't gone off yet.", {})
        def list_reminders() -> dict:
            now = self.now_fn()
            return {"reminders": [self._reminder_view(r, now) for r in self.store.active_reminders()]}

        @self._tool(
            "cancel_reminder",
            "Cancel a reminder by its ref (from list_reminders or create_reminder), or 'all'.",
            {"reminder": {"type": "string", "description": "Reminder ref like 'r1a2b3c4d', or 'all'"}},
            ["reminder"],
        )
        def cancel_reminder(reminder: str) -> dict:
            now = self.now_fn()
            active = self.store.active_reminders()
            if reminder.strip().lower() == "all":
                chosen = active
            else:
                chosen = [r for r in active if self.reminder_ref(r) == reminder.strip().lower()]
                if not chosen:
                    raise ToolError(f"no active reminder '{reminder}'; call list_reminders first")
            for r in chosen:
                self.store.cancel_reminder(r.id)
            return {"cancelled": [self._reminder_view(r, now) for r in chosen]}

        @self._tool("get_settings", "The student's text settings: morning text, due-soon reminders, alerts.", {})
        def get_settings() -> dict:
            return {"settings": self._settings_view()}

        @self._tool(
            "update_settings",
            "Change text settings ('move my morning text to 7:30', 'stop the morning texts', 'remind me a day "
            "before things are due', 'turn off due-soon reminders', 'stop telling me about new deadlines').",
            {
                "morning_text_time": {"type": "string", "description": "24h local HH:MM, e.g. '07:30'"},
                "morning_text_on": {"type": "boolean"},
                "due_soon_hours_before": {"type": "number", "description": "Hours before each deadline; 0 = off"},
                "alerts_on": {"type": "boolean", "description": "Texts about new or moved deadlines"},
            },
        )
        def update_settings(morning_text_time: str | None = None, morning_text_on: bool | None = None,
                            due_soon_hours_before: float | None = None, alerts_on: bool | None = None) -> dict:
            changes: dict[str, Any] = {}
            if morning_text_time is not None:
                try:
                    clock = time.fromisoformat(morning_text_time.strip())
                except ValueError:
                    raise ToolError("morning_text_time must be HH:MM in 24h time, e.g. '07:30'") from None
                changes["briefing_time"], changes["briefing_enabled"] = f"{clock:%H:%M}", True
            if morning_text_on is not None:
                changes["briefing_enabled"] = morning_text_on
            if due_soon_hours_before is not None:
                if not 0 <= due_soon_hours_before <= 24 * 7:
                    raise ToolError("due_soon_hours_before must be between 0 and 168")
                changes["due_reminder_minutes"] = round(due_soon_hours_before * 60) or None
            if alerts_on is not None:
                changes["alerts_enabled"] = alerts_on
            if not changes:
                raise ToolError("nothing to change")
            self.store.update_settings(**changes)
            return {"updated": True, "settings": self._settings_view()}

    # ------------------------------------------------------------ matching

    _STOP = {"the", "my", "for", "i", "im", "finished", "finish", "done", "mark", "about", "remind", "me", "is",
             "it", "on", "due", "what", "when", "that", "with", "a", "an", "of", "to", "just", "completed", "submitted"}
    _ABBREV = {"a": "assignment", "asst": "assignment", "hw": "homework", "q": "quiz", "ww": "webwork",
               "mt": "midterm", "pres": "presentation"}

    def _tokens(self, query: str) -> list[str]:
        raw = re.findall(r"[a-z]+|\d+", query.lower())
        return [self._ABBREV.get(t, t) for t in raw if t not in self._STOP]

    def _score(self, item: Item, tokens: list[str]) -> int:
        score = 0
        code = item.course.lower()
        subject = _subject(item.course)
        number = code[len(subject):] if subject else ""
        name_words = re.findall(r"[a-z]+", (item.course_name or "").lower())
        title_words = re.findall(r"[a-z]+|\d+", item.title.lower())
        for t in tokens:
            if t in COURSE_ALIASES and subject in COURSE_ALIASES[t]:
                score += 3
            elif len(t) >= 2 and (code.startswith(t) or t == code):
                score += 3
            elif t.isdigit() and t == number:
                score += 3
            elif len(t) >= 4 and any(w.startswith(t) for w in name_words):
                score += 2
            if t in title_words or (len(t) >= 4 and any(w.startswith(t) for w in title_words)):
                score += 2
        return score

    def _rank(self, query: str, now: datetime) -> list[tuple[int, Item]]:
        tokens = self._tokens(query)
        scored = [(self._score(i, tokens), i) for i in self.store.items]
        scored = [(s, i) for s, i in scored if s > 0]

        def key(pair: tuple[int, Item]):
            s, i = pair
            upcoming = i.when >= now - timedelta(days=1)
            return (-s, not upcoming, abs((i.when - now).total_seconds()))
        return sorted(scored, key=key)
