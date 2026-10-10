"""Raw-wire regressions adapted from the supplied actionstep reference."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import LATEST_PROTOCOL_VERSION
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from test_http_transport import (
    PROTOCOL_VERSION,
    SERVER_INFO_META_KEY,
    _modern_request,
    _post_modern,
    _result,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def loopback_environment(monkeypatch):
    monkeypatch.setenv("VLEX_MCP_HOST", "127.0.0.1")


def test_sdk_pins_the_2026_revision():
    assert LATEST_PROTOCOL_VERSION == PROTOCOL_VERSION
    assert MODERN_PROTOCOL_VERSIONS == (PROTOCOL_VERSION,)


async def test_modern_discovery_is_sessionless_and_has_sdk_capabilities():
    response = await _post_modern("server/discover")
    result = _result(response)
    assert "mcp-session-id" not in response.headers
    assert result["supportedVersions"] == [PROTOCOL_VERSION]
    assert result["resultType"] == "complete"
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"
    assert result["capabilities"] == {
        "prompts": {"listChanged": True},
        "resources": {"listChanged": True, "subscribe": True},
        "tools": {"listChanged": True},
    }
    assert "extensions" not in result["capabilities"]
    assert result["_meta"][SERVER_INFO_META_KEY]["name"] == "vlex"


async def test_stdio_client_negotiates_modern_and_legacy():
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "vlex_mcp.server"],
        cwd=str(REPO_ROOT),
        env={"VLEX_MCP_TRANSPORT": "stdio"},
    )
    async with Client(parameters, cache=None) as modern:
        assert modern.protocol_version == PROTOCOL_VERSION
        assert len((await modern.list_tools()).tools) == 14
    async with Client(parameters, mode="legacy", cache=None) as legacy:
        assert legacy.protocol_version == "2025-11-25"
        assert len((await legacy.list_tools()).tools) == 14


async def test_cacheable_results_are_complete_private_and_deterministic():
    methods = (
        "tools/list",
        "tools/list",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
    )
    results = [_result(await _post_modern(method)) for method in methods]
    for result in results:
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "private"
    first, second, prompts, resources, templates = results
    assert first == second
    assert len(first["tools"]) == 14
    assert all(tool["inputSchema"]["type"] == "object" for tool in first["tools"])
    assert prompts["prompts"] == []
    assert resources["resources"] == []
    assert templates["resourceTemplates"] == []


async def test_unknown_tool_stays_an_is_error_result():
    result = _result(await _post_modern("tools/call", {"name": "does_not_exist", "arguments": {}}))
    assert result["isError"] is True


async def test_modern_http_enforces_routing_headers():
    headers, _ = _modern_request("tools/call", {"name": "search_authorities", "arguments": {}})
    assert headers["mcp-protocol-version"] == PROTOCOL_VERSION
    assert headers["mcp-method"] == "tools/call"
    assert headers["mcp-name"] == "search_authorities"
    mismatch = await _post_modern("tools/list", header_overrides={"mcp-method": "resources/list"})
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == -32020
    named_mismatch = await _post_modern(
        "tools/call",
        {"name": "search_authorities", "arguments": {}},
        header_overrides={"mcp-name": "retrieve_legal_document"},
    )
    assert named_mismatch.status_code == 400
    assert named_mismatch.json()["error"]["code"] == -32020


async def test_modern_http_enforces_versions_and_method_errors():
    unsupported = await _post_modern("tools/list", protocol_version="2099-01-01")
    assert unsupported.status_code == 400
    assert unsupported.json()["error"] == {
        "code": -32022,
        "message": "Unsupported protocol version",
        "data": {"supported": [PROTOCOL_VERSION], "requested": "2099-01-01"},
    }
    unknown = await _post_modern("example/unknown")
    assert unknown.status_code == 404
    assert unknown.json()["error"] == {
        "code": -32601,
        "message": "Method not found",
        "data": "example/unknown",
    }
