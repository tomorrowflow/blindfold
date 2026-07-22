"""Unprotected mode: a bounded, capability-gated override (ADR-0038, issue #180).

CONTEXT.md: "a temporary, local, operator-invoked override that suspends all
blindfolding: the detection pipeline does not run, nothing is surrogate-replaced,
and real entities egress to the provider as a pure relay." It is an override on
top of the configured global protection posture, never a change to it -- resuming
(or auto-revert) returns to whatever posture was set.

The flag + expiry timer live here, in the proxy process -- never the menu-bar app,
never the shared store (ADR-0038: "scoped to this machine's proxy only") -- so both
the suspended-blindfolding behavior and the auto-revert survive a menu-bar-app
crash. The capability defaults off (ADR-0009/0019 fail-closed instinct applied to
the control surface): :meth:`UnprotectedMode.enable` refuses until
:meth:`UnprotectedMode.enable_capability` has been called explicitly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

BOUND_NEXT_REQUEST = "next-request"
BOUND_TIMED = "timed"
BOUND_INFINITE = "infinite"
VALID_BOUNDS = (BOUND_NEXT_REQUEST, BOUND_TIMED, BOUND_INFINITE)
VALID_TIMED_MINUTES = (5, 15, 30)


class CapabilityDisabledError(Exception):
    """Raised by :meth:`UnprotectedMode.enable` while the capability flag is off."""


class InvalidBoundError(Exception):
    """Raised for an unrecognized ``bound``, or a ``timed`` bound with unsupported minutes."""


@dataclass(frozen=True)
class UnprotectedModeStatus:
    """The `/v1/status` contract's ``unprotected_mode`` shape (menu-bar alarm state)."""

    active: bool
    bound: str | None
    remaining_seconds: float | None

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "bound": self.bound,
            "remaining_seconds": self.remaining_seconds,
        }


class UnprotectedMode:
    """Process-global Unprotected-mode state: capability flag + active/bound/expiry.

    ``clock`` is injected (mirroring ``status.py``'s ``CachedHealthProbe``) so tests
    control expiry deterministically without a real sleep.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._capability_enabled = False
        self._active = False
        self._bound: str | None = None
        self._expires_at: float | None = None

    @property
    def capability_enabled(self) -> bool:
        return self._capability_enabled

    def enable_capability(self) -> None:
        self._capability_enabled = True

    def disable_capability(self) -> None:
        # A capability going back off can't leave the mode dangling active.
        self._capability_enabled = False
        self.disable()

    def enable(self, bound: str, *, minutes: int | None = None) -> None:
        """Activate Unprotected mode. Never mutates the global protection posture."""
        if not self._capability_enabled:
            raise CapabilityDisabledError(
                "Unprotected mode capability is disabled; enable it in Settings first"
            )
        if bound not in VALID_BOUNDS:
            raise InvalidBoundError(f"unrecognized bound: {bound!r}")
        if bound == BOUND_TIMED:
            if minutes not in VALID_TIMED_MINUTES:
                raise InvalidBoundError(
                    f"timed bound requires minutes in {VALID_TIMED_MINUTES}, got {minutes!r}"
                )
            self._expires_at = self._clock() + minutes * 60
        else:
            self._expires_at = None
        self._active = True
        self._bound = bound

    def disable(self) -> None:
        """Resume protection: returns to the configured global posture, unchanged."""
        self._active = False
        self._bound = None
        self._expires_at = None

    def is_active(self) -> bool:
        self._expire_if_due()
        return self._active

    def note_exchange_complete(self) -> None:
        """Consume a ``next-request`` grant after it has covered one exchange."""
        if self._active and self._bound == BOUND_NEXT_REQUEST:
            self.disable()

    def _expire_if_due(self) -> None:
        if (
            self._active
            and self._bound == BOUND_TIMED
            and self._clock() >= self._expires_at
        ):
            self.disable()

    def status(self) -> UnprotectedModeStatus:
        active = self.is_active()
        remaining = None
        if active and self._bound == BOUND_TIMED:
            remaining = max(0.0, self._expires_at - self._clock())
        return UnprotectedModeStatus(
            active=active,
            bound=self._bound if active else None,
            remaining_seconds=remaining,
        )
