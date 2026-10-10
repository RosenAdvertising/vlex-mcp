"""Shared offline fixtures for the vLex MCP test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from vlex_mcp.client import VLexClient
from vlex_mcp.config import Settings
from vlex_mcp.server import set_client


@pytest.fixture
async def mocked_vlex_client() -> AsyncIterator[VLexClient]:
    """Install a vLex client whose network traffic must be intercepted by respx."""
    async with httpx.AsyncClient() as http_client:
        client = VLexClient(
            Settings(
                api_key="test-subscription-key",
                max_retries=2,
                backoff_base_seconds=0.01,
                max_backoff_seconds=0.02,
            ),
            http_client=http_client,
        )
        set_client(client)
        try:
            yield client
        finally:
            set_client(None)
