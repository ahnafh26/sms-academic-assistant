from collections import Counter
from pathlib import Path

import httpx
import pytest

from app.connectors.brightspace.fetch import FetchError, fetch_feed, validate_feed_url
from app.connectors.brightspace.institutions import get_institution, load_institutions, match_institution
from app.connectors.brightspace.normalize import normalize_events
from app.connectors.brightspace.parse import parse_ics

FIXTURES = Path(__file__).parent / "fixtures"
LAURIER = get_institution("laurier")
FEED = "https://mylearningspace.wlu.ca/d2l/le/calendar/feed/user/feed.ics?token=SECRET999"


def test_registry_has_all_13_schools_with_aliases():
    insts = load_institutions()
    assert len(insts) == 13 and all(i.aliases for i in insts.values())


@pytest.mark.parametrize("text,slug", [
    ("Waterloo", "waterloo"), ("uw", "waterloo"), ("I go to laurier", "laurier"), ("WLU", "laurier"),
    ("mac", "mcmaster"), ("ryerson", "tmu"), ("Queen's", "queens"), ("uwo", "western"),
    ("university of guelph", "guelph"), ("carleton u", "carleton"),
])
def test_match_institution(text, slug):
    assert [i.slug for i in match_institution(text)][:1] == [slug]


def test_match_institution_no_match():
    assert match_institution("harvard") == [] and match_institution("") == []


def test_real_laurier_feed_normalizes_like_the_spike():
    items = normalize_events(parse_ics((FIXTURES / "real_laurier_anon.ics").read_bytes()).events, LAURIER)
    actionable = [i for i in items if i.category in ("deliverable", "exam") and i.in_term is not False
                  and (i.due_at or i.start_at or "") >= "2026-08-15"]
    assert len(items) == 151 and len(actionable) == 52
    assert Counter(i.org_kind for i in items)["institution"] == 77


@pytest.mark.parametrize("url,problem", [
    ("", "Paste the link"), ("hello", "doesn't look like a link"),
    ("http://mylearningspace.wlu.ca/d2l/le/calendar/feed/user/feed.ics?token=x", "https://"),
    ("https://learn.uwaterloo.ca/d2l/le/calendar/feed/user/feed.ics?token=x", "MyLearningSpace links start with"),
    ("https://mylearningspace.wlu.ca/d2l/home", "not the calendar feed"),
    ("https://mylearningspace.wlu.ca/d2l/le/calendar/feed/user/feed.ics", "missing its private token"),
])
def test_validate_feed_url(url, problem):
    assert problem in validate_feed_url(url, LAURIER)


def test_validate_feed_url_ok():
    assert validate_feed_url(f"  {FEED} ", LAURIER) is None


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_ok_and_login_page_detected():
    ics = (FIXTURES / "synthetic_before.ics").read_bytes()
    assert fetch_feed(FEED, LAURIER, client=_client(lambda r: httpx.Response(200, content=ics))) == ics
    with pytest.raises(FetchError, match="not a calendar") as exc:
        fetch_feed(FEED, LAURIER, client=_client(lambda r: httpx.Response(200, content=b"<html>login</html>")))
    assert "SECRET999" not in str(exc.value)


def test_fetch_refuses_redirect_off_host():
    with pytest.raises(FetchError, match="not allowed"):
        fetch_feed(FEED, LAURIER, client=_client(lambda r: httpx.Response(302, headers={"location": "https://evil.example/x"})))
