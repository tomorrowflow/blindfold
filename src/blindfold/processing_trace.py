"""Processing trace: local, ephemeral, scrubbed per-exchange record (ADR-0035).

A live follow-along view of what the proxy is doing per request, replacing
`tail`-ing stdout. Mirrors :class:`~blindfold.status.BlockHistory`'s in-memory,
process-global, scrubbed-by-construction shape, but count-bounded rather than
time-windowed (ADR-0035 decisions 1-3, 6): a live view wants "the last ~200
exchanges", not a rolling time window, and must survive a traffic burst without
unbounded growth. Never persisted to the store -- evaporates on restart.

Each :class:`ProcessingTraceRecord` carries stage outcomes/counts/timings and
surrogate/hashed references only -- never a real value, raw hop content,
candidate-span text, or a payload diff.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

# ADR-0035 decision 7: exactly 3 outcome buckets. A Blocked record's `reason`
# carries the same scrubbed string the leak/l3-unavailable/resolution block
# already routes to the 503 body, the audit record, and the log line -- never
# a separately-derived string.
OUTCOME_PASSED = "passed"
OUTCOME_BLOCKED = "blocked"
OUTCOME_UPSTREAM_ERROR = "upstream_error"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ProcessingTraceRecord:
    """One scrubbed, exchange-level processing-trace record (ADR-0035).

    ``hops`` (issue #153, per-hop expansion) carries one already-scrubbed dict per
    hop (see :meth:`~blindfold.engine.HopDetail.to_dict`) -- counts, timings, and
    surrogate tokens only, never a real value, candidate-span text, or raw hop
    text. ``l3_provider``/``l3_duration_ms`` are the exchange-level rollup the
    collapsed row's L3 column reads: ``None`` when no hop actually ran L3.
    """

    ts: str
    workspace: str
    endpoint: str  # "messages" | "chat_completions"
    streamed: bool
    outcome: str  # one of OUTCOME_PASSED / OUTCOME_BLOCKED / OUTCOME_UPSTREAM_ERROR
    detected: int
    duration_ms: float
    reason: str | None = None
    hops: tuple[dict, ...] = ()
    l3_provider: str | None = None
    l3_duration_ms: float | None = None

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "workspace": self.workspace,
            "endpoint": self.endpoint,
            "streamed": self.streamed,
            "outcome": self.outcome,
            "detected": self.detected,
            "duration_ms": self.duration_ms,
            "reason": self.reason,
            "hops": list(self.hops),
            "l3_provider": self.l3_provider,
            "l3_duration_ms": self.l3_duration_ms,
        }


class ProcessingTraceBuffer:
    """In-memory, count-bounded (~200) ring buffer of processing-trace records.

    Process-global (like :class:`~blindfold.policy.AuditLog` and
    :class:`~blindfold.status.BlockHistory`), never written to the store, empty
    after restart -- the oldest record is evicted once the bound is exceeded.
    """

    def __init__(
        self, maxlen: int = 200, now_iso: Callable[[], str] = _utc_now_iso
    ) -> None:
        self._entries: deque[ProcessingTraceRecord] = deque(maxlen=maxlen)
        self._now_iso = now_iso

    def record(
        self,
        *,
        workspace: str,
        endpoint: str,
        streamed: bool,
        outcome: str,
        detected: int,
        duration_ms: float,
        reason: str | None = None,
        hops: Sequence[dict] = (),
        l3_provider: str | None = None,
        l3_duration_ms: float | None = None,
    ) -> None:
        self._entries.append(
            ProcessingTraceRecord(
                ts=self._now_iso(),
                workspace=workspace,
                endpoint=endpoint,
                streamed=streamed,
                outcome=outcome,
                detected=detected,
                duration_ms=duration_ms,
                reason=reason,
                hops=tuple(hops),
                l3_provider=l3_provider,
                l3_duration_ms=l3_duration_ms,
            )
        )

    def recent(self) -> list[ProcessingTraceRecord]:
        return list(self._entries)
