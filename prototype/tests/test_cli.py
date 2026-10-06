"""Smoke test of the REPL wiring with a fake model (no network)."""

import builtins

from chat import __main__ as cli
from chat.llm import AssistantTurn


class OneShotModel:
    def __init__(self, *a, **k):
        pass

    def complete(self, messages, tools):
        return AssistantTurn("Nothing to worry about right now.")


def test_repl_session(monkeypatch, items_file, tmp_path, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("ITEMS_PATH", str(items_file))
    monkeypatch.setenv("STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(cli, "OpenAICompatModel", OneShotModel)
    inputs = iter(["/help", "hello", "/quit"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(inputs))
    assert cli.main(["--now", "2026-10-05T09:00:00-04:00"]) == 0
    out = capsys.readouterr().out
    assert "7 deadlines" in out and "MATH" not in out.split("courses:")[1].split("\n")[0]
    assert "bot > Nothing to worry about right now." in out


def test_missing_key_message(monkeypatch, capsys):
    def no_key():
        raise cli.SettingsError("No LLM_API_KEY set")

    monkeypatch.setattr(cli, "load_settings", no_key)
    assert cli.main([]) == 2
    assert "Setup needed" in capsys.readouterr().out
