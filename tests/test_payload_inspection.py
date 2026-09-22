"""Payload inspection's process-global arm/disarm state (ADR-0059 §4, issue #398).

Unit-level tests for :class:`blindfold.payload_inspection.PayloadInspection` --
the armed flag + fixed-30-minute expiry that lives in the proxy process, mirroring
``UnprotectedMode`` (``unprotected_mode.py``, ADR-0038): never the menu-bar app,
never the shared store, so both the armed behaviour and the auto-disarm survive a
menu-bar-app crash, and a proxy restart (a fresh process, hence a fresh instance)
starts disarmed by construction. HTTP-level control-endpoint tests (role gate,
audit-on-arm/refusal) live in ``test_payload_inspection_endpoint.py``.

This slice retains nothing yet -- it is the arm/disarm lifecycle only (ADR-0059
§4's precondition). N/A this module: A-G leak-audit clauses -- no request-path
payload is touched here; nothing is read from this flag on the request path this
slice, and retention is a future slice.
"""

from __future__ import annotations

from blindfold.payload_inspection import PayloadInspection


def test_defaults_disarmed():
    inspection = PayloadInspection()
    assert inspection.is_armed() is False


def test_arm_activates():
    inspection = PayloadInspection()
    inspection.arm()
    assert inspection.is_armed() is True


def test_disarm_resumes_default():
    inspection = PayloadInspection()
    inspection.arm()
    assert inspection.is_armed() is True

    inspection.disarm()

    assert inspection.is_armed() is False


def test_auto_disarms_after_30_minutes_via_injected_clock():
    ticks = [0.0]
    inspection = PayloadInspection(clock=lambda: ticks[0])
    inspection.arm()
    assert inspection.is_armed() is True

    ticks[0] = 30 * 60 - 1  # one second before the deadline
    assert inspection.is_armed() is True

    ticks[0] = 30 * 60  # deadline reached
    assert inspection.is_armed() is False


def test_status_reports_remaining_seconds_while_armed():
    ticks = [0.0]
    inspection = PayloadInspection(clock=lambda: ticks[0])
    inspection.arm()

    ticks[0] = 10 * 60

    status = inspection.status()
    assert status.armed is True
    assert status.remaining_seconds == 20 * 60


def test_status_reports_no_remaining_seconds_while_disarmed():
    inspection = PayloadInspection()
    status = inspection.status()
    assert status.armed is False
    assert status.remaining_seconds is None


def test_status_reports_armed_at_wall_clock_while_armed():
    # Issue #400: the Processing trace needs a wall-clock "armed since" moment
    # to tell an exchange that predates arming apart from one that was armed
    # but has since been evicted from the retention ring buffer -- neither
    # `remaining_seconds` (monotonic-clock-derived) nor `armed` alone can
    # answer "was this exchange's own timestamp before or after arming".
    inspection = PayloadInspection(now_iso=lambda: "2026-09-22T10:00:00+00:00")
    inspection.arm()

    status = inspection.status()
    assert status.armed_at == "2026-09-22T10:00:00+00:00"


def test_status_reports_no_armed_at_while_disarmed():
    inspection = PayloadInspection()
    status = inspection.status()
    assert status.armed_at is None


def test_disarm_clears_armed_at():
    inspection = PayloadInspection(now_iso=lambda: "2026-09-22T10:00:00+00:00")
    inspection.arm()
    inspection.disarm()

    status = inspection.status()
    assert status.armed_at is None


def test_disarm_invokes_the_injected_release_hook():
    # Issue #420: disarm must release the retained leaves, but `PayloadInspection`
    # deliberately holds no reference to `RewrittenLeafStore` (ADR-0059 §3 -- two
    # separate instances). `on_disarm` is the seam: a generic callable, injected
    # the same way `clock`/`now_iso` already are, wired to the store's own
    # `clear()` at the app level (app.py) rather than baked in here.
    released = []
    inspection = PayloadInspection(on_disarm=lambda: released.append(True))
    inspection.arm()

    inspection.disarm()

    assert released == [True]


def test_auto_disarm_also_invokes_the_injected_release_hook_via_fake_clock():
    # Issue #420's more serious half: the 30-minute auto-disarm calls the same
    # `disarm()` internally (`_expire_if_due`), so it must release too -- pinned
    # here with a fake clock rather than a real sleep.
    ticks = [0.0]
    released = []
    inspection = PayloadInspection(
        clock=lambda: ticks[0], on_disarm=lambda: released.append(True)
    )
    inspection.arm()
    assert released == []

    ticks[0] = 30 * 60  # deadline reached
    assert inspection.is_armed() is False

    assert released == [True]


def test_a_fresh_instance_is_disarmed_mirroring_a_proxy_restart():
    # Nothing about this state is persisted (ADR-0059 §4: "disarms on proxy
    # restart") -- a fresh process makes a fresh PayloadInspection, which is
    # disarmed by construction. Arm one instance, then confirm a second,
    # independent instance (standing in for the process that restart produces)
    # never observes it.
    armed_before_restart = PayloadInspection()
    armed_before_restart.arm()
    assert armed_before_restart.is_armed() is True

    after_restart = PayloadInspection()

    assert after_restart.is_armed() is False
