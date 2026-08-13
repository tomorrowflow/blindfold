"""Auto-detect a replay payload's dialect by shape (ADR-0047 §6, issue #269).

A bare replay input carries no client header naming a dialect, unlike a proxied
request (routed by its own path, ``/v1/messages`` vs ``/v1/chat/completions``).
Detection is structural: a top-level ``system`` key exists only in the
Anthropic Messages shape; a ``role: "system"`` message and a ``function``-
wrapped tool exist only in the OpenAI Chat Completions shape. A payload
carrying neither signal (a bare user turn, ambiguous between the two) defaults
to the Messages dialect -- the proxy's primary, oldest-supported shape.
"""

from __future__ import annotations

MESSAGES = "messages"
CHAT_COMPLETIONS = "chat_completions"


def detect_dialect(payload: dict) -> str:
    if "system" in payload:
        return MESSAGES
    for message in payload.get("messages", []) or []:
        if isinstance(message, dict) and message.get("role") == "system":
            return CHAT_COMPLETIONS
    for tool in payload.get("tools", []) or []:
        if isinstance(tool, dict) and "function" in tool:
            return CHAT_COMPLETIONS
    return MESSAGES
