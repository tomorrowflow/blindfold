"""Payload inspection: an admin-gated, auto-disarming arm/disarm flag (ADR-0059
§4, issue #398).

CONTEXT.md / ADR-0059: a **product** surface that retains, while armed, the
rewritten payload leaves a blindfold pass wrote -- so an operator can see what
the provider actually received. This module is only the arm/disarm precondition
-- retaining nothing yet.

The flag + expiry timer live here, in the proxy process -- never the menu-bar
app, never the shared **store** -- so both the armed behaviour and the
auto-disarm survive a menu-bar-app crash, and disarm-on-restart falls out for
free (a fresh process makes a fresh instance, which starts disarmed). An
``admin`` picks one of three retention windows when arming (ADR-0059 amendment
#431 §4, issue #433) -- 30 minutes (default), 2 hours, or until disarmed (no
timer) -- each with its own count bound; there is no capability toggle here
(unlike :class:`~blindfold.unprotected_mode.UnprotectedMode`) because arming
itself is already gated on the ``admin`` role at the HTTP layer (``app.py``)
-- ADR-0059 §4's deliberate departure from the Unprotected-mode shape.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

DEFAULT_RETENTION_WINDOW = "30m"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RetentionWindow:
    """One selectable retention window (ADR-0059 amendment #431 §4, issue #433):
    a fixed pairing of an auto-disarm duration (``None`` for "until disarmed" --
    never on a timer) and the count bound the ring evicts the oldest retained
    exchange against. ``label`` is the display string Settings/the banner render;
    the dict key below (``"30m"``/``"2h"``/``"until_disarmed"``) is the wire/query
    value, never shown to an operator directly."""

    label: str
    duration_seconds: float | None
    count_bound: int


RETENTION_WINDOWS: dict[str, RetentionWindow] = {
    "30m": RetentionWindow(label="30 minutes", duration_seconds=30 * 60, count_bound=25),
    "2h": RetentionWindow(label="2 hours", duration_seconds=2 * 60 * 60, count_bound=100),
    "until_disarmed": RetentionWindow(
        label="until disarmed", duration_seconds=None, count_bound=200
    ),
}


class InvalidRetentionWindowError(Exception):
    """Raised by :meth:`PayloadInspection.arm` for an unrecognized window key."""


@dataclass(frozen=True)
class PayloadInspectionStatus:
    """The armed state + remaining time, readable by an authorized caller.

    ``armed_at`` (issue #400) is the wall-clock moment arming took effect --
    ``None`` while disarmed. It lets a reader tell an exchange that predates
    arming apart from one that was armed but has since aged out of the
    retention ring buffer, which ``armed``/``remaining_seconds`` alone (both
    about the CURRENT moment, not a given exchange's) cannot answer.

    ``window``/``count_bound`` (issue #433) name the retention window chosen at
    arm time and its count bound -- ``None`` while disarmed, since no window is
    in effect. ``remaining_seconds`` stays ``None`` for the untimed "until
    disarmed" window even while armed: there is no deadline to report.
    """

    armed: bool
    remaining_seconds: float | None
    armed_at: str | None = None
    window: str | None = None
    count_bound: int | None = None

    def to_dict(self) -> dict:
        return {
            "armed": self.armed,
            "remaining_seconds": self.remaining_seconds,
            "armed_at": self.armed_at,
            "window": self.window,
            "count_bound": self.count_bound,
        }


class PayloadInspection:
    """Process-global Payload inspection arm/disarm state (ADR-0059 §4).

    ``clock`` is injected (mirroring ``UnprotectedMode``) so tests control
    auto-disarm deterministically without a real sleep. ``on_arm`` (issue
    #433) fires once per :meth:`arm` call with the chosen window's count
    bound -- the seam that lets the retained-leaf store's own bound track
    whichever window was picked, mirroring ``on_disarm``'s existing release
    seam (ADR-0059 §3: two separate instances, wired together only through
    injected callables).
    """

    def __init__(
        self,
        clock=time.monotonic,
        now_iso: Callable[[], str] = _utc_now_iso,
        on_disarm: Callable[[], None] | None = None,
        on_arm: Callable[[int], None] | None = None,
    ) -> None:
        self._clock = clock
        self._now_iso = now_iso
        self._on_disarm = on_disarm
        self._on_arm = on_arm
        self._armed = False
        self._expires_at: float | None = None
        self._armed_at: str | None = None
        self._window: str | None = None

    def arm(self, window: str = DEFAULT_RETENTION_WINDOW) -> None:
        if window not in RETENTION_WINDOWS:
            raise InvalidRetentionWindowError(f"unrecognized retention window: {window!r}")
        spec = RETENTION_WINDOWS[window]
        self._armed = True
        self._window = window
        self._expires_at = (
            self._clock() + spec.duration_seconds if spec.duration_seconds is not None else None
        )
        self._armed_at = self._now_iso()
        if self._on_arm is not None:
            self._on_arm(spec.count_bound)

    def disarm(self) -> None:
        """Disarm (issue #420: both this explicit call and the auto-disarm in
        `_expire_if_due` below route through here) and, if a release hook was
        injected, invoke it -- the retained leaves' actual release lives
        outside this class (ADR-0059 §3: a separate store instance), so this
        is the seam that keeps the announced bound from being cosmetic."""
        self._armed = False
        self._expires_at = None
        self._armed_at = None
        self._window = None
        if self._on_disarm is not None:
            self._on_disarm()

    def is_armed(self) -> bool:
        self._expire_if_due()
        return self._armed

    def _expire_if_due(self) -> None:
        # The "until disarmed" window's `_expires_at` stays `None` (no timer,
        # ADR-0059 amendment #431 §4) -- it never auto-disarms here.
        if self._armed and self._expires_at is not None and self._clock() >= self._expires_at:
            self.disarm()

    def status(self) -> PayloadInspectionStatus:
        armed = self.is_armed()
        remaining = None
        if armed and self._expires_at is not None:
            remaining = max(0.0, self._expires_at - self._clock())
        spec = RETENTION_WINDOWS[self._window] if armed and self._window is not None else None
        return PayloadInspectionStatus(
            armed=armed,
            remaining_seconds=remaining,
            armed_at=self._armed_at if armed else None,
            window=self._window if armed else None,
            count_bound=spec.count_bound if spec is not None else None,
        )
