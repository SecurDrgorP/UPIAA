"""
JSON utilities for workflow ingestion.

The editor and AI builder sometimes produce JSON-like workflow exports with
JavaScript-style comments. Standard json.loads rejects those files, so backend
ingestion goes through this small tolerant preprocessor first.
"""

from __future__ import annotations

import json
from typing import Any


def strip_json_comments(raw: str) -> str:
    """Remove // and /* */ comments while preserving quoted string content."""
    out: list[str] = []
    i = 0
    in_string = False
    quote = ""
    escaped = False

    while i < len(raw):
        ch = raw[i]
        nxt = raw[i + 1] if i + 1 < len(raw) else ""

        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                in_string = False
                quote = ""
            i += 1
            continue

        if ch in ("'", '"'):
            in_string = True
            quote = ch
            out.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            i += 2
            while i < len(raw) and raw[i] not in "\r\n":
                i += 1
            continue

        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(raw) and not (raw[i] == "*" and raw[i + 1] == "/"):
                i += 1
            i = min(i + 2, len(raw))
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def loads_tolerant(raw: str | bytes) -> Any:
    """Parse JSON after removing JS-style comments."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    return json.loads(strip_json_comments(text))


def normalize_workflow_payload(payload: Any) -> Any:
    """
    Normalize workflow payloads at ingestion time.

    Currently a pass-through — reserved for future structural migrations.
    """
    return payload

