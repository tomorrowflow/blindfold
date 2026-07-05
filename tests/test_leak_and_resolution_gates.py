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


def test_leak_gate_logs_a_clear_warning_referencing_the_leaked_entity_by_surrogate(
    caplog,
):
    """Issue #40 (SEC-3): a leak surfaces a clear warning WITHOUT the real value.

    A clear warning means the log record identifies the offending entity so an
    operator can see exactly which entity slipped through — but by its surrogate,
    never the plaintext (the real-value side is never stored/surfaced in plaintext).
    """
    mapping = _mapping()
    leaky_outbound = {
        "messages": [{"role": "user", "content": "Contact Anna Schmidt now."}]
    }
    anna_surrogate = mapping.surrogate_for("Anna Schmidt")

    with caplog.at_level(logging.WARNING, logger="blindfold.engine"):
        with pytest.raises(LeakError):
            leak_gate(leaky_outbound, mapping)

    warnings = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert any(anna_surrogate in record.getMessage() for record in warnings), (
        "leak_gate must surface a clear warning referencing the leaked entity's "
        f"surrogate ({anna_surrogate!r}), but no WARNING record contained it. "
        f"Records: {[r.getMessage() for r in warnings]}"
    )
    assert not any("Anna Schmidt" in record.getMessage() for record in warnings), (
        "leak_gate must never log the real value it just blocked. "
        f"Records: {[r.getMessage() for r in warnings]}"
    )


class _UnmintedLeak(SurrogateMapping):
    """Test double: reports a real value via ``real_values()`` that was never minted
    a surrogate — a blindfold-engine miss on a value the mapping never saw, so
    :func:`scrub_entity_reference` has no surrogate to fall back on.
    """

    def real_values(self) -> list[str]:
        return ["Quentin"]


def test_leak_gate_falls_back_to_a_hashed_id_when_the_leak_has_no_surrogate(caplog):
    """Issue #40 (SEC-3): even with no surrogate minted, the plaintext must not leak."""
    mapping = _UnmintedLeak()
    leaky_outbound = {"messages": [{"role": "user", "content": "Brief Quentin now."}]}

    with caplog.at_level(logging.WARNING, logger="blindfold.engine"):
        with pytest.raises(LeakError) as excinfo:
            leak_gate(leaky_outbound, mapping)

    assert "Quentin" not in str(excinfo.value)
    assert "hash:" in str(excinfo.value)
    warnings = [record.getMessage() for record in caplog.records]
    assert not any("Quentin" in w for w in warnings), warnings


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
