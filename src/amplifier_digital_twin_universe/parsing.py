"""Pulling structured documents out of model replies.

Deterministic. A reply that carries no recoverable document is a failed attempt,
never a prompt to guess at the prose around it.
"""

from __future__ import annotations

import json
import re
from typing import Any

_YAML_FENCE_RE = re.compile(r"```(?:yaml|yml)?\s*\n(.*?)```", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def extract_yaml(reply: str) -> str | None:
    """The YAML document in a reply, or None when there is no fenced block."""
    matches = _YAML_FENCE_RE.findall(reply)
    if not matches:
        return None
    # The document is the last fenced block: a model that narrates before
    # answering puts its examples first and its answer last.
    return matches[-1].strip() + "\n"


def extract_json_object(reply: str) -> dict[str, Any] | None:
    """The JSON object in a reply, or None when nothing parses as one.

    Tries fenced blocks last-first, then the widest brace-delimited span, so a
    reply that narrates around its answer still yields the answer.
    """
    for candidate in reversed(_JSON_FENCE_RE.findall(reply)):
        parsed = _load_object(candidate)
        if parsed is not None:
            return parsed

    start = reply.find("{")
    end = reply.rfind("}")
    if start != -1 and end > start:
        return _load_object(reply[start : end + 1])
    return None


def _load_object(text: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(text)
    except ValueError:
        return None
    return loaded if isinstance(loaded, dict) else None
