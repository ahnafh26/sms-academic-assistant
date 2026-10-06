import json
from datetime import timedelta

import pytest

from chat.tools import Toolbox
from conftest import ITEMS, NOW, TZ


@pytest.fixture
def tb(store):
    return Toolbox(store, TZ, now_fn=lambda: NOW)


def test_finished_econ_resolves_to_todays_item(tb, store):
    r = tb.call("mark_complete", {"item": "econ"})
    assert r["marked_complete"]["title"] == "Ch. 5 EOC Problems: Elasticity"
    assert r["marked_complete"]["status"] == "done"
    assert store.get("ec-hw").completed
    # done items disappear from default listings
    assert tb.call("list_deadlines", {"range": "today"})["hidden_completed"] == 1


def test_mark_by_ref_and_undo(tb, store):
    ref = tb.call("find_items", {"query": "cs a4"})["candidates"][0]["ref"]
    tb.call("mark_complete", {"item": ref})
    assert store.get("cs-a4").completed
    tb.call("mark_incomplete", {"item": ref})
    assert not store.get("cs-a4").completed


def test_nearest_match_wins_when_only_one_is_soon(tb):
    r = tb.call("mark_complete", {"item": "BU111"})  # quiz today + case report in November -> today's wins
    assert r["marked_complete"]["title"] == "Quiz 5"


def test_ambiguous_request_asks_instead_of_guessing(tmp_path):
    from chat.data import Store
    from conftest import raw_item
    rows = [
        raw_item("e1", "EC120", "Ch. 5 Decision Point", "assignment", due="2026-10-05T14:00:00Z"),
        raw_item("e2", "EC120", "Ch. 6 Decision Point", "assignment", due="2026-10-06T14:00:00Z"),
    ]
    path = tmp_path / "items.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    store = Store(path, tmp_path / "state.json")
    store.load()
    r = Toolbox(store, TZ, now_fn=lambda: NOW).call("mark_complete", {"item": "econ"})
    assert r["needs_clarification"] and len(r["candidates"]) == 2
    assert not any(i.completed for i in store.items)


def test_nothing_matches(tb):
    assert "nothing matches" in tb.call("mark_complete", {"item": "philosophy essay"})["error"]
    assert "unknown ref" in tb.call("mark_complete", {"item": "i99"})["error"]


def test_set_estimate(tb, store):
    r = tb.call("set_estimate", {"item": "cs", "hours": 3})
    assert r["estimate_saved"]["estimate"] == "3h" and store.get("cs-a4").estimate_minutes == 180
    assert "error" in tb.call("set_estimate", {"item": "cs", "hours": -1})


def test_prioritize_uses_estimates_and_reports_unknowns(tb):
    tb.call("set_estimate", {"item": "cs a4", "hours": 10})  # due 10 PM today, 13h left, 10h of work
    r = tb.call("prioritize", {"horizon_days": 2})
    order = [i["title"] for i in r["order"]]
    assert order[0] == "Assignment 4"
    assert "you estimated 10h" in r["order"][0]["reasons"]
    assert any("Quiz 5" in m for m in r["unknown_estimates"])
    assert "Grade weights" in r["note"]


def test_refresh_reports_changes_and_rate_limits(store, items_file):
    clock = {"now": NOW}

    def fake_refresh():
        data = json.loads(items_file.read_text(encoding="utf-8"))
        for row in data:
            if row["external_source_id"] == "ma-ww":
                row["due_at"] = "2026-10-08T03:59:00Z"  # moved a day later
        data.append({**ITEMS[0], "external_source_id": "new-1", "title": "Quiz 6", "due_at": "2026-10-09T20:00:00Z"})
        items_file.write_text(json.dumps(data), encoding="utf-8")
        return "laurier-all: 1 significant change"

    tb = Toolbox(store, TZ, now_fn=lambda: clock["now"], refresh_fn=fake_refresh)
    r = tb.call("refresh_data", {})
    assert r["refreshed"] and [i["title"] for i in r["new_items"]] == ["Quiz 6"]
    assert [i["title"] for i in r["moved_items"]] == ["WebWork 3"]
    clock["now"] = NOW + timedelta(minutes=3)
    again = tb.call("refresh_data", {})
    assert not again["refreshed"] and "try again in 7 min" in again["reason"]


def test_refresh_unavailable(tb):
    assert "isn't available" in tb.call("refresh_data", {})["error"]
