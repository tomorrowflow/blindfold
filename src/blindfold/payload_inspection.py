"""Payload inspection: an admin-gated, auto-disarming arm/disarm flag (ADR-0059
§4, issue #398).

CONTEXT.md / ADR-0059: a **product** surface that retains, while armed, the
rewritten payload leaves a blindfold pass wrote -- so an operator can see what
the provider actually received. This module is only the arm/disarm precondition
-- retaining nothing yet.

The flag + expiry timer live here, in the proxy process -- never the menu-bar
app, never the shared **store** -- so both the armed behaviour and the
auto-disarm survive a menu-bar-app crash, and disarm-on-restart falls out for
free (a fresh process makes a fresh instance, which starts disarmed). Armed for
a fixed 30 minutes; there is no capability toggle here (unlike
:class:`~blindfold.unprotected_mode.UnprotectedMode`) because arming itself is
already gated on the ``admin`` role at the HTTP layer (``app.py``) -- ADR-0059
§4's deliberate departure from the Unprotected-mode shape.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

ARM_DURATION_SECONDS = 30 * 60


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PayloadInspectionStatus:
    """The armed state + remaining time, readable by an authorized caller.

    ``armed_at`` (issue #400) is the wall-clock moment arming took effect --
    ``None`` while disarmed. It lets a reader tell an exchange that predates
    arming apart from one that was armed but has since aged out of the
    retention ring buffer, which ``armed``/``remaining_seconds`` alone (both
    about the CURRENT moment, not a given exchange's) cannot answer.
    """

    armed: bool
    remaining_seconds: float | None
    armed_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "armed": self.armed,
            "remaining_seconds": self.remaining_seconds,
            "armed_at": self.armed_at,
        }


class PayloadInspection:
    """Process-global Payload inspection arm/disarm state (ADR-0059 §4).

    ``clock`` is injected (mirroring ``UnprotectedMode``) so tests control the
    30-minute auto-disarm deterministically without a real sleep.
    """

    def __init__(
        self,
        clock=time.monotonic,
        now_iso: Callable[[], str] = _utc_now_iso,
        on_disarm: Callable[[], None] | None = None,
    ) -> None:
        self._clock = clock
        self._now_iso = now_iso
        self._on_disarm = on_disarm
        self._armed = False
        self._expires_at: float | None = None
        self._armed_at: str | None = None

    def arm(self) -> None:
        self._armed = True
        self._expires_at = self._clock() + ARM_DURATION_SECONDS
        self._armed_at = self._now_iso()

    def disarm(self) -> None:
        """Disarm (issue #420: both this explicit call and the auto-disarm in
        `_expire_if_due` below route through here) and, if a release hook was
        injected, invoke it -- the retained leaves' actual release lives
        outside this class (ADR-0059 §3: a separate store instance), so this
        is the seam that keeps the announced bound from being cosmetic."""
        self._armed = False
        self._expires_at = None
        self._armed_at = None
        if self._on_disarm is not None:
            self._on_disarm()

    def is_armed(self) -> bool:
        self._expire_if_due()
        return self._armed

    def _expire_if_due(self) -> None:
        if self._armed and self._clock() >= self._expires_at:
            self.disarm()

    def status(self) -> PayloadInspectionStatus:
        armed = self.is_armed()
        remaining = max(0.0, self._expires_at - self._clock()) if armed else None
        return PayloadInspectionStatus(
            armed=armed,
            remaining_seconds=remaining,
            armed_at=self._armed_at if armed else None,
        )
