"""``blindfold explain`` render (ADR-0047 §3/§6, issue #257).

Selection resolves an id (or ``--last``) against the capture directory;
rendering leads with a mismatch banner (only when the offline leak check or
the comparison's severity ladder reports something worth reading), then
hop-by-hop annotated text, then a summary table.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from blindfold.detection import Entity
from blindfold.surrogates import SurrogateMapping

from .capture import SECTION_RECONSTRUCTED, DetectionRecord, FooterRecord, HeaderRecord
from .capture_comparison import SEVERITY_DEFECT, compare
from .capture_directory import CAPTURE_SUFFIX
from .leak_check import leak_check


class CaptureNotFoundError(RuntimeError):
    """Raised when ``explain`` cannot resolve an id (or ``--last``) to a capture file."""


def resolve_capture(directory: Path, capture_id: str | None, *, last: bool = False) -> Path:
    """Resolve ``capture_id`` (or, with ``last=True``, the most recent capture by
    filename sort key) to its path under ``directory``. Filename order is
    chronological order (:func:`blindfold_devtools.capture_directory.generate_capture_id`).
    """
    directory = Path(directory)
    if last:
        candidates = sorted(directory.glob(f"*{CAPTURE_SUFFIX}")) if directory.is_dir() else []
        if not candidates:
            raise CaptureNotFoundError(f"no captures found in {directory}")
        return candidates[-1]

    if capture_id is None:
        raise CaptureNotFoundError("no capture id given, and --last was not requested")
    path = directory / f"{capture_id}{CAPTURE_SUFFIX}"
    if not path.exists():
        raise CaptureNotFoundError(f"no capture {capture_id!r} found in {directory}")
    return path


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _hop_texts(inbound_payload: dict) -> list[tuple[str, str]]:
    """``[(hop_kind, text), ...]`` for each hop in the header's inbound payload --
    system first (if present), then one per message -- mirroring
    :func:`blindfold.engine.blindfold_payload`'s own hop enumeration."""
    hops = []
    system = inbound_payload.get("system")
    if system is not None:
        hops.append(("system", system if isinstance(system, str) else _content_text(system)))
    for message in inbound_payload.get("messages", []) or []:
        role = message.get("role") if isinstance(message, dict) else None
        text = _content_text(message.get("content")) if isinstance(message, dict) else ""
        hops.append((role or "user", text))
    return hops


def _annotate_known_strings(text: str, injected: dict[str, str]) -> str:
    """Mark every occurrence of a known real value with its surrogate, inline.

    Known strings only -- the exact real value and the exact surrogate already
    held in ``session.injected`` (the footer's pair table) -- never a heuristic
    alignment of two blobs, so every occurrence of the same real value is marked,
    not just the first.
    """
    for surrogate, real in injected.items():
        if real and real in text:
            text = text.replace(real, f"[{real} -> {surrogate}]")
    return text


def render_capture(
    records: Iterable, *, graph_entities: Iterable[Entity], mapping: SurrogateMapping
) -> str:
    """Render one Exchange capture: a mismatch banner first (only when the
    offline leak check or the comparison's severity ladder found a ``leak`` or
    a ``defect`` -- the top two of the severity ladder, ADR-0047 §9), then
    hop-by-hop annotated text with each replaced span marked inline and its
    surrogate shown, then a summary table. Never errors on an in-flight
    capture -- it states what is missing instead.
    """
    records = list(records)
    header = next((r for r in records if isinstance(r, HeaderRecord)), None)
    footer = next((r for r in records if isinstance(r, FooterRecord)), None)

    capture_id = header.capture_id if header is not None else "?"

    if footer is None:
        return (
            f"capture {capture_id}: in-flight -- no footer yet, so the injected "
            "pair table, offline leak check and comparison are not available."
        )

    leak_result = leak_check(records, mapping)
    comparison = compare(records, graph_entities=graph_entities)
    defects = [d for d in comparison.divergences if d.severity == SEVERITY_DEFECT]

    lines: list[str] = []
    if leak_result.findings:
        lines.append(f"MISMATCH -- {leak_result.summary()}")
    elif defects:
        refs = ", ".join(d.ref for d in defects)
        lines.append(f"MISMATCH -- comparison found a defect (ref: {refs})")

    lines.append(f"capture {capture_id}: {header.endpoint if header is not None else '?'}")

    if header is not None:
        for hop_index, (hop_kind, text) in enumerate(_hop_texts(header.inbound_payload)):
            annotated = _annotate_known_strings(text, footer.injected)
            lines.append(f"hop {hop_index} ({hop_kind}): {annotated}")
            for record in records:
                if not (
                    isinstance(record, DetectionRecord)
                    and record.section == SECTION_RECONSTRUCTED
                    and record.hop_index == hop_index
                    and record.offsets
                ):
                    continue
                for start, end in record.offsets:
                    lines.append(
                        f"  (reconstructed, pass_name={record.pass_name}, "
                        f"offset=({start}, {end})): {text[start:end]!r}"
                    )

    lines.append("summary:")
    lines.append(f"  outcome: {footer.outcome}")
    lines.append(f"  detected: {len(footer.injected)} value(s) injected")
    lines.append(f"  {leak_result.summary()}")
    if not comparison.comparable:
        lines.append(
            "  comparison: not run -- no reconstructed section (live-only capture)"
        )
    elif comparison.divergences:
        for divergence in comparison.divergences:
            lines.append(
                f"  divergence [{divergence.severity}]: {divergence.ref} -- {divergence.reason}"
            )
    else:
        lines.append("  comparison: no divergence")

    return "\n".join(lines)
