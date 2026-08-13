"""Per-hop text extraction from an inbound payload (ADR-0047 §3/§6).

Shared by ``capture_render`` (annotating an already-rendered hop) and ``replay``
(re-detecting offsets against the same hop text engine.py itself walked) --
one extraction, not two copies that could drift apart.

Structural, not dialect-branching: an Anthropic Messages payload's top-level
``system`` is hop 0 when present; a Chat Completions payload has no such key
(its system turn is already a ``role: "system"`` message in ``messages``), so
the same walk covers both shapes without naming either one, mirroring
:func:`blindfold.engine.blindfold_payload`'s own hop enumeration.
"""

from __future__ import annotations


def content_text(content) -> str:
    """Flatten a Messages/Chat-Completions ``content`` value to display text.

    A string is returned as-is; a list of content blocks joins every
    ``type: "text"`` block's text with a space. Anything else (a tool_use/
    tool_result block alone, ``None``) contributes no text -- the same scope
    :func:`capture_render.render_capture` has always rendered.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def hop_texts(inbound_payload: dict) -> list[tuple[str, str]]:
    """``[(hop_kind, text), ...]`` for each hop, in the order
    :func:`blindfold.engine.blindfold_payload` (or its Chat Completions sibling)
    enumerates ``session.hops`` -- system first (if present), then one per
    message."""
    hops = []
    system = inbound_payload.get("system")
    if system is not None:
        hops.append(("system", system if isinstance(system, str) else content_text(system)))
    for message in inbound_payload.get("messages", []) or []:
        role = message.get("role") if isinstance(message, dict) else None
        text = content_text(message.get("content")) if isinstance(message, dict) else ""
        hops.append((role or "user", text))
    return hops
