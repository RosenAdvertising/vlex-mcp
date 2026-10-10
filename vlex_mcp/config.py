"""Environment-only configuration for the vLex MCP server."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(ValueError):
    """Raised when server configuration is missing or invalid."""


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number greater than zero.") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero.")
    return value


def _nonnegative_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a non-negative integer.") from exc
    if value < 0:
        raise ConfigError(f"{name} must be a non-negative integer.")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated settings loaded exclusively from environment variables."""

    api_key: str
    timeout_seconds: float = 30.0
    max_retries: int = 3
    backoff_base_seconds: float = 0.5
    max_backoff_seconds: float = 8.0
    research_base_url: str = "https://api.icbg.io/research/v1"
    vincent_base_url: str = "https://api.icbg.io/vincent/v1"
    public_base_url: str = "https://api.icbg.io"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Load and validate settings without reading a dotenv file."""
        env = os.environ if environ is None else environ
        api_key = env.get("VLEX_API_KEY", "").strip()
        if not api_key:
            raise ConfigError(
                "Missing required environment variable VLEX_API_KEY. "
                "Set it to a subscription key from developer.vlex.com."
            )

        backoff_base = _positive_float(env, "VLEX_BACKOFF_BASE_SECONDS", 0.5)
        max_backoff = _positive_float(env, "VLEX_MAX_BACKOFF_SECONDS", 8.0)
        if max_backoff < backoff_base:
            raise ConfigError(
                "VLEX_MAX_BACKOFF_SECONDS must be greater than or equal to "
                "VLEX_BACKOFF_BASE_SECONDS."
            )

        return cls(
            api_key=api_key,
            timeout_seconds=_positive_float(env, "VLEX_HTTP_TIMEOUT_SECONDS", 30.0),
            max_retries=_nonnegative_int(env, "VLEX_MAX_RETRIES", 3),
            backoff_base_seconds=backoff_base,
            max_backoff_seconds=max_backoff,
        )
