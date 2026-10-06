from pathlib import Path

from mailspike.parse import load_inbox, parse_eml

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return parse_eml((FIXTURES / name).read_bytes(), FIXTURES / name)


def test_html_email_text_and_links():
    e = _load("synthetic_grade.eml")
    assert e.was_html and e.sender == "noreply@learn.example.ca" and e.sender_name == "LEARN"
    assert e.subject == "Grade released - CS 135 - Fall 2026: Assignment 4"
    assert "Grade: 42 / 50" in e.text and "color:red" not in e.text  # <style> stripped
    assert e.links == ["https://learn.example.ca/d2l/lms/grades/my_grades/main.d2l?ou=411111"]
    assert e.sent_at.isoformat() == "2026-10-14T15:02:11-04:00"
    assert e.message_id == "<grade-1@learn.example.ca>"


def test_plain_email_links_from_text():
    e = _load("synthetic_announcement.eml")
    assert not e.was_html
    assert e.links == ["https://mylearningspace.example.ca/d2l/le/news/658593/123456/view"]
    assert "extended to Sunday, October 18, 2026 at 11:59 PM" in e.text


def test_load_inbox_sorted_oldest_first():
    emails = load_inbox(FIXTURES)
    assert len(emails) == 5
    assert [e.message_id for e in emails][0] == "<due-1@mylearningspace.example.ca>"
