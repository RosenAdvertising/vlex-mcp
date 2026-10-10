"""Validate resource identifiers before composing URL paths."""

import math
import re
from typing import Any
from urllib.parse import quote
from uuid import UUID

from mcp.server.mcpserver.exceptions import ToolError

_PLAIN_ID = re.compile(r"[A-Za-z0-9_-]+")


def path_id(value: str | int | float | UUID, parameter: str, *, numeric: bool = False) -> str:
    """Validate the identifier before encoding it as one URL segment."""
    # JSON represents whole-number IDs as either integers or floats. Preserve
    # integer precision and let the SDK enforce each tool's numeric constraints.
    if isinstance(value, float) and math.isfinite(value):
        if numeric:
            # vLex explicitly declares int | float, including fractional IDs.
            return quote(str(value), safe="")
        if value.is_integer():
            value = int(value)
    if (
        isinstance(value, bool)
        or not isinstance(value, str | int | UUID)
        or not _PLAIN_ID.fullmatch(str(value))
    ):
        raise ToolError(
            f"{parameter} must be a non-empty plain identifier using only ASCII letters, "
            "digits, underscores, or hyphens. Supply the record ID, not a URL or encoded path."
        )
    return quote(str(value), safe="")


_TOOL_PATH_IDS: dict[str, tuple[str, ...]] = {
    "retrieve_legal_document": ("document_id",),
    "get_research_answer": ("assignment_id",),
    "get_document_upload_status": ("file_id",),
    "get_conversation_messages": ("conversation_id",),
    "continue_conversation": ("conversation_id",),
    "resolve_conversation_action": ("conversation_id",),
    "get_authority_match_result": ("job_id",),
}


def validate_path_arguments(name: str, arguments: dict[str, Any]) -> None:
    """Keep actionable ID errors ahead of SDK coercion, without changing schemas."""
    for parameter in _TOOL_PATH_IDS.get(name, ()):
        if parameter not in arguments:
            continue  # The SDK reports missing required arguments.
        path_id(
            arguments[parameter],
            parameter,
            numeric=name == "retrieve_legal_document" and parameter == "document_id",
        )
