"""Payload inspection's retained-leaf record + bounded store (ADR-0059 §2-§4,
issue #399).

Two halves, deliberately kept apart:

- ``engine.py`` decides WHAT gets retained -- a :class:`RewrittenLeaf` per
  string leaf the blindfold pass actually rewrote, spans recorded at mint
  time via :class:`~blindfold.engine._LeafAccumulator` -- and only while
  ``ExchangeSession`` was constructed armed (ADR-0059 §3: the armed flag is
  read once per exchange, gating a record, never a behaviour).
- This module holds WHERE it lands once an exchange finishes:
  :class:`RewrittenLeafStore`, a separate, bounded, in-memory, per-workspace
  ring buffer -- "keep this in a separate store, not as extra fields on the
  [Processing] trace record" (ADR-0059 §3), mirroring
  :class:`~blindfold.payload_inspection.PayloadInspection`'s own process-
  global, never-persisted shape: a fresh process starts with an empty store,
  so disarm-on-restart (ADR-0059 §4) falls out for free.

Retains only the last **5** exchanges (ADR-0059 §4), oldest evicted -- never
the store, never disk. A blocked exchange is retained too, marked
``blocked=True`` ("never sent", ADR-0059 §4): a fail-closed block is the
most interesting exchange to look at, and the mark is load-bearing -- a
reader must never conclude a retained payload egressed.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RewrittenSpan:
    """One rewritten span, at its offset into the retained leaf's own
    blindfolded text (issue #399) -- never a real value, only the surrogate
    written there and which detection layer produced it (display-only, see
    :class:`~blindfold.engine.ReplacementSpan`)."""

    start: int
    end: int
    surrogate: str
    layer: str

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "surrogate": self.surrogate,
            "layer": self.layer,
        }


@dataclass(frozen=True)
class RewrittenLeaf:
    """One string leaf the blindfold pass rewrote (issue #399, ADR-0059 §2):
    ``text`` is the leaf in blindfolded form only -- no real value is ever
    carried here. ``leaf_id`` is a stable walk-order id; ``label`` is a
    display label naming the kind of place this leaf came from (hop kind
    plus block/field type -- a tool-call input, a tool description, a user
    text block, a tool-result body), not a JSON path (ADR-0059 §2's own
    rejection of path-level addressability)."""

    leaf_id: str
    label: str
    text: str
    spans: tuple[RewrittenSpan, ...] = ()

    def to_dict(self) -> dict:
        return {
            "leaf_id": self.leaf_id,
            "label": self.label,
            "text": self.text,
            "spans": [span.to_dict() for span in self.spans],
        }


@dataclass(frozen=True)
class RetainedExchange:
    """One retained exchange's leaves (issue #399). ``blocked`` is ADR-0059
    §4's "never sent" mark -- True for an exchange the pre-egress leak gate
    stopped after the blindfolded payload was fully constructed; the
    retained leaves never reached the provider."""

    ts: str
    workspace: str
    blocked: bool
    leaves: tuple[RewrittenLeaf, ...] = ()

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "workspace": self.workspace,
            "blocked": self.blocked,
            "leaves": [leaf.to_dict() for leaf in self.leaves],
        }


class RewrittenLeafStore:
    """In-memory, count-bounded (last 5, ADR-0059 §4) ring buffer of retained
    exchanges, never persisted to the store, empty after a restart -- the
    same process-global, evaporate-on-restart shape as
    :class:`~blindfold.payload_inspection.PayloadInspection` and
    :class:`~blindfold.processing_trace.ProcessingTraceBuffer`, just a
    separate instance (ADR-0059 §3) rather than folded into either.
    """

    def __init__(self, maxlen: int = 5, now_iso: Callable[[], str] = _utc_now_iso) -> None:
        self._entries: deque[RetainedExchange] = deque(maxlen=maxlen)
        self._now_iso = now_iso

    def retain(
        self, *, workspace: str, leaves: Sequence[RewrittenLeaf], blocked: bool
    ) -> None:
        self._entries.append(
            RetainedExchange(
                ts=self._now_iso(),
                workspace=workspace,
                blocked=blocked,
                leaves=tuple(leaves),
            )
        )

    def for_workspace(self, workspace: str) -> list[RetainedExchange]:
        return [entry for entry in self._entries if entry.workspace == workspace]
