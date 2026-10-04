import httpx
import pytest

from spike.config import FeedConfig, InstitutionConfig
from spike.fetch import FetchError, fetch_feed

INST = InstitutionConfig(
    slug="waterloo", name="UW", lms_name="LEARN", hosts=("learn.uwaterloo.ca",), timezone="America/Toronto"
)
FEED_URL = "https://learn.uwaterloo.ca/d2l/le/calendar/feed/user/feed.ics?token=SECRETTOKEN123"
ICS = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _feed(url: str = FEED_URL) -> FeedConfig:
    return FeedConfig(label="t", institution="waterloo", url=url)


def test_fetch_ok():
    body = fetch_feed(_feed(), INST, client=_client(lambda r: httpx.Response(200, content=ICS)))
    assert body == ICS


def test_rejects_disallowed_host():
    with pytest.raises(FetchError, match="allow-list"):
        fetch_feed(_feed("https://evil.example.com/feed.ics?token=x"), INST, client=_client(lambda r: None))


def test_rejects_http():
    with pytest.raises(FetchError, match="non-HTTPS"):
        fetch_feed(_feed(FEED_URL.replace("https", "http")), INST, client=_client(lambda r: None))


def test_redirect_off_host_refused():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://other.example.com/x.ics"})

    with pytest.raises(FetchError, match="allow-list"):
        fetch_feed(_feed(), INST, client=_client(handler))


def test_redirect_within_host_followed():
    def handler(request):
        if request.url.path.endswith("feed.ics"):
            return httpx.Response(302, headers={"location": "/d2l/le/calendar/feed/v2.ics"})
        return httpx.Response(200, content=ICS)

    assert fetch_feed(_feed(), INST, client=_client(handler)) == ICS


def test_html_login_page_detected_and_token_not_leaked():
    def handler(request):
        return httpx.Response(200, content=b"<html>Login</html>", headers={"content-type": "text/html"})

    with pytest.raises(FetchError) as exc:
        fetch_feed(_feed(), INST, client=_client(handler))
    assert "login page" in str(exc.value)
    assert "SECRETTOKEN123" not in str(exc.value)


def test_http_error_message_redacted():
    with pytest.raises(FetchError) as exc:
        fetch_feed(_feed(), INST, client=_client(lambda r: httpx.Response(403)))
    assert "403" in str(exc.value)
    assert "SECRETTOKEN123" not in str(exc.value)
