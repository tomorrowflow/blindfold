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
    ExchangeSession,
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


def test_resolution_gate_does_not_false_positive_on_a_sub_token_containment():
    # ADR-0024: restore correctly leaves "Weberei" untouched (surrogate "Weber" is
    # merely its prefix, not a reference to the surrogate). The resolution gate must
    # not treat that coincidental substring as a left-unresolved surrogate — it would
    # otherwise fail-close a benign response that was never a restore target.
    session = ExchangeSession()
    session.record("Weber", "Müller")
    clean = {"content": [{"type": "text", "text": "Die Weberei war geschlossen."}]}

    # Should not raise.
    resolution_gate(clean, session)


def test_resolution_gate_still_raises_when_a_suffixed_surrogate_is_left_unresolved():
    # A stricter detector than the restorer is fine (issue #75 acceptance criterion),
    # but it must still catch a genuinely unresolved injected surrogate that carries
    # a closed-set suffix — not just the bare form.
    session = ExchangeSession()
    session.record("Weber", "Müller")
    unrestored = {"content": [{"type": "text", "text": "Webers report was thorough."}]}

    with pytest.raises(UnresolvedSurrogateError):
        resolution_gate(unrestored, session)


def test_resolution_gate_does_not_fail_close_on_a_leftover_component():
    # ADR-0036: a leftover surrogate component (left because it was generic or
    # ambiguous) is a synthetic token, never a real value, so it must never
    # fail-close a response — only a real-value leak or an unresolved FULL
    # injected surrogate does.
    session = ExchangeSession()
    session.record("Carla Distel", "Sarah Bergmann")
    session.record("Carla Weber", "Petra Klein")
    # "Carla" is ambiguous (shared by two surrogates) and stays unresolved by
    # design — must not trip the gate.
    restored = {"content": [{"type": "text", "text": "Carla called earlier."}]}

    # Should not raise.
    resolution_gate(restored, session)


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
