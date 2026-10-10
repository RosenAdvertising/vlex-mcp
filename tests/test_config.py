"""Tests for environment-only startup configuration."""

from __future__ import annotations

import pytest

from vlex_mcp.config import ConfigError, Settings


def test_settings_load_required_key_and_retry_values() -> None:
    settings = Settings.from_env(
        {
            "VLEX_API_KEY": " secret ",
            "VLEX_HTTP_TIMEOUT_SECONDS": "12",
            "VLEX_MAX_RETRIES": "4",
            "VLEX_BACKOFF_BASE_SECONDS": "1",
            "VLEX_MAX_BACKOFF_SECONDS": "5",
        }
    )

    assert settings.api_key == "secret"
    assert settings.timeout_seconds == 12
    assert settings.max_retries == 4


def test_settings_reject_missing_credential_by_name() -> None:
    with pytest.raises(ConfigError, match="VLEX_API_KEY"):
        Settings.from_env({})


def test_startup_without_credentials_runs_stdio(monkeypatch, capsys) -> None:
    from vlex_mcp import server
    from vlex_mcp.runtime import MCPServer

    seen = []
    monkeypatch.setattr(MCPServer, "run", lambda self, **kwargs: seen.append(kwargs))
    server.main()
    assert seen == [{"transport": "stdio"}]
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("VLEX_HTTP_TIMEOUT_SECONDS", "0"),
        ("VLEX_MAX_RETRIES", "-1"),
        ("VLEX_BACKOFF_BASE_SECONDS", "not-a-number"),
    ],
)
def test_settings_reject_invalid_http_values(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        Settings.from_env({"VLEX_API_KEY": "test", name: value})
