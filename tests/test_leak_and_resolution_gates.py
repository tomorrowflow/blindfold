"""Blindfold-engine seam: the egress split (ADR-0020, issue #47, SEC-5/SEC-6).

``verify_pass`` used to run both checks after restore, so a leak was only ever
detected post-hoc. It is now two single-purpose gates around the egress boundary:

- ``leak_gate`` — pre-egress: raises if a known real entity value is present in a
  payload about to cross egress. Runs BEFORE ``upstream.send_*``.
- ``resolution_gate`` — post-restore: raises if an injected surrogate is left
  unresolved in the client-visible response. Runs AFTER restore.

The two failure modes are covered by distinct tests, one gate at a time.
"""

import logging

import pytest

from blindfold.engine import (
    LeakError,
    UnresolvedSurrogateError,
    blindfold_payload,
    leak_gate,
    resolution_gate,
    restore_response,
)
from blindfold.surrogates import SurrogateMapping


def _mapping() -> SurrogateMapping:
    # Engine-mechanics tests own their fixture data (decoupled from the entity-graph seed).
    return SurrogateMapping.from_pairs(
        [("Anna Schmidt", "Berta Vogel"), ("Markus Wagner", "Tobias Lehmann")]
    )


def test_leak_gate_accepts_a_blindfolded_outbound_payload():
    mapping = _mapping()
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Hi Anna Schmidt"}],
    }
    blinded, _session = blindfold_payload(payload, mapping)

    # Should not raise: every real value was already replaced with its surrogate.
    leak_gate(blinded, mapping)


def test_leak_gate_raises_when_a_real_entity_value_is_in_the_outbound_payload():
    mapping = _mapping()
    # A blindfold miss: the real value is still present in what would egress.
    leaky_outbound = {
        "messages": [{"role": "user", "content": "Contact Anna Schmidt now."}]
    }

    with pytest.raises(LeakError):
        leak_gate(leaky_outbound, mapping)


def test_leak_gate_logs_a_clear_warning_naming_the_leaked_real_value(caplog):
    """Issue #17 AC (carried into the pre-egress gate): a leak surfaces a clear warning.

    A clear warning means the log record names the offending value so an operator can
    see exactly which entity slipped through. Raising alone is not a warning surface.
    """
    mapping = _mapping()
    leaky_outbound = {
        "messages": [{"role": "user", "content": "Contact Anna Schmidt now."}]
    }

    with caplog.at_level(logging.WARNING, logger="blindfold.engine"):
        with pytest.raises(LeakError):
            leak_gate(leaky_outbound, mapping)

    warnings = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert any("Anna Schmidt" in record.getMessage() for record in warnings), (
        "leak_gate must surface a clear warning naming the leaked real value, "
        f"but no WARNING record contained 'Anna Schmidt'. Records: "
        f"{[r.getMessage() for r in warnings]}"
    )


def test_resolution_gate_accepts_a_clean_round_trip():
    mapping = _mapping()
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Hi Anna Schmidt"}],
    }
    blinded, session = blindfold_payload(payload, mapping)
    anna_surrogate = mapping.surrogate_for("Anna Schmidt")
    provider_response = {
        "content": [{"type": "text", "text": f"{anna_surrogate} replied."}]
    }
    restored = restore_response(provider_response, session)

    # Should not raise.
    resolution_gate(restored, session)


def test_resolution_gate_raises_when_an_injected_surrogate_is_left_unresolved():
    mapping = _mapping()
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Hi Anna Schmidt"}],
    }
    blinded, session = blindfold_payload(payload, mapping)
    anna_surrogate = mapping.surrogate_for("Anna Schmidt")
    # Restore failed to reverse the injected surrogate (it is still client-visible).
    unrestored = {"content": [{"type": "text", "text": f"{anna_surrogate} replied."}]}

    with pytest.raises(UnresolvedSurrogateError):
        resolution_gate(unrestored, session)


def test_resolution_gate_logs_a_clear_warning_naming_the_unresolved_surrogate(caplog):
    """The gate also surfaces a clear, identifying warning on the resolution failure mode."""
    mapping = _mapping()
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Hi Anna Schmidt"}],
    }
    blinded, session = blindfold_payload(payload, mapping)
    anna_surrogate = mapping.surrogate_for("Anna Schmidt")
    unrestored = {"content": [{"type": "text", "text": f"{anna_surrogate} replied."}]}

    with caplog.at_level(logging.WARNING, logger="blindfold.engine"):
        with pytest.raises(UnresolvedSurrogateError):
            resolution_gate(unrestored, session)

    warnings = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert any(anna_surrogate in record.getMessage() for record in warnings), (
        "resolution_gate must surface a clear warning naming the unresolved surrogate, "
        f"but no WARNING record contained {anna_surrogate!r}. Records: "
        f"{[r.getMessage() for r in warnings]}"
    )
