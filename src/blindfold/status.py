"""GET /v1/status primitives (issue #92).

The single status contract consumed by both the management app's Home/Status view
and the future macOS menu bar item. `/v1/status` is deliberately outside
`/v1/management/*` (ADR-0011): not workspace-scoped, not role-gated -- the security
boundary is the existing loopback-only bind (ADR-0021), and the payload is scrubbed
by construction: dependency names, sub-reason codes, counts, never entity content,
never secrets.

This module owns the small primitives the endpoint (app.py) composes:

- :class:`DependencyHealth` / :func:`compute_state` -- the shape of one dependency's
  health and the single Protected/Degraded rule computed from all of them.
- :class:`CachedHealthProbe` -- wraps a dependency health check with a short TTL so
  the (~5s-polled) endpoint never itself becomes a probe storm against L3/Transit.
- :class:`BlockHistory` -- a rolling window of fail-closed/leak-gate blocks (the same
  `_blocked_response` funnel #91 built), so `blocks.recent` carries the identical
  scrubbed reason + management_url as the 503 body, never entity plaintext.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable


@dataclass(frozen=True)
class DependencyHealth:
    """One dependency's scrubbed-by-construction health (issue #92).

    ``detail`` is a scrubbed, non-secret diagnostic string (e.g. "ollama
    unreachable") -- never entity content, never a credential. ``latency_ms``
    (issue #110) is the wall-clock cost of the probe call that produced this
    result -- ``None`` when no probe was actually run to produce it (e.g. a
    cache hit's original probe already carries it; an unconfigured-dependency
    short-circuit that never made a call has none to report).
    """

    healthy: bool
    detail: str | None = None
    latency_ms: float | None = None

    def to_dict(self) -> dict:
        body: dict = {"healthy": self.healthy}
        if self.detail is not None:
            body["detail"] = self.detail
        if self.latency_ms is not None:
            body["latency_ms"] = self.latency_ms
        return body


def compute_state(dependencies: dict[str, DependencyHealth]) -> str:
    """"protected" iff every dependency is healthy, else "degraded" (issue #92).

    Computed server-side -- one place owns the Protected/Degraded rule; consumers
    (Home view, menu bar) only render it, per the issue's own framing.
    """
    if all(health.healthy for health in dependencies.values()):
        return "protected"
    return "degraded"


class CachedHealthProbe:
    """A dependency health probe wrapped with a short TTL cache (issue #92).

    Rapid repeated `/v1/status` polls must collapse into a single underlying probe
    call within ``ttl_seconds`` -- the TTL absorbs a poll storm instead of forwarding
    every poll straight through to a (possibly network-bound) probe. ``clock`` is
    injectable (monotonic by default) so tests can control TTL expiry deterministically.
    """

    def __init__(
        self,
        probe: Callable[[], DependencyHealth],
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._probe = probe
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._cached: DependencyHealth | None = None
        self._cached_at: float | None = None

    def check(self) -> DependencyHealth:
        now = self._clock()
        if (
            self._cached is not None
            and self._cached_at is not None
            and (now - self._cached_at) < self._ttl_seconds
        ):
            return self._cached
        result = self._probe()
        if result.latency_ms is None:
            elapsed_ms = (self._clock() - now) * 1000
            result = replace(result, latency_ms=round(elapsed_ms, 1))
        self._cached = result
        self._cached_at = now
        return result


class RecentFailureHealth:
    """Passive health signal: unhealthy for a bounded window after an observed failure.

    For a dependency with no cheap standalone active probe of its own (e.g.
    upstream -- the paid provider), the real signal is "did the last real request to
    this dependency fail", decaying back to healthy automatically once the window
    passes rather than requiring every success call site to mark it healthy again.
    """

    def __init__(
        self,
        unhealthy_window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._unhealthy_window_seconds = unhealthy_window_seconds
        self._clock = clock
        self._unhealthy_until: float | None = None
        self._detail: str | None = None

    def mark_unhealthy(self, detail: str) -> None:
        self._unhealthy_until = self._clock() + self._unhealthy_window_seconds
        self._detail = detail

    def check(self) -> DependencyHealth:
        if self._unhealthy_until is not None and self._clock() < self._unhealthy_until:
            return DependencyHealth(healthy=False, detail=self._detail)
        return DependencyHealth(healthy=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class BlockRecord:
    """One fail-closed/leak-gate block, scrubbed by construction (issue #92).

    Mirrors the 503 body's own fields exactly (#91 / ADR-0027) -- ``scrubbed_reason``
    and ``management_url`` are copied verbatim from the same `_blocked_response`
    funnel, never re-derived, so the two surfaces can never drift or leak.
    """

    ts: str
    sub_reason: str
    scrubbed_reason: str
    management_url: str

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "sub_reason": self.sub_reason,
            "scrubbed_reason": self.scrubbed_reason,
            "management_url": self.management_url,
        }


class BlockHistory:
    """Rolling window of recent fail-closed/leak-gate blocks (issue #92).

    In-memory, process-global (like `AuditLog`) -- persistence is out of scope this
    slice. ``recent()`` prunes entries older than ``window_minutes`` on every read,
    so the window is always accurate as of "now" without a background sweep.
    """

    def __init__(
        self,
        window_minutes: int = 15,
        clock: Callable[[], float] = time.monotonic,
        now_iso: Callable[[], str] = _utc_now_iso,
    ) -> None:
        self._window_minutes = window_minutes
        self._clock = clock
        self._now_iso = now_iso
        self._entries: list[tuple[float, BlockRecord]] = []

    @property
    def window_minutes(self) -> int:
        return self._window_minutes

    def record(self, sub_reason: str, scrubbed_reason: str, management_url: str) -> None:
        self._entries.append(
            (
                self._clock(),
                BlockRecord(
                    ts=self._now_iso(),
                    sub_reason=sub_reason,
                    scrubbed_reason=scrubbed_reason,
                    management_url=management_url,
                ),
            )
        )

    def recent(self) -> list[BlockRecord]:
        cutoff = self._clock() - self._window_minutes * 60
        self._entries = [entry for entry in self._entries if entry[0] >= cutoff]
        return [record for _, record in self._entries]
