"""Client-visible and source wording stays plain and matches the README."""

from __future__ import annotations

import re
from pathlib import Path

from vlex_mcp.server import mcp

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "vlex_mcp"
INTERNAL_WORDING = re.compile(r"method[- ]verified|verified vLex", re.IGNORECASE)


def test_package_source_has_no_internal_method_verified_wording() -> None:
    offenders = [
        f"{path.name}:{number}"
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if INTERNAL_WORDING.search(line)
    ]
    assert offenders == []


def test_server_text_sent_to_clients_is_plain() -> None:
    assert mcp.description == "Search vLex and run enabled Vincent AI legal workflows."
    assert mcp.instructions is not None
    assert "require confirm=true" in mcp.instructions
    for text in (mcp.description, mcp.instructions):
        assert not INTERNAL_WORDING.search(text)
