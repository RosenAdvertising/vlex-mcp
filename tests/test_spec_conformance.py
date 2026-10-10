"""MCP protocol and tool-catalog conformance tests."""

from __future__ import annotations

import re

import pytest
from mcp.client import Client

from vlex_mcp.server import mcp

PROTOCOL_REVISION = "2026-07-28"


@pytest.mark.asyncio
async def test_server_initialization_declares_2026_07_28_protocol_revision() -> None:
    async with Client(mcp, mode="auto") as client:
        discovery = client.session.discover_result

        assert client.protocol_version == PROTOCOL_REVISION
        assert discovery is not None
        assert discovery.supported_versions == [PROTOCOL_REVISION]


@pytest.mark.asyncio
async def test_tool_catalog_has_descriptions_and_structured_outputs() -> None:
    async with Client(mcp) as client:
        tools = (await client.list_tools()).tools

    assert len(tools) == 14
    for tool in tools:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", tool.name)
        assert tool.description and "\n" not in tool.description
        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        for parameter in tool.input_schema.get("properties", {}).values():
            assert parameter.get("description")


@pytest.mark.asyncio
async def test_list_tools_expose_limit_and_write_tools_expose_confirmation() -> None:
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    for name in (
        "search_authorities",
        "list_research_jurisdictions",
        "list_vincent_workflows",
        "get_conversation_messages",
    ):
        assert "limit" in tools[name].input_schema["properties"]

    for name in (
        "ask_research_question",
        "prepare_document_upload",
        "analyze_with_vincent",
        "continue_conversation",
        "resolve_conversation_action",
        "start_authority_match",
    ):
        assert "confirm" in tools[name].input_schema["properties"]
