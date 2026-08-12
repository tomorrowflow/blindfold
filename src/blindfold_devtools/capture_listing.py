"""Captures listing (ADR-0047 §6 selection, issue #257).

``list_captures`` is the data behind ``blindfold captures``: a printed table
over the capture directory -- id, time, endpoint, hop count, detected count,
outcome, and a truncated excerpt of the first user hop. A footer-less capture
(no completion marker) is ``in-flight``, per the capture schema's own
completion-marker convention (:mod:`blindfold_devtools.capture`); a
size-capped one is ``truncated``. Sorted by filename, which doubles as the
chronological sort key (:func:`blindfold_devtools.capture_directory.generate_capture_id`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .capture import STATUS_IN_FLIGHT, STATUS_TRUNCATED, FooterRecord, HeaderRecord, read_capture
from .capture_directory import CAPTURE_SUFFIX

_EXCERPT_MAX_LEN = 80


def _first_user_hop_excerpt(inbound_payload: dict) -> str:
    for message in inbound_payload.get("messages", []) or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            text = ""
        text = text.strip()
        if len(text) > _EXCERPT_MAX_LEN:
            text = text[:_EXCERPT_MAX_LEN].rstrip() + "…"
        return text
    return ""


def _hop_count(inbound_payload: dict) -> int:
    count = len(inbound_payload.get("messages", []) or [])
    if inbound_payload.get("system") is not None:
        count += 1
    return count


@dataclass(frozen=True)
class CaptureSummary:
    """One row of ``blindfold captures``' table."""

    id: str
    ts: str | None
    endpoint: str | None
    hop_count: int | None
    detected_count: int | None
    outcome: str
    excerpt: str


def _summarize(capture_id: str, capture) -> CaptureSummary:
    header = next((r for r in capture.records if isinstance(r, HeaderRecord)), None)
    footer = next((r for r in capture.records if isinstance(r, FooterRecord)), None)

    if footer is not None:
        outcome = footer.outcome
    elif capture.status == STATUS_TRUNCATED:
        outcome = STATUS_TRUNCATED
    else:
        outcome = STATUS_IN_FLIGHT

    return CaptureSummary(
        id=capture_id,
        ts=header.ts if header is not None else None,
        endpoint=header.endpoint if header is not None else None,
        hop_count=_hop_count(header.inbound_payload) if header is not None else None,
        detected_count=len(footer.injected) if footer is not None else None,
        outcome=outcome,
        excerpt=_first_user_hop_excerpt(header.inbound_payload) if header is not None else "",
    )


def list_captures(directory: Path) -> list[CaptureSummary]:
    """List every capture in ``directory``, sorted chronologically (filename order).

    Never errors on an incomplete capture -- a footer-less one lists as
    ``in-flight``, mirroring :func:`blindfold_devtools.capture.read_capture`'s
    own tolerance of a mid-write file.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return [
        _summarize(path.stem, read_capture(path))
        for path in sorted(directory.glob(f"*{CAPTURE_SUFFIX}"))
    ]
