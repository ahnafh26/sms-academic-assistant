from chat.data import Store
from conftest import NOW


def test_only_actionable_items_loaded(store):
    ids = {i.id for i in store.items}
    assert ids == {"bu-quiz5", "cs-a4", "ec-hw", "ma-ww", "ec-mid", "bu-case", "old-done"}
    assert [i.id for i in store.items][:3] == ["old-done", "bu-quiz5", "cs-a4"]  # sorted by time


def test_courses(store):
    assert ("waterloo", "CS135", None) in store.courses()
    assert ("laurier", "EC120", "Intro To Microeconomics") in store.courses()


def test_completion_and_estimates_persist_across_reload(store, items_file, tmp_path):
    store.set_completed("ec-hw", True, NOW)
    store.set_estimate("cs-a4", 180)
    again = Store(items_file, tmp_path / "state.json")
    again.load()
    assert again.get("ec-hw").completed
    assert again.get("cs-a4").estimate_minutes == 180
    again.set_completed("ec-hw", False, NOW)
    assert not again.get("ec-hw").completed
