"""MCP server exposing vLex tools over stdio or HTTP."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette

from vlex_mcp import __version__
from vlex_mcp.client import VLexAPIError, VLexClient
from vlex_mcp.paths import path_id
from vlex_mcp.runtime import LazyClient
from vlex_mcp.runtime import SafeMCPServer as MCPServer

JsonScalar = str | int | float | bool | None
Locale = Literal["en", "es", "fr", "it", "ca", "ptbr"]
STREAMABLE_HTTP_TRANSPORT = "streamable-http"

_client: VLexClient | LazyClient | None = None


def set_client(client: VLexClient | LazyClient | None) -> None:
    """Set the process-wide API client; tests use this to install a mocked client."""
    global _client
    _client = client


def _get_client() -> VLexClient | LazyClient:
    global _client
    if _client is None:
        _client = LazyClient()
    return _client


def _without_none(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _confirm(confirm: bool, action: str) -> None:
    if not confirm:
        raise ToolError(f"Confirmation required: set confirm=true to {action}.")


def _require_https_callback(callback: str | None) -> None:
    if callback is None:
        return
    parsed = urlsplit(callback)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("callback must be a complete HTTPS URL")


def _require_flat_metadata(metadata: Mapping[str, JsonScalar] | None) -> None:
    if metadata is None:
        return
    for key, value in metadata.items():
        if not isinstance(key, str) or not key:
            raise ToolError("metadata keys must be non-empty strings")
        if isinstance(value, dict | list | tuple | set):
            raise ToolError("metadata values must be scalar JSON values")


def _limit_list_payload(
    payload: Any, limit: int, candidate_keys: tuple[str, ...]
) -> dict[str, Any]:
    if isinstance(payload, list):
        return {
            "data": payload[:limit],
            "meta": {"returned": min(len(payload), limit), "limit": limit},
        }
    if isinstance(payload, dict):
        result = dict(payload)
        for key in candidate_keys:
            values = result.get(key)
            if isinstance(values, list):
                result[key] = values[:limit]
                result["mcp_meta"] = {"returned": min(len(values), limit), "limit": limit}
                break
        return result
    return {"data": payload}


mcp = MCPServer(
    name="vlex",
    title="vLex Legal Research",
    description="Search vLex and run enabled Vincent AI legal workflows.",
    instructions=(
        "Use jurisdiction identifiers returned by list_research_jurisdictions. "
        "All mutating or job-starting tools require confirm=true."
    ),
    version=__version__,
)


@mcp.tool(structured_output=True)
async def search_authorities(
    query: Annotated[
        str,
        Field(min_length=1, description="Words or a legal question to search for."),
    ],
    jurisdiction: Annotated[
        str | None,
        Field(description="A jurisdiction identifier returned by list_research_jurisdictions."),
    ] = None,
    content_type: Annotated[
        str | None,
        Field(description="A vLex content-type filter, when one is known."),
    ] = None,
    sort: Annotated[
        str | None,
        Field(description="The vLex sort option to apply, when one is known."),
    ] = None,
    subscription_only: Annotated[
        bool | None,
        Field(description="Whether to show only material included in the subscription."),
    ] = None,
    limit: Annotated[
        int,
        Field(ge=1, le=10, description="Maximum results to return; vLex documents at most 10."),
    ] = 10,
) -> dict[str, Any]:
    """Search legal authorities and secondary materials in vLex."""
    params = _without_none(
        {
            "query": query,
            "jurisdiction": jurisdiction,
            "content_type": content_type,
            "sort": sort,
            "subscription_only": subscription_only,
            "page": 1,
            "per_page": 10,
        }
    )
    payload = await _get_client().request_json("research", "GET", "/search", params=params)
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("has"):
            raise VLexAPIError(
                str(error.get("message") or "vLex reported a search error."), status_code=200
            )
    return _limit_list_payload(payload, limit, ("results",))


@mcp.tool(structured_output=True)
async def retrieve_legal_document(
    document_id: Annotated[
        int | float,
        Field(description="The numeric vLex document identifier."),
    ],
) -> dict[str, Any]:
    """Retrieve the full HTML for a selected vLex document."""
    response = await _get_client().request(
        "research",
        "GET",
        f"/document/{path_id(document_id, 'document_id', numeric=True)}",
    )
    return {
        "id": document_id,
        "content_type": response.headers.get("content-type", "text/html").split(";", 1)[0],
        "content": response.text,
    }


@mcp.tool(structured_output=True)
async def ask_research_question(
    input: Annotated[
        str, Field(min_length=1, description="The legal research question to answer.")
    ],
    jurisdiction: Annotated[
        str, Field(min_length=1, description="The primary jurisdiction identifier.")
    ],
    locale: Annotated[str, Field(min_length=1, description="The requested response locale.")],
    research_skill: Annotated[
        str,
        Field(min_length=1, description="The Vincent research skill to use."),
    ],
    related_jurisdiction: Annotated[
        str | None,
        Field(description="A related jurisdiction identifier for comparative research."),
    ] = None,
    callback: Annotated[
        str | None,
        Field(description="An optional HTTPS URL for Vincent to call when the answer is ready."),
    ] = None,
    confirm: Annotated[
        bool,
        Field(description="Set true to confirm creation of this asynchronous assignment."),
    ] = False,
) -> dict[str, Any]:
    """Start a Vincent assignment to answer a legal research question."""
    _confirm(confirm, "create the Vincent research assignment")
    _require_https_callback(callback)
    body = _without_none(
        {
            "input": input,
            "jurisdiction": jurisdiction,
            "locale": locale,
            "research-skill": research_skill,
            "relatedJurisdiction": related_jurisdiction,
            "callback": callback,
        }
    )
    return await _get_client().request_structured(
        "vincent",
        "POST",
        "/research_assignment",
        json_body=body,
    )


@mcp.tool(structured_output=True)
async def get_research_answer(
    assignment_id: Annotated[
        str,
        Field(min_length=1, description="The Vincent research-assignment identifier."),
    ],
) -> dict[str, Any]:
    """Check a Vincent research assignment and return its answer when available."""
    return await _get_client().request_structured(
        "vincent",
        "GET",
        f"/research_assignment/{path_id(assignment_id, 'assignment_id')}",
    )


@mcp.tool(structured_output=True)
async def list_research_jurisdictions(
    locale: Annotated[
        Locale,
        Field(description="Language used for jurisdiction names."),
    ] = "en",
    limit: Annotated[
        int,
        Field(ge=1, le=1000, description="Maximum jurisdiction entries to return."),
    ] = 250,
) -> dict[str, Any]:
    """List jurisdiction identifiers accepted by vLex legal search."""
    payload = await _get_client().request_json(
        "research",
        "GET",
        "/jurisdictions",
        headers={"locale": locale},
    )
    return _limit_list_payload(payload, limit, ("jurisdictions", "data", "results"))


@mcp.tool(structured_output=True)
async def list_vincent_workflows(
    limit: Annotated[
        int,
        Field(ge=1, le=1000, description="Maximum enabled workflows to return."),
    ] = 100,
) -> dict[str, Any]:
    """List the Vincent workflows enabled for this vLex account."""
    payload = await _get_client().request_json("public", "GET", "/api/v2/workflows")
    return _limit_list_payload(payload, limit, ("data", "workflows", "results"))


@mcp.tool(structured_output=True)
async def prepare_document_upload(
    file_name: Annotated[str, Field(min_length=1, description="Name of the file to upload.")],
    size: Annotated[
        int,
        Field(
            ge=1,
            le=1_073_741_824,
            description="File size in bytes, up to the documented 1 GB record limit.",
        ),
    ],
    file_type: Annotated[str, Field(min_length=1, description="The file's accepted MIME type.")],
    metadata: Annotated[
        dict[str, JsonScalar] | None,
        Field(description="Optional flat key-value metadata for the file."),
    ] = None,
    confirm: Annotated[
        bool,
        Field(description="Set true to confirm creation of the file record and upload target."),
    ] = False,
) -> dict[str, Any]:
    """Create a vLex file record and receive a secure document-upload target."""
    _confirm(confirm, "create the vLex file record")
    _require_flat_metadata(metadata)
    body = _without_none(
        {"file_name": file_name, "size": size, "file_type": file_type, "metadata": metadata}
    )
    return await _get_client().request_structured(
        "public",
        "POST",
        "/api/v2/files",
        json_body=body,
    )


@mcp.tool(structured_output=True)
async def get_document_upload_status(
    file_id: Annotated[str, Field(min_length=1, description="The vLex file-record identifier.")],
) -> dict[str, Any]:
    """Check whether a document upload is ready or has failed."""
    return await _get_client().request_structured(
        "public",
        "GET",
        f"/api/v2/files/{path_id(file_id, 'file_id')}",
    )


@mcp.tool(structured_output=True)
async def analyze_with_vincent(
    workflow: Annotated[
        str,
        Field(min_length=1, description="An enabled workflow name from list_vincent_workflows."),
    ],
    question: Annotated[
        str | None,
        Field(description="The question or instruction for Vincent."),
    ] = None,
    locale: Annotated[str | None, Field(description="The requested language or locale.")] = None,
    country: Annotated[
        str | None, Field(description="The primary country for the analysis.")
    ] = None,
    sub_jurisdiction: Annotated[
        str | None,
        Field(description="A state, province, or other sub-jurisdiction."),
    ] = None,
    related_jurisdiction: Annotated[
        str | None,
        Field(description="A related jurisdiction for comparison."),
    ] = None,
    collection_id: Annotated[
        str | None,
        Field(description="An existing Vincent collection identifier."),
    ] = None,
    matter_id: Annotated[str | None, Field(description="An optional matter identifier.")] = None,
    file_ids: Annotated[
        list[str] | None,
        Field(description="Identifiers of previously uploaded files to analyze."),
    ] = None,
    initial_approval_keys: Annotated[
        list[str] | None,
        Field(description="Workflow approval keys that are already approved."),
    ] = None,
    unattended: Annotated[
        bool | None,
        Field(description="Whether the workflow should run without interactive pauses."),
    ] = None,
    stream: Annotated[
        bool,
        Field(
            description="Whether to collect the endpoint's SSE events instead of one JSON response."
        ),
    ] = False,
    confirm: Annotated[
        bool,
        Field(description="Set true to confirm creation of this Vincent conversation."),
    ] = False,
) -> dict[str, Any]:
    """Start an enabled Vincent workflow over a question or uploaded documents."""
    _confirm(confirm, "start the Vincent workflow")
    body = _without_none(
        {
            "workflow": workflow,
            "question": question,
            "locale": locale,
            "country": country,
            "sub_jurisdiction": sub_jurisdiction,
            "related_jurisdiction": related_jurisdiction,
            "collection_id": collection_id,
            "matter_id": matter_id,
            "file_ids": file_ids,
            "initial_approval_keys": initial_approval_keys,
            "unattended": unattended,
        }
    )
    return await _get_client().request_structured(
        "public",
        "POST",
        "/api/v2/conversations",
        params={"stream": stream},
        json_body=body,
    )


@mcp.tool(structured_output=True)
async def get_conversation_messages(
    conversation_id: Annotated[
        str,
        Field(min_length=1, description="The Vincent conversation identifier."),
    ],
    page: Annotated[int, Field(ge=1, description="One-based page at which to start.")] = 1,
    limit: Annotated[
        int,
        Field(ge=1, le=1000, description="Maximum messages to return across pages."),
    ] = 50,
    order: Annotated[
        Literal["asc", "desc"],
        Field(description="Whether to return messages oldest-first or newest-first."),
    ] = "asc",
    types: Annotated[
        list[str] | None,
        Field(description="Optional vLex message types to include."),
    ] = None,
) -> dict[str, Any]:
    """Get a Vincent conversation transcript and see whether it is still running."""
    return await _get_client().paginate(
        "public",
        f"/api/v2/conversations/{path_id(conversation_id, 'conversation_id')}/messages",
        limit=limit,
        page=page,
        max_per_page=100,
        params=_without_none({"order": order, "types": types}),
    )


@mcp.tool(structured_output=True)
async def continue_conversation(
    conversation_id: Annotated[
        str,
        Field(min_length=1, description="The Vincent conversation identifier."),
    ],
    input: Annotated[
        str,
        Field(min_length=1, description="A follow-up question or clarification answer."),
    ],
    stream: Annotated[
        bool,
        Field(
            description="Whether to collect the endpoint's SSE events instead of one JSON response."
        ),
    ] = False,
    confirm: Annotated[
        bool,
        Field(description="Set true to confirm sending this message to Vincent."),
    ] = False,
) -> dict[str, Any]:
    """Send a follow-up question or clarification answer to Vincent."""
    _confirm(confirm, "send the Vincent conversation message")
    return await _get_client().request_structured(
        "public",
        "POST",
        f"/api/v2/conversations/{path_id(conversation_id, 'conversation_id')}/messages",
        params={"stream": stream},
        json_body={"input": input},
    )


@mcp.tool(structured_output=True)
async def resolve_conversation_action(
    conversation_id: Annotated[
        str,
        Field(min_length=1, description="The Vincent conversation identifier."),
    ],
    kind: Annotated[
        str,
        Field(min_length=1, description="The action kind requested by the Vincent message."),
    ],
    interruption_id: Annotated[
        str | None,
        Field(description="The interruption identifier for an approval action."),
    ] = None,
    decision: Annotated[
        str | None,
        Field(description="The requested approval or rejection decision."),
    ] = None,
    scope: Annotated[str | None, Field(description="The scope of an approval decision.")] = None,
    rejection_message: Annotated[
        str | None,
        Field(description="An explanation supplied when rejecting an action."),
    ] = None,
    result: Annotated[
        Any | None,
        Field(description="A JSON-compatible result returned by an external tool."),
    ] = None,
    message_id: Annotated[
        str | None,
        Field(description="The message identifier containing proposed tasks."),
    ] = None,
    selected_task_indices: Annotated[
        list[int] | None,
        Field(description="Zero-based indices of the proposed tasks to select."),
    ] = None,
    confirm: Annotated[
        bool,
        Field(description="Set true to confirm submitting this action decision."),
    ] = False,
) -> dict[str, Any]:
    """Approve, reject, or answer a pending Vincent workflow action."""
    _confirm(confirm, "submit the Vincent action decision")
    body = _without_none(
        {
            "kind": kind,
            "interruption_id": interruption_id,
            "decision": decision,
            "scope": scope,
            "rejection_message": rejection_message,
            "result": result,
            "message_id": message_id,
            "selected_task_indices": selected_task_indices,
        }
    )
    return await _get_client().request_structured(
        "public",
        "POST",
        f"/api/v2/conversations/{path_id(conversation_id, 'conversation_id')}/actions",
        json_body=body,
    )


@mcp.tool(structured_output=True)
async def start_authority_match(
    file_id: Annotated[
        str,
        Field(min_length=1, description="A previously uploaded vLex file identifier."),
    ],
    confirm: Annotated[
        bool,
        Field(description="Set true to confirm starting this authority-match job."),
    ] = False,
) -> dict[str, Any]:
    """Start checking and verifying the citations in an uploaded document."""
    _confirm(confirm, "start the authority-match job")
    return await _get_client().request_structured(
        "public",
        "POST",
        "/api/v2/authority-match",
        json_body={"file_id": file_id},
    )


@mcp.tool(structured_output=True)
async def get_authority_match_result(
    job_id: Annotated[str, Field(min_length=1, description="The authority-match job identifier.")],
) -> dict[str, Any]:
    """Check an authority-match job and return verified citations when ready."""
    return await _get_client().request_structured(
        "public",
        "GET",
        f"/api/v2/authority-match/{path_id(job_id, 'job_id')}",
    )


def _requested_transport() -> str:
    return os.environ.get("VLEX_MCP_TRANSPORT", "stdio").strip().lower() or "stdio"


def _host() -> str:
    return os.environ.get("VLEX_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"


def _port() -> int:
    raw = os.environ.get("PORT", "8080").strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"PORT must be an integer, got {raw!r}") from exc


def _transport_security() -> TransportSecuritySettings | None:
    if _host() in ("127.0.0.1", "localhost", "::1"):
        return None
    allowed_hosts = [
        host.strip()
        for host in os.environ.get("VLEX_MCP_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ]
    if not allowed_hosts:
        raise SystemExit("VLEX_MCP_ALLOWED_HOSTS is required when VLEX_MCP_HOST is not loopback.")
    allowed_origins = [
        origin.strip()
        for origin in os.environ.get("VLEX_MCP_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def create_serve_app() -> Starlette:
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        host=_host(),
        stateless_http=True,
        transport_security=_transport_security(),
    )


async def _serve_streamable_http() -> None:
    import uvicorn

    config = uvicorn.Config(
        create_serve_app(),
        host=_host(),
        port=_port(),
        log_level=mcp.settings.log_level.lower(),
        access_log=False,
    )
    client = LazyClient()
    set_client(client)
    try:
        await uvicorn.Server(config).serve()
    finally:
        set_client(None)
        await client.aclose()


def main() -> None:
    """Select transport; configure the vendor client only when a tool needs it."""
    transport = _requested_transport()
    if transport == STREAMABLE_HTTP_TRANSPORT:
        asyncio.run(_serve_streamable_http())
        return
    if transport != "stdio":
        raise SystemExit(
            "Unsupported VLEX_MCP_TRANSPORT "
            f"{transport!r}; expected 'stdio' or '{STREAMABLE_HTTP_TRANSPORT}'."
        )
    client = LazyClient()
    set_client(client)
    try:
        mcp.run(transport="stdio")
    finally:
        set_client(None)
        asyncio.run(client.aclose())


if __name__ == "__main__":
    main()
