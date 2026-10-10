"""One mocked MCP round trip for every exposed vLex tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import respx
from mcp.client import Client

from vlex_mcp.client import VLexClient
from vlex_mcp.server import mcp


@dataclass(frozen=True)
class ToolCase:
    name: str
    method: str
    url: str
    arguments: dict[str, Any]
    response_json: dict[str, Any] | None = None
    response_text: str | None = None
    content_type: str = "application/json"


TOOL_CASES = [
    ToolCase(
        "search_authorities",
        "GET",
        "https://api.icbg.io/research/v1/search",
        {"query": "fair use", "jurisdiction": "us", "limit": 1},
        {"results-count": 1, "results": [{"title": "Example v. Case"}], "error": {"has": False}},
    ),
    ToolCase(
        "retrieve_legal_document",
        "GET",
        "https://api.icbg.io/research/v1/document/123",
        {"document_id": 123},
        response_text="<article>Legal text</article>",
        content_type="text/html",
    ),
    ToolCase(
        "ask_research_question",
        "POST",
        "https://api.icbg.io/vincent/v1/research_assignment",
        {
            "input": "What is the standard?",
            "jurisdiction": "us",
            "locale": "en-US",
            "research_skill": "research",
            "confirm": True,
        },
        {"id": "ra-1"},
    ),
    ToolCase(
        "get_research_answer",
        "GET",
        "https://api.icbg.io/vincent/v1/research_assignment/ra-1",
        {"assignment_id": "ra-1"},
        {"id": "ra-1", "status": "complete", "response": "The standard is ...", "error": None},
    ),
    ToolCase(
        "list_research_jurisdictions",
        "GET",
        "https://api.icbg.io/research/v1/jurisdictions",
        {"locale": "en", "limit": 1},
        {"jurisdictions": [{"id": "us", "name": "United States"}]},
    ),
    ToolCase(
        "list_vincent_workflows",
        "GET",
        "https://api.icbg.io/api/v2/workflows",
        {"limit": 10},
        {"data": [{"name": "legal-research", "title": "Legal research"}]},
    ),
    ToolCase(
        "prepare_document_upload",
        "POST",
        "https://api.icbg.io/api/v2/files",
        {
            "file_name": "brief.pdf",
            "size": 1234,
            "file_type": "application/pdf",
            "confirm": True,
        },
        {"id": "file-1", "status": "pending", "upload": {"url": "https://s3.example.test"}},
    ),
    ToolCase(
        "get_document_upload_status",
        "GET",
        "https://api.icbg.io/api/v2/files/file-1",
        {"file_id": "file-1"},
        {"id": "file-1", "status": "ready"},
    ),
    ToolCase(
        "analyze_with_vincent",
        "POST",
        "https://api.icbg.io/api/v2/conversations",
        {"workflow": "legal-research", "question": "Analyze this", "confirm": True},
        {"id": "conversation-1", "status": "running"},
    ),
    ToolCase(
        "get_conversation_messages",
        "GET",
        "https://api.icbg.io/api/v2/conversations/conversation-1/messages",
        {"conversation_id": "conversation-1", "limit": 20},
        {
            "data": [{"id": "message-1", "parts": []}],
            "meta": {"page": 1, "per_page": 20, "total": 1, "total_pages": 1, "running": False},
        },
    ),
    ToolCase(
        "continue_conversation",
        "POST",
        "https://api.icbg.io/api/v2/conversations/conversation-1/messages",
        {"conversation_id": "conversation-1", "input": "Please compare New York", "confirm": True},
        {"id": "message-2", "type": "user"},
    ),
    ToolCase(
        "resolve_conversation_action",
        "POST",
        "https://api.icbg.io/api/v2/conversations/conversation-1/actions",
        {
            "conversation_id": "conversation-1",
            "kind": "approval",
            "interruption_id": "interrupt-1",
            "decision": "approve",
            "confirm": True,
        },
        {"status": "accepted"},
    ),
    ToolCase(
        "start_authority_match",
        "POST",
        "https://api.icbg.io/api/v2/authority-match",
        {"file_id": "file-1", "confirm": True},
        {"job_id": "job-1", "status": "queued", "poll_url": "/api/v2/authority-match/job-1"},
    ),
    ToolCase(
        "get_authority_match_result",
        "GET",
        "https://api.icbg.io/api/v2/authority-match/job-1",
        {"job_id": "job-1"},
        {"job_id": "job-1", "status": "complete", "authorities": []},
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", TOOL_CASES, ids=lambda case: case.name)
async def test_tool_makes_mocked_round_trip(
    case: ToolCase,
    mocked_vlex_client: VLexClient,
) -> None:
    del mocked_vlex_client
    response = (
        httpx.Response(200, json=case.response_json)
        if case.response_json is not None
        else httpx.Response(
            200,
            text=case.response_text or "",
            headers={"content-type": case.content_type},
        )
    )

    with respx.mock(assert_all_called=True) as router:
        route = router.request(case.method, case.url).mock(return_value=response)
        async with Client(mcp, raise_exceptions=True) as client:
            result = await client.call_tool(case.name, case.arguments)

    assert route.call_count == 1
    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert route.calls.last.request.headers["Ocp-Apim-Subscription-Key"] == "test-subscription-key"
    if case.name == "list_research_jurisdictions":
        assert route.calls.last.request.headers["locale"] == "en"
        assert "locale" not in route.calls.last.request.url.params


WRITE_TOOLS = [
    (
        "ask_research_question",
        {
            "input": "Question",
            "jurisdiction": "us",
            "locale": "en-US",
            "research_skill": "research",
        },
    ),
    (
        "prepare_document_upload",
        {"file_name": "brief.pdf", "size": 1, "file_type": "application/pdf"},
    ),
    ("analyze_with_vincent", {"workflow": "legal-research"}),
    (
        "continue_conversation",
        {"conversation_id": "conversation-1", "input": "Continue"},
    ),
    (
        "resolve_conversation_action",
        {"conversation_id": "conversation-1", "kind": "approval"},
    ),
    ("start_authority_match", {"file_id": "file-1"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool_name", "arguments"), WRITE_TOOLS)
async def test_write_tools_require_confirmation(tool_name: str, arguments: dict[str, Any]) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(tool_name, arguments)

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text


@pytest.mark.asyncio
async def test_tool_redacts_vendor_validation_message(
    mocked_vlex_client: VLexClient,
) -> None:
    del mocked_vlex_client
    vendor_error = [
        {"loc": ["body", "file_type"], "msg": "unsupported MIME type", "type": "value_error"}
    ]

    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.icbg.io/api/v2/files").mock(
            return_value=httpx.Response(422, json=vendor_error)
        )
        async with Client(mcp) as client:
            result = await client.call_tool(
                "prepare_document_upload",
                {
                    "file_name": "brief.unknown",
                    "size": 10,
                    "file_type": "application/x-unknown",
                    "confirm": True,
                },
            )

    assert result.is_error is True
    assert "unsupported MIME type" not in result.content[0].text
    assert "Check inputs" in result.content[0].text
