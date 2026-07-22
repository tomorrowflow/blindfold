"""Unprotected mode's process-global state machine (ADR-0038, issue #180).

Unit-level tests for :class:`blindfold.unprotected_mode.UnprotectedMode` -- the
capability flag + active/bound/expiry state that lives in the proxy (never the
menu-bar app, never the shared store). HTTP-level control-endpoint and
request-path leak-audit tests live in ``test_unprotected_mode_control_endpoint.py``
and ``test_unprotected_mode_request_path.py`` respectively.

N/A this module: A-E/G (no request-path/mapping touched here, pure state machine).
F fail-closed: covered by ``test_capability_disabled_by_default_refuses_enable``
below -- the control-surface fail-closed instinct (ADR-0009/0019) this issue adds.
"""

from __future__ import annotations

import pytest

from blindfold.unprotected_mode import (
    BOUND_INFINITE,
    BOUND_NEXT_REQUEST,
    BOUND_TIMED,
    CapabilityDisabledError,
    InvalidBoundError,
    UnprotectedMode,
)


def test_capability_defaults_off():
    mode = UnprotectedMode()
    assert mode.capability_enabled is False


def test_capability_disabled_by_default_refuses_enable():
    mode = UnprotectedMode()
    with pytest.raises(CapabilityDisabledError):
        mode.enable(BOUND_NEXT_REQUEST)
    # The refused call must never have armed the mode.
    assert mode.is_active() is False


def test_enable_next_request_activates_once_capability_is_enabled():
    mode = UnprotectedMode()
    mode.enable_capability()
    mode.enable(BOUND_NEXT_REQUEST)
    assert mode.is_active() is True
    assert mode.status().bound == BOUND_NEXT_REQUEST


def test_disable_resumes_protection():
    mode = UnprotectedMode()
    mode.enable_capability()
    mode.enable(BOUND_INFINITE)
    assert mode.is_active() is True

    mode.disable()

    assert mode.is_active() is False
    assert mode.status().bound is None


def test_next_request_bound_auto_reverts_after_one_exchange():
    mode = UnprotectedMode()
    mode.enable_capability()
    mode.enable(BOUND_NEXT_REQUEST)

    mode.note_exchange_complete()

    assert mode.is_active() is False


def test_note_exchange_complete_does_not_revert_infinite_bound():
    mode = UnprotectedMode()
    mode.enable_capability()
    mode.enable(BOUND_INFINITE)

    mode.note_exchange_complete()

    assert mode.is_active() is True


def test_timed_bound_rejects_unsupported_minutes():
    mode = UnprotectedMode()
    mode.enable_capability()
    with pytest.raises(InvalidBoundError):
        mode.enable(BOUND_TIMED, minutes=7)
    assert mode.is_active() is False


def test_timed_bound_auto_reverts_on_expiry_via_injected_clock():
    ticks = [0.0]
    mode = UnprotectedMode(clock=lambda: ticks[0])
    mode.enable_capability()
    mode.enable(BOUND_TIMED, minutes=5)
    assert mode.is_active() is True

    ticks[0] = 5 * 60 - 1  # one second before the deadline
    assert mode.is_active() is True

    ticks[0] = 5 * 60  # deadline reached
    assert mode.is_active() is False


def test_infinite_bound_persists_until_explicit_disable():
    ticks = [0.0]
    mode = UnprotectedMode(clock=lambda: ticks[0])
    mode.enable_capability()
    mode.enable(BOUND_INFINITE)

    ticks[0] = 60 * 60 * 24 * 365  # a full year later
    assert mode.is_active() is True

    mode.disable()
    assert mode.is_active() is False
