"""Replay: drive blindfold.engine's real pipeline over a request payload and
reconstruct the per-hop detection detail ``_HopContext``/``_finish_hop`` discard
(ADR-0047 §6, issue #269 -- #255 was closed with no shipped implementation).

Drives the real pipeline, not a reimplementation: :func:`blindfold.engine.
blindfold_payload` / :func:`blindfold_chat_completions_payload` -- the exact
functions ``blindfold.app``'s request handlers call -- produce the blindfolded
payload and the :class:`~blindfold.engine.ExchangeSession`. Every count/duration/
provider/surrogate field on a reconstructed :class:`~blindfold_devtools.capture.
DetectionRecord` comes straight from that call's own
``session.hops`` (:class:`~blindfold.engine.HopDetail`) -- never independently
re-derived. Only ``offsets``/``pass_name`` -- the detail ``_HopContext`` itself
discards -- are recovered, by calling :func:`blindfold.detection.detect_l2` /
:func:`~blindfold.detection.detect_pii` a **second time** on each hop's
*original* text: the exact pure primitives ``_blindfold_text`` calls internally
against that same original text (L2 runs first, before anything is rewritten),
so this reproduces their result exactly rather than reimplementing the
replacement algorithm itself.

``inbox=None`` always (ADR-0047 §6): no novel entity from a replay payload may
reach the real Review inbox or grow the entity graph through the learning loop.
**Finding, verified against the current engine.py (issue #269's own instruction
to check this claim before proceeding):** ``blindfold.engine._blindfold_text``'s
L3 branch guards on ``l3_detector is not None and inbox is not None`` -- BOTH,
not just the detector. With ``inbox=None`` this is always false, so L3 *never*
adjudicates during replay, regardless of whether an L3 detector is wired. The
ADR text's "mints are harmless: SurrogateMapping is in-memory with no store
handle" describes L1 PII mints, not L3 -- L3 does not skip minting a harmless
provisional surrogate here, it skips running at all. Reconciling that (letting
L3 mint a provisional surrogate through some inbox-less path) would mean
restructuring ``_blindfold_text``'s L3 block, a change to privacy-critical
request-path code out of scope for this devtools-only slice; see this issue's
handoff notes. ``l3_wired`` on the emitted :class:`DetectionRecord` therefore
records only whether an L3 detector was *configured* for this run (a caller-side
settings fact), not whether it ran -- it never does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from blindfold.detection import detect_l2, detect_pii
from blindfold.engine import (
    ExchangeSession,
    blindfold_chat_completions_payload,
    blindfold_payload,
    extract_declared_tools_chat_completions,
    extract_declared_tools_messages,
)
from blindfold.l3 import L3Detector
from blindfold.policy import DEFAULT_WORKSPACE
from blindfold.surrogates import SurrogateMapping

from .capture import SECTION_RECONSTRUCTED, DetectionRecord
from .dialect import CHAT_COMPLETIONS, MESSAGES, detect_dialect
from .hop_text import hop_texts

# The outcome stamped on a Footer this module writes for a capture it created
# itself (a bare-payload/--text replay, as opposed to appending reconstructed
# detail to an already-captured live exchange) -- distinct from the live
# capture's processing-trace-derived outcomes (ADR-0035's "passed"/"blocked"/
# "upstream_error"), since nothing was ever sent anywhere to pass, block, or
# error against.
REPLAY_OUTCOME = "replayed"


def wrap_text_payload(text: str) -> dict:
    """Wrap a bare text file's contents in a minimal single-hop Messages
    payload (ADR-0047 §6's ``--text FILE``)."""
    return {"messages": [{"role": "user", "content": text}]}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ReplayResult:
    endpoint: str  # "messages" | "chat_completions"
    payload: dict  # the blindfolded outbound payload this run produced
    session: ExchangeSession
    l3_wired: bool
    detections: tuple[DetectionRecord, ...]  # SECTION_RECONSTRUCTED, one per hop


def _reconstruct_hop(hop, text: str, mapping: SurrogateMapping, *, l3_wired: bool) -> DetectionRecord:
    """One hop's reconstructed detection: real HopDetail counts/durations/
    provider/surrogates (from the live call this replay just made) plus
    offsets/pass_name recovered by re-running the pure L2/L1 detection
    primitives against this hop's original text.
    """
    offsets: list[tuple[int, int]] = []
    pass_names: list[str] = []

    for span in detect_l2(text, mapping.entities()):
        offsets.append((span.start, span.end))
        pass_names.append(span.pass_name)

    # detect_pii() yields one PiiSpan per occurrence (not per distinct value),
    # so a value repeated in this hop yields duplicate PiiSpans -- track how
    # many of each value have already been located so a repeat resolves to its
    # own, later position rather than the same first occurrence every time.
    search_from: dict[str, int] = {}
    for pii_span in detect_pii(text):
        start = text.find(pii_span.value, search_from.get(pii_span.value, 0))
        if start == -1:
            continue
        search_from[pii_span.value] = start + 1
        offsets.append((start, start + len(pii_span.value)))
        pass_names.append(f"l1_pii:{pii_span.kind}")

    return DetectionRecord(
        section=SECTION_RECONSTRUCTED,
        ts=_now_iso(),
        hop_index=hop.hop_index,
        hop_kind=hop.hop_kind,
        l1_counts=dict(hop.l1_counts),
        l1_duration_ms=hop.l1_duration_ms,
        l2_count=hop.l2_count,
        l2_duration_ms=hop.l2_duration_ms,
        l3_confirmed=hop.l3_confirmed,
        l3_dismissed=hop.l3_dismissed,
        l3_suppressed=hop.l3_suppressed,
        l3_provider=hop.l3_provider,
        l3_duration_ms=hop.l3_duration_ms,
        surrogates=hop.surrogates,
        pass_name=",".join(sorted(set(pass_names))) if pass_names else None,
        offsets=tuple(offsets) if offsets else None,
        l3_wired=l3_wired,
    )


def replay(
    payload: dict,
    *,
    mapping: SurrogateMapping,
    l3_detector: L3Detector | None,
    workspace: str = DEFAULT_WORKSPACE,
) -> ReplayResult:
    """Drive the real pipeline over ``payload``: never sends anything upstream
    (no upstream client is even reachable from here), never writes to a review
    inbox (``inbox=None``, always). ``endpoint`` (dialect) is auto-detected by
    shape (:func:`blindfold_devtools.dialect.detect_dialect`).
    """
    endpoint = detect_dialect(payload)
    original_hop_texts = [text for _hop_kind, text in hop_texts(payload)]

    if endpoint == MESSAGES:
        blinded, session = blindfold_payload(
            payload,
            mapping,
            l3_detector,
            inbox=None,
            declared_tools=extract_declared_tools_messages(payload),
            workspace=workspace,
        )
    else:
        blinded, session = blindfold_chat_completions_payload(
            payload,
            mapping,
            l3_detector,
            inbox=None,
            declared_tools=extract_declared_tools_chat_completions(payload),
            workspace=workspace,
        )

    l3_wired = l3_detector is not None
    detections = tuple(
        _reconstruct_hop(hop, text, mapping, l3_wired=l3_wired)
        for hop, text in zip(session.hops, original_hop_texts)
    )

    return ReplayResult(
        endpoint=endpoint,
        payload=blinded,
        session=session,
        l3_wired=l3_wired,
        detections=detections,
    )


__all__ = ["CHAT_COMPLETIONS", "MESSAGES", "REPLAY_OUTCOME", "ReplayResult", "replay", "wrap_text_payload"]
