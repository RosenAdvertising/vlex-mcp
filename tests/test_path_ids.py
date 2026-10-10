"""Adversarial path tests through MCP and real HTTP clients; no sockets."""

import copy
from datetime import date

import httpx
import pytest
import pytest_asyncio
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from vlex_mcp import client as client_module
from vlex_mcp import config, server

BAD_IDS = [
    "",
    ".",
    "..",
    "a/../b",
    "%2e%2e",
    "%2E%2E",
    "a/b",
    "a\\b",
    "a?x=1",
    "a#b",
    "a b",
    "é",
    "a\n",
]
UUID_ID = "00000000-0000-4000-8000-000000000001"
PATH_PARAMETERS = {
    "retrieve_legal_document": ["document_id"],
    "get_research_answer": ["assignment_id"],
    "get_document_upload_status": ["file_id"],
    "get_conversation_messages": ["conversation_id"],
    "continue_conversation": ["conversation_id"],
    "resolve_conversation_action": ["conversation_id"],
    "get_authority_match_result": ["job_id"],
}


def sample(schema, root):
    if "$ref" in schema:
        return sample(root["$defs"][schema["$ref"].split("/")[-1]], root)
    if "anyOf" in schema:
        return sample(next(s for s in schema["anyOf"] if s.get("type") != "null"), root)
    if "enum" in schema:
        return schema["enum"][0]
    if schema.get("type") == "array":
        return [sample(schema["items"], root)]
    if schema.get("type") == "object":
        return {}
    if schema.get("type") in ("integer", "number"):
        return 123
    if schema.get("type") == "boolean":
        return True
    if schema.get("format") == "uuid":
        return UUID_ID
    if schema.get("format") == "date":
        return "2026-10-01"
    return "normal-id_123"


def arguments_for(tool):
    schema = tool.parameters
    args = {k: sample(schema["properties"][k], schema) for k in schema.get("required", [])}
    if "confirm" in schema["properties"]:
        args["confirm"] = True
    if "post_type" in schema["properties"]:
        args["post_type"] = "posts"
    return args


@pytest_asyncio.fixture
async def backend(monkeypatch):
    sent = []

    def respond(request):
        sent.append(request)
        # Only successful synthetic API data, after HTTPX URL preparation.
        payload = {"data": []}
        return httpx.Response(200, json=payload)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://example.invalid/"
    )
    real = client_module.VLexClient(
        config.Settings(
            "synthetic",
            research_base_url="https://example.invalid/research",
            vincent_base_url="https://example.invalid/vincent",
            public_base_url="https://example.invalid",
        ),
        http_client=http,
    )
    monkeypatch.setattr(server, "_client", real)
    app = server.mcp
    try:
        yield app, sent
    finally:
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", BAD_IDS)
@pytest.mark.parametrize(
    "tool_name,parameter", [(t, p) for t, ps in PATH_PARAMETERS.items() for p in ps]
)
async def test_invalid_path_id_never_reaches_transport(backend, tool_name, parameter, bad):
    app, sent = backend
    tool = app._tool_manager.get_tool(tool_name)
    args = arguments_for(tool)
    args[parameter] = [UUID_ID, bad] if parameter == "grid_ids" else bad
    async with Client(app) as client:
        result = await client.call_tool(tool_name, args)
    text = " ".join(getattr(c, "text", "") for c in result.content)
    assert result.is_error
    assert parameter in text
    assert "plain identifier" in text
    assert not sent, "Invalid IDs must be rejected before authentication or data requests"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", PATH_PARAMETERS)
async def test_normal_path_id_preserves_prepared_route(backend, tool_name):
    app, sent = backend
    tool = app._tool_manager.get_tool(tool_name)
    args = arguments_for(tool)
    async with Client(app) as client:
        result = await client.call_tool(tool_name, args)
    assert not result.is_error, result
    assert sent
    for parameter in PATH_PARAMETERS[tool_name]:
        value = args.get(parameter, 1)
        if isinstance(value, list):
            value = value[0]
        assert str(value) in sent[0].url.path.split("/")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", ".", "..", "a/../b", "%2e%2e", "%2E%2E"])
@pytest.mark.parametrize(
    "tool_name,parameter", [(t, p) for t, ps in PATH_PARAMETERS.items() for p in ps]
)
async def test_path_callsite_validates_without_sdk(backend, tool_name, parameter, bad):
    app, sent = backend
    tool = app._tool_manager.get_tool(tool_name)
    args = copy.deepcopy(arguments_for(tool))
    if "date" in args:
        args["date"] = date.fromisoformat(args["date"])
    args[parameter] = [UUID_ID, bad] if parameter == "grid_ids" else bad
    with pytest.raises(
        ToolError, match=parameter.removesuffix("s") if parameter == "grid_ids" else parameter
    ):
        await tool.fn(**args)
    assert not sent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name,parameter,route",
    [("retrieve_legal_document", "document_id", "/research/document/{id}")],
)
@pytest.mark.parametrize(
    "value,segment",
    [
        (123, "123"),
        ("123", "123"),
        ("00123", "123"),
        (123.0, "123.0"),
        (9007199254740993, "9007199254740993"),
        (123.5, "123.5"),
        (-123.5, "-123.5"),
        (1e-07, "1e-07"),
        (1e21, "1e+21"),
    ],
)
async def test_numeric_ids_through_mcp_preserve_route(
    backend, tool_name, parameter, route, value, segment
):
    app, sent = backend
    args = arguments_for(app._tool_manager.get_tool(tool_name))
    args[parameter] = value
    async with Client(app) as client:
        result = await client.call_tool(tool_name, args)
    assert not result.is_error, result
    assert len(sent) == 1
    assert sent[0].url.path == route.format(id=segment)


@pytest.mark.parametrize(
    "bad", [True, False, None, [], {}, float("inf"), float("-inf"), float("nan")]
)
def test_non_identifiers_rejected(bad):
    from vlex_mcp.paths import path_id

    with pytest.raises(ToolError, match="record_id"):
        path_id(bad, "record_id")
