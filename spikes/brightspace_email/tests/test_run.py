import json
from pathlib import Path

from mailspike import __main__ as cli
from mailspike.report import link_shape, subject_shape

FIXTURES = Path(__file__).parent / "fixtures"


def test_run_writes_events_and_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli, "load_hosts", lambda: (
        {"learn.example.ca": "waterloo", "mylearningspace.example.ca": "laurier"},
        {"waterloo": "America/Toronto", "laurier": "America/Toronto"}))
    assert cli.main(["run", "--inbox", str(FIXTURES)]) == 0
    events = json.loads((tmp_path / "out" / "email_events.json").read_text(encoding="utf-8"))
    assert sorted(e["kind"] for e in events) == ["announcement", "due_soon", "feedback", "grade"]
    report = (tmp_path / "reports" / "EMAIL_REPORT.md").read_text(encoding="utf-8")
    assert "Brightspace notifications: **4**" in report and "deadline change" in report
    assert "rec@example.ca: Intramural registration closes Friday" in report
    assert "5 emails -> 4 Brightspace notifications" in capsys.readouterr().out


def test_empty_inbox(tmp_path, capsys):
    assert cli.main(["run", "--inbox", str(tmp_path)]) == 1
    assert "No .eml files" in capsys.readouterr().out


def test_real_registry_has_both_schools():
    hosts, _ = cli.load_hosts()
    assert hosts["learn.uwaterloo.ca"] == "waterloo" and hosts["mylearningspace.wlu.ca"] == "laurier"


def test_shapes():
    assert subject_shape("Grade released - CS 135 - Fall 2026: Assignment 4") == "Grade released - CODE - Fall #: Assignment #"
    assert link_shape("https://x.ca/d2l/le/news/658593/123/view") == "x.ca/d2l/le/news/#/#/view"
