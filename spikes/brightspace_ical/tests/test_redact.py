import pytest

from spike import redact

URL = "https://learn.uwaterloo.ca/d2l/le/calendar/feed/user/feed.ics?token=abc123SECRETxyz789"


@pytest.fixture(autouse=True)
def _clean_registry():
    redact.clear_registry()
    yield
    redact.clear_registry()


def test_pattern_masks_unregistered_token():
    out = redact.redact(f"GET {URL} failed")
    assert "abc123SECRETxyz789" not in out
    assert "token=REDACTED" in out


def test_registered_token_masked_even_without_url_context():
    redact.register_url(URL)
    out = redact.redact("server echoed abc123SECRETxyz789 in an error page")
    assert "abc123SECRETxyz789" not in out


def test_registered_url_replaced_with_safe_form():
    redact.register_url(URL)
    out = redact.redact(f"fetching {URL}")
    assert out == "fetching https://learn.uwaterloo.ca/d2l/le/calendar/feed/user/feed.ics?token=REDACTED"


def test_safe_url_keeps_host_and_path():
    assert redact.safe_url(URL).startswith("https://learn.uwaterloo.ca/d2l/le/calendar/feed/")
    assert "abc123" not in redact.safe_url(URL)


def test_non_secret_text_untouched():
    assert redact.redact("CS 135 Assignment 4 - Due") == "CS 135 Assignment 4 - Due"
