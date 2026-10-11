"""Offline checks for the stateless HTTP entry point and its CLI selection."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from vlex_mcp import __version__, server
from vlex_mcp.client import VLexClient

PROTOCOL_VERSION = "2026-07-28"
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"


def _modern_request(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    protocol_version: str = PROTOCOL_VERSION,
    request_id: int = 1,
    client_name: str = "vlex-http-test",
) -> tuple[dict[str, str], dict[str, Any]]:
    request_params = dict(params or {})
    request_params["_meta"] = {
        PROTOCOL_VERSION_META_KEY: protocol_version,
        CLIENT_CAPABILITIES_META_KEY: {},
        CLIENT_INFO_META_KEY: {"name": client_name, "version": "0"},
    }
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": protocol_version,
        "mcp-method": method,
    }
    if method in ("tools/call", "prompts/get"):
        headers["mcp-name"] = str(request_params["name"])
    elif method == "resources/read":
        headers["mcp-name"] = str(request_params["uri"])
    return headers, {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": request_params,
    }


def _result(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    if response.headers["content-type"].startswith("text/event-stream"):
        payload = json.loads(
            next(
                line[5:].strip()
                for line in reversed(response.text.splitlines())
                if line.startswith("data:")
            )
        )
    else:
        payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    return payload["result"]


@pytest.fixture(autouse=True)
def transport_environment(monkeypatch):
    for variable in (
        "VLEX_MCP_TRANSPORT",
        "VLEX_MCP_HOST",
        "PORT",
        "VLEX_MCP_ALLOWED_HOSTS",
        "VLEX_MCP_ALLOWED_ORIGINS",
    ):
        monkeypatch.delenv(variable, raising=False)


@asynccontextmanager
async def _http_client():
    app = server.create_serve_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8080",
        ) as client:
            yield client


async def _post_modern(method: str, params: dict[str, Any] | None = None, **kwargs):
    header_overrides = kwargs.pop("header_overrides", {})
    headers, body = _modern_request(method, params, **kwargs)
    headers.update(header_overrides)
    async with _http_client() as client:
        return await client.post("/mcp", headers=headers, json=body)


async def test_http_catalog_equals_stdio_server_catalog():
    repo_root = (await asyncio.to_thread(Path(__file__).resolve)).parents[1]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "vlex_mcp.server"],
        cwd=str(repo_root),
        env={"VLEX_MCP_TRANSPORT": "stdio"},
    )
    async with Client(parameters, cache=None) as stdio_server:
        expected = {
            tool.name: tool.input_schema for tool in (await stdio_server.list_tools()).tools
        }
    result = _result(await _post_modern("tools/list"))
    actual = {tool["name"]: tool["inputSchema"] for tool in result["tools"]}
    assert actual == expected
    assert len(actual) == 14


async def test_read_tool_over_http_uses_existing_vendor_mock(mocked_vlex_client: VLexClient):
    del mocked_vlex_client
    payload = {"results": [{"title": "Example v. Case"}], "error": {"has": False}}
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://api.icbg.io/research/v1/search").respond(200, json=payload)
        result = _result(
            await _post_modern(
                "tools/call",
                {"name": "search_authorities", "arguments": {"query": "fair use"}},
                header_overrides={"Ocp-Apim-Subscription-Key": "untrusted-request-key"},
            )
        )
    assert result["isError"] is False
    assert result["structuredContent"]["results"] == payload["results"]
    assert route.call_count == 1
    assert route.calls.last.request.headers["Ocp-Apim-Subscription-Key"] == "test-subscription-key"


async def test_two_concurrent_requests_have_distinct_connections_and_one_lifespan(monkeypatch):
    lowlevel = server.mcp._lowlevel_server
    original_entry = lowlevel._request_handlers["tools/list"]
    connections = []
    states = []
    events = []

    @asynccontextmanager
    async def lifespan(_server):
        events.append("start")
        try:
            yield {"shared": object()}
        finally:
            events.append("stop")

    async def observe(ctx, params):
        connections.append(ctx.session)
        states.append(ctx.lifespan_context)
        return await original_entry.handler(ctx, params)

    monkeypatch.setattr(lowlevel, "lifespan", lifespan)
    monkeypatch.setitem(
        lowlevel._request_handlers, "tools/list", replace(original_entry, handler=observe)
    )
    async with _http_client() as client:
        requests = [_modern_request("tools/list", client_name=name) for name in ("first", "second")]
        responses = await asyncio.gather(
            *[client.post("/mcp", headers=headers, json=body) for headers, body in requests]
        )
        assert events == ["start"]
    assert events == ["start", "stop"]
    assert len(connections) == 2
    assert connections[0] is not connections[1]
    assert [connection.client_params.client_info.name for connection in connections] == [
        "first",
        "second",
    ]
    assert states[0] is states[1]
    assert all("mcp-session-id" not in response.headers for response in responses)
    assert _result(responses[0]) == _result(responses[1])


def test_default_transport_keeps_existing_stdio_call(monkeypatch):
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: calls.append(kwargs))
    assert server._requested_transport() == "stdio"
    server.main()
    assert calls == [{"transport": "stdio"}]


@pytest.mark.parametrize("value", ["", "  ", " STDIO "])
def test_transport_normalizes_stdio(monkeypatch, value):
    monkeypatch.setenv("VLEX_MCP_TRANSPORT", value)
    assert server._requested_transport() == "stdio"


def test_empty_transport_selects_stdio_startup(monkeypatch):
    calls = []
    monkeypatch.setenv("VLEX_MCP_TRANSPORT", "")
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: calls.append(kwargs))
    server.main()
    assert calls == [{"transport": "stdio"}]


def test_unknown_transport_exits_with_both_options(monkeypatch):
    monkeypatch.setenv("VLEX_MCP_TRANSPORT", "bogus")
    monkeypatch.setattr(server.mcp, "run", lambda **_kwargs: None)
    with pytest.raises(SystemExit, match=r"VLEX_MCP_TRANSPORT.*bogus.*stdio.*streamable-http"):
        server.main()


def test_http_transport_dispatch(monkeypatch):
    calls = []

    async def serve():
        calls.append("http")

    monkeypatch.setenv("VLEX_MCP_TRANSPORT", " STREAMABLE-HTTP ")
    monkeypatch.setattr(server, "_serve_streamable_http", serve)
    server.main()
    assert calls == ["http"]


def test_host_and_port_defaults_and_overrides(monkeypatch):
    assert server._host() == "127.0.0.1"
    assert server._port() == 8080
    monkeypatch.setenv("VLEX_MCP_HOST", " 0.0.0.0 ")
    monkeypatch.setenv("PORT", " 9000 ")
    assert server._host() == "0.0.0.0"
    assert server._port() == 9000


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_host_yields_loopback_default(monkeypatch, value):
    monkeypatch.setenv("VLEX_MCP_HOST", value)
    assert server._host() == "127.0.0.1"
    assert server._transport_security() is None


def test_invalid_port_exits_clearly(monkeypatch):
    monkeypatch.setenv("PORT", "bogus")
    with pytest.raises(SystemExit, match="PORT must be an integer"):
        server._port()


def test_server_imports_when_distribution_metadata_is_missing(monkeypatch):
    import importlib.metadata

    def missing(_name):
        raise importlib.metadata.PackageNotFoundError(_name)

    original = sys.modules.pop("vlex_mcp.server", None)
    monkeypatch.setattr(importlib.metadata, "version", missing)
    try:
        fresh = importlib.import_module("vlex_mcp.server")
        assert fresh.__name__ == "vlex_mcp.server"
        assert fresh.__version__ == __version__
    finally:
        sys.modules.pop("vlex_mcp.server", None)
        if original is not None:
            sys.modules["vlex_mcp.server"] = original


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_uses_sdk_security_defaults(monkeypatch, host):
    monkeypatch.setenv("VLEX_MCP_HOST", host)
    assert server._transport_security() is None


def test_uppercase_localhost_is_treated_as_non_loopback(monkeypatch):
    monkeypatch.setenv("VLEX_MCP_HOST", "LOCALHOST")
    assert server._host() == "LOCALHOST"
    with pytest.raises(SystemExit, match="VLEX_MCP_ALLOWED_HOSTS"):
        server.create_serve_app()


@pytest.mark.parametrize("allowed_hosts", [None, "", " , "])
def test_non_loopback_requires_allowed_hosts(monkeypatch, allowed_hosts):
    monkeypatch.setenv("VLEX_MCP_HOST", "0.0.0.0")
    if allowed_hosts is not None:
        monkeypatch.setenv("VLEX_MCP_ALLOWED_HOSTS", allowed_hosts)
    with pytest.raises(SystemExit, match="VLEX_MCP_ALLOWED_HOSTS"):
        server.create_serve_app()


async def test_explicit_host_and_origin_allowlists(monkeypatch):
    monkeypatch.setenv("VLEX_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("VLEX_MCP_ALLOWED_HOSTS", " 127.0.0.1:8080, mcp.example.test:* , ")
    monkeypatch.setenv("VLEX_MCP_ALLOWED_ORIGINS", " https://app.example.test, ")
    settings = server._transport_security()
    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == ["127.0.0.1:8080", "mcp.example.test:*"]
    assert settings.allowed_origins == ["https://app.example.test"]
    async with _http_client() as client:
        headers, body = _modern_request("tools/list")
        bad_host = await client.post("/mcp", headers={**headers, "host": "evil.test"}, json=body)
        bad_origin = await client.post(
            "/mcp",
            headers={**headers, "origin": "https://evil.test"},
            json=body,
        )
        good_origin = await client.post(
            "/mcp",
            headers={**headers, "origin": "https://app.example.test"},
            json=body,
        )
    assert bad_host.status_code == 421
    assert bad_origin.status_code == 403
    assert len(_result(good_origin)["tools"]) == 14


async def test_origin_is_rejected_when_optional_allowlist_is_empty(monkeypatch):
    monkeypatch.setenv("VLEX_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("VLEX_MCP_ALLOWED_HOSTS", "127.0.0.1:8080")
    response = await _post_modern("tools/list", header_overrides={"origin": "https://evil.test"})
    assert response.status_code == 403


async def test_loopback_rejects_untrusted_host_and_origin():
    bad_host = await _post_modern("tools/list", header_overrides={"host": "evil.test"})
    bad_origin = await _post_modern("tools/list", header_overrides={"origin": "https://evil.test"})
    assert bad_host.status_code == 421
    assert bad_origin.status_code == 403


@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_http_rejects_get_and_delete(method):
    async with _http_client() as client:
        response = await client.request(
            method, "/mcp", headers={"mcp-protocol-version": PROTOCOL_VERSION}
        )
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


async def test_discovery_has_protocol_and_complete_server_identity():
    response = await _post_modern("server/discover")
    result = _result(response)
    assert "mcp-session-id" not in response.headers
    assert PROTOCOL_VERSION in result["supportedVersions"]
    info = result["_meta"][SERVER_INFO_META_KEY]
    assert info["name"] == "vlex"
    assert info["title"] == "vLex Legal Research"
    assert info["version"] == __version__


async def test_same_endpoint_serves_legacy_and_modern_catalogs():
    async with _http_client() as client:
        legacy = await client.post(
            "/mcp",
            headers={
                "accept": "application/json, text/event-stream",
                "mcp-protocol-version": "2025-11-25",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        headers, body = _modern_request("tools/list")
        modern = await client.post("/mcp", headers=headers, json=body)
    assert _result(legacy)["tools"] == _result(modern)["tools"]
    assert "mcp-session-id" not in legacy.headers


async def test_vendor_cancellation_propagates_through_tool_boundary(monkeypatch):
    class CancelledClient:
        async def request_json(self, *_args, **_kwargs):
            raise asyncio.CancelledError

    monkeypatch.setattr(server, "_client", CancelledClient())
    with pytest.raises(asyncio.CancelledError):
        await server.mcp.call_tool("search_authorities", {"query": "fair use"})


async def test_client_disconnect_cancels_running_vendor_call(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class WaitingClient:
        async def request_json(self, *_args, **_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    monkeypatch.setattr(server, "_client", WaitingClient())
    headers, body = _modern_request(
        "tools/call",
        {"name": "search_authorities", "arguments": {"query": "fair use"}},
    )
    incoming = asyncio.Queue()
    incoming.put_nowait(
        {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
    )
    outgoing = []

    async def send(message):
        outgoing.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "root_path": "",
        "server": ("127.0.0.1", 8080),
        "client": ("127.0.0.1", 12345),
        "headers": [
            (key.encode(), value.encode())
            for key, value in {**headers, "host": "127.0.0.1:8080"}.items()
        ],
    }
    app = server.create_serve_app()
    async with app.router.lifespan_context(app):
        task = asyncio.create_task(app(scope, incoming.get, send))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            incoming.put_nowait({"type": "http.disconnect"})
            await asyncio.wait_for(task, timeout=5)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert cancelled.is_set()


async def test_http_server_closes_vendor_client_on_its_event_loop(monkeypatch):
    import uvicorn

    monkeypatch.setenv("VLEX_API_KEY", "test-subscription-key")
    http_clients = []

    class StubServer:
        def __init__(self, config):
            assert config.host == "127.0.0.1"
            assert config.port == 8080
            assert config.access_log is False

        async def serve(self):
            await server.search_authorities("fair use")
            http_clients.append(server._get_client()._client._http)
            assert not http_clients[0].is_closed

    monkeypatch.setattr(uvicorn, "Server", StubServer)
    with respx.mock(assert_all_called=True) as router:
        router.get("https://api.icbg.io/research/v1/search").respond(200, json={"results": []})
        await server._serve_streamable_http()
    assert http_clients[0].is_closed
    assert server._client is None
