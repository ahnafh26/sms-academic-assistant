"""Turn a real feed into a commit-safe fixture.

Keeps what the normalizer needs to be tested against reality (structure,
title patterns, suffixes, time representation, UID *shape*, course-code
*format*) and scrubs what identifies the student or their courses
(course names, descriptions, IDs in URLs/UIDs, attendees). Mapping is
deterministic per salt, so the same course maps to the same fake code
across snapshots and diffs still line up.

Always review the output by hand before committing it.
"""

from __future__ import annotations

import hashlib
import re
import string

from icalendar import Calendar

from .normalize import _TOOL_PREFIX, split_location, strip_role
from .rules import COURSE_CODE_STOPWORDS, Rules, build_rules

DROP_PROPS = {"ATTENDEE", "ORGANIZER", "CONTACT", "COMMENT", "ATTACH", "X-ALT-DESC", "GEO"}
KEEP_X_PROPS = {"X-WR-TIMEZONE", "X-MICROSOFT-CDO-BUSYSTATUS"}


class _Mapper:
    def __init__(self, salt: str, rules: Rules):
        self.salt = salt
        self.rules = rules
        self._names: dict[tuple[str, str], str] = {}

    def _h(self, kind: str, value: str) -> bytes:
        return hashlib.sha256(f"{self.salt}|{kind}|{value}".encode()).digest()

    def digits(self, text: str, min_len: int = 1) -> str:
        """Replace digit runs (of at least min_len) with same-length deterministic digits."""
        def sub(m: re.Match) -> str:
            h = self._h("digits", m.group(0))
            return "".join(str(b % 10) for b in h[: len(m.group(0))])
        return re.sub(r"\d{%d,}" % min_len, sub, text)

    def letters(self, text: str) -> str:
        h = self._h("letters", text)
        return "".join(string.ascii_uppercase[b % 26] for b in h[: len(text)])

    def course_codes(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            if m.group(1) in COURSE_CODE_STOPWORDS:
                return m.group(0)
            whole, start = m.group(0), m.start()
            subj_s, subj_e = m.span(1)
            num_s, num_e = m.span(2)
            return (
                whole[: subj_s - start] + self.letters(m.group(1)) + whole[subj_e - start : num_s - start]
                + self.digits(m.group(2)) + whole[num_e - start :]
            )
        return self.rules.course_code.sub(sub, text)

    def course_label(self, text: str) -> str:
        """LOCATION/CATEGORIES: keep code format + term, replace free-text names."""
        segments = re.split(r"(\s[-–—|:]\s)", text)
        out = []
        for seg in segments:
            if re.fullmatch(r"\s[-–—|:]\s", seg) or not seg.strip():
                out.append(seg)
            elif self.rules.course_code.search(seg):
                out.append(self.course_codes(seg))
            elif self.rules.term.fullmatch(seg.strip()):
                out.append(seg)
            else:
                out.append(self._name(seg.strip()))
        return "".join(out)

    def community_title(self, text: str) -> str:
        base, role = strip_role(text, self.rules)
        suffix = text[len(base):] if role != "plain" and text.startswith(base) else ""
        return self._pseudonym("Community event", base) + suffix

    def location(self, text: str, institution_name: str | None = None) -> str:
        """Handle D2L's "<venue> (<label>)" form so parentheses and the
        label-to-fake-name mapping both survive. The school's root-org label
        is public and needed for institution detection, so it's kept."""
        label, venue = split_location(text)
        is_root = institution_name and label.casefold() == institution_name.casefold()
        anon_label = label if is_root else self.course_label(label)
        return f"{self._pseudonym('Venue', venue)} ({anon_label})" if venue else anon_label

    def _name(self, original: str) -> str:
        return self._pseudonym("Course Name", original)

    def _pseudonym(self, kind: str, original: str) -> str:
        key = (kind, original)
        if key not in self._names:
            self._names[key] = f"{kind} {sum(1 for k in self._names if k[0] == kind) + 1}"
        return self._names[key]


def anonymize_ics(
    data: bytes, *, salt: str, rules: Rules | None = None, institution_name: str | None = None
) -> bytes:
    """institution_name: the school's root-org label; its events (maintenance
    windows etc.) keep their titles. Titles from other code-less org units
    (residence, clubs, programs) describe the student, so they're replaced."""
    rules = rules or build_rules()
    m = _Mapper(salt, rules)
    cal = Calendar.from_ical(data)

    for name in list(cal.keys()):
        if name == "X-WR-CALNAME":
            _set(cal, name, "Anonymized Calendar")
        elif name.startswith("X-") and name not in KEEP_X_PROPS:
            _set(cal, name, "[removed]")

    for ev in cal.walk("VEVENT"):
        label = split_location(str(ev["LOCATION"]))[0] if "LOCATION" in ev else ""
        community = bool(label) and not rules.course_code.search(label) and (
            label.casefold() != (institution_name or "").casefold()
        )
        for name in list(ev.keys()):
            value = ev[name]
            if isinstance(value, list):
                value = value[0]
            text = str(value)
            if name in DROP_PROPS:
                del ev[name]
            elif name == "UID":
                local, at, host = text.partition("@")  # host is the LMS server, not personal
                _set(ev, name, m.digits(local) + at + host)
            elif name == "SUMMARY":
                _set(ev, name, m.community_title(text) if community else m.course_codes(text))
            elif name == "LOCATION":
                _set(ev, name, m.location(text, institution_name))
            elif name == "CATEGORIES":
                cats = getattr(value, "cats", [text])
                del ev[name]
                ev.add(name, [m.course_label(str(c)) for c in cats])
            elif name == "DESCRIPTION":
                tool = _TOOL_PREFIX.match(text)  # keep "Quizzes:" etc. — the normalizer uses it
                prefix = f"{tool.group(1)}: " if tool else ""
                _set(ev, name, f"{prefix}[description removed: {len(text)} chars, {len(re.findall(r'https?://', text))} links]")
            elif name == "URL":
                _set(ev, name, m.digits(text, min_len=3))  # IDs only; keep "/d2l/" intact
            elif name.startswith("X-") and name not in KEEP_X_PROPS:
                _set(ev, name, "[removed]")
    return cal.to_ical()


def _set(component, name: str, value) -> None:
    params = getattr(component[name], "params", None) if name in component else None
    del component[name]
    component.add(name, value, parameters=dict(params) if params else None)
