"""Tools the LLM calls to read and change the student's academic data.

Design rules (from docs/architecture.md):
- the LLM never sees or chooses a user/student id; the toolbox is bound
  to one student's store
- every fact the assistant states must come from a tool result
- dates are resolved and formatted here, never computed by the model
- items are referred to by short session refs (i1, i2, ...) so follow-ups
  like "mark that done" resolve to an exact record
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .data import Item, Store
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
        self._ref_by_id: dict[str, str] = {}
        self._id_by_ref: dict[str, str] = {}
        self._tools: dict[str, tuple[Callable[..., dict], dict]] = {}
        self._register_query_tools()

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
        if item.id not in self._ref_by_id:
            ref = f"i{len(self._ref_by_id) + 1}"
            self._ref_by_id[item.id] = ref
            self._id_by_ref[ref] = item.id
        return self._ref_by_id[item.id]

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
