import pytest

from chat import config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)


def test_gemini_preset(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_API_KEY", "k")
    s = config.load_settings(tmp_path / "missing.env")
    assert s.provider == "gemini" and "generativelanguage" in s.base_url and s.model


def test_ollama_needs_no_key(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    s = config.load_settings(tmp_path / "missing.env")
    assert s.base_url.startswith("http://localhost:11434") and s.api_key == "not-needed"


def test_missing_key_explains_setup(tmp_path):
    with pytest.raises(config.SettingsError, match="prototype/.env"):
        config.load_settings(tmp_path / "missing.env")


def test_env_file_loaded_and_model_override(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=groq\nLLM_API_KEY='abc'\nLLM_MODEL=some-model\n", encoding="utf-8")
    s = config.load_settings(env)
    assert (s.provider, s.api_key, s.model) == ("groq", "abc", "some-model")


def test_unknown_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(config.SettingsError, match="LLM_PROVIDER"):
        config.load_settings(tmp_path / "missing.env")
