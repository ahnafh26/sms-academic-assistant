"""Sign-up by text: a fixed state machine, no LLM.

new -> awaiting_consent -> awaiting_school -> awaiting_connection -> done

At any point after the school is known, pasting the Brightspace calendar
link straight into the text thread connects without the web page.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.orm import Session

from .config import get_settings
from .connect import connect_calendar
from .connectors.brightspace.institutions import load_institutions, match_institution
from .models import User
from .security import create_setup_token, setup_link

YES = {"YES", "Y", "YEP", "YEAH", "YA", "YUP", "OK", "OKAY", "SURE", "AGREE", "I AGREE", "ACCEPT"}
FEED_LINK = re.compile(r"https?://\S+/d2l/le/calendar/feed/\S+", re.I)
NEW_LINK = re.compile(r"\b(link|connect|new link|resend|again|expired)\b", re.I)
CHANGE_SCHOOL = re.compile(r"\b(wrong school|change school|different school|not my school)\b", re.I)


def _terms_url() -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/terms"


def welcome() -> list[str]:
    return [
        "Hey 👋 I'm your school assistant. Connect your courses once, then text me anything: "
        "what's due, what's next, \"I finished econ\".",
        f"Reply YES to agree to the terms ({_terms_url()}) and get started. Text STOP anytime to opt out.",
    ]


def school_question() -> str:
    return "What school do you go to?"


def _schools_we_support() -> str:
    names = [i.name.replace("University of ", "").replace(" University", "") for i in load_institutions().values()]
    return ", ".join(names[:-1]) + " and " + names[-1]


def connect_link_text(session: Session, user: User) -> str:
    inst = load_institutions()[user.institution]
    raw = create_setup_token(session, user, "connect_calendar")
    minutes = get_settings().setup_link_minutes
    return (f"Tap this link to connect {inst.lms_name} (expires in {minutes} min): {setup_link(raw)}\n\n"
            f"Or paste your {inst.lms_name} calendar Subscribe link right here.")


def handle_onboarding(session: Session, user: User, text: str, *, now: datetime | None = None) -> list[str]:
    step = user.onboarding_step

    link = FEED_LINK.search(text or "")
    if link and user.institution and step in ("awaiting_connection", "awaiting_school"):
        outcome = connect_calendar(session, user, link.group(0), now=now)
        return [] if outcome.ok else [outcome.problem]  # success text is sent by connect_calendar

    if step == "new":
        user.onboarding_step = "awaiting_consent"
        return welcome()

    if step == "awaiting_consent":
        if text.upper().strip(" .!") in YES:
            user.consent_at, user.terms_version = now, get_settings().terms_version
            user.onboarding_step = "awaiting_school"
            return [school_question()]
        return ["Reply YES to agree to the terms and get started, or STOP to opt out."]

    if step == "awaiting_school":
        matches = match_institution(text)
        if len(matches) == 1:
            inst = matches[0]
            user.institution, user.timezone = inst.slug, inst.timezone
            user.onboarding_step = "awaiting_connection"
            return [f"Got it, {inst.name}.", connect_link_text(session, user)]
        if len(matches) > 1:
            options = " or ".join(m.name for m in matches[:3])
            return [f"Did you mean {options}? Reply with the full name."]
        return [f"I don't support that school yet. Right now I work with Ontario schools on Brightspace: "
                f"{_schools_we_support()}. Which one do you go to?"]

    if step == "awaiting_connection":
        if CHANGE_SCHOOL.search(text):
            user.onboarding_step, user.institution = "awaiting_school", None
            return [school_question()]
        if NEW_LINK.search(text):
            return [connect_link_text(session, user)]
        inst = load_institutions()[user.institution]
        return [f"Almost there: tap the link I sent to connect {inst.lms_name}. "
                f"Reply LINK for a new one, or WRONG SCHOOL to change schools."]

    return []
