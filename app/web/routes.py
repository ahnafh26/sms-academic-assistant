"""One-time connect page + terms. Opened from a link texted to the student."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..config import get_settings
from ..connect import connect_calendar
from ..connectors.brightspace.institutions import get_institution
from ..db import get_session, utcnow
from ..models import User
from ..security import check_setup_token

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")
router = APIRouter()
PURPOSE = "connect_calendar"


def _ctx(**extra) -> dict:
    s = get_settings()
    return {"assistant_name": s.assistant_name, "terms_version": s.terms_version, **extra}


def _logo(slug: str) -> str | None:
    return f"/static/logos/{slug}.png" if (WEB_DIR / "static" / "logos" / f"{slug}.png").exists() else None


def _problem_page(request: Request, problem: str) -> HTMLResponse:
    return templates.TemplateResponse(request, "message.html", _ctx(heading="Link not working", body=problem),
                                      status_code=410)


def _connect_page(request: Request, user: User, *, error: str | None = None, pasted: str | None = None,
                  status_code: int = 200) -> HTMLResponse:
    inst = get_institution(user.institution)
    return templates.TemplateResponse(request, "connect.html", _ctx(
        inst=inst, logo=_logo(inst.slug), calendar_url=inst.calendar_url, error=error, pasted=pasted),
        status_code=status_code)


@router.get("/s/{token}", response_class=HTMLResponse)
def connect_page(token: str, request: Request, session: Session = Depends(get_session)):
    check = check_setup_token(session, token, PURPOSE)
    if check.problem:
        return _problem_page(request, check.problem)
    user = session.get(User, check.token.user_id)
    return _connect_page(request, user)


@router.post("/s/{token}", response_class=HTMLResponse)
def connect_submit(token: str, request: Request, feed_url: str = Form(""), session: Session = Depends(get_session)):
    check = check_setup_token(session, token, PURPOSE)
    if check.problem:
        return _problem_page(request, check.problem)
    user = session.get(User, check.token.user_id)
    outcome = connect_calendar(session, user, feed_url)
    if not outcome.ok:
        return _connect_page(request, user, error=outcome.problem, pasted=feed_url, status_code=422)
    check.token.used_at = utcnow()
    return templates.TemplateResponse(request, "done.html", _ctx(summary=outcome.summary))


@router.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    return templates.TemplateResponse(request, "terms.html", _ctx())
