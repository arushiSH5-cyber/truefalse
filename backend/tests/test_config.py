import pytest

from app.core.config import Settings


def test_defaults_need_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.anthropic_api_key is None
    assert s.anthropic_model


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "some-model")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.anthropic_model == "some-model"
    assert s.telegram_bot_token is not None
    assert "secret-token" not in repr(s)
