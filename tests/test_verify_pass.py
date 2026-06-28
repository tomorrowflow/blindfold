"""Blindfold-engine seam: the verify pass (ADR-0006, leak-audit clause D).

After restore, assert (1) no real entity value leaked into the outbound payload and
(2) no injected surrogate was left unresolved in the restored response. The two
failure modes are covered by distinct tests.
"""

import pytest

from blindfold.engine import (
    ExchangeSession,
    LeakError,
    UnresolvedSurrogateError,
    blindfold_payload,
    restore_response,
    verify_pass,
)
from blindfold.surrogates import SurrogateMapping


def _mapping() -> SurrogateMapping:
    # Engine-mechanics tests own their fixture data (decoupled from the entity-graph seed).
    return SurrogateMapping.from_pairs(
        [("Anna Schmidt", "Berta Vogel"), ("Markus Wagner", "Tobias Lehmann")]
    )


def test_verify_pass_accepts_a_clean_round_trip():
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
    verify_pass(blinded, restored, session, mapping)


def test_verify_pass_raises_when_a_real_entity_value_is_in_the_outbound_payload():
    mapping = _mapping()
    session = ExchangeSession()
    # A blindfold miss: the real value is still present in what would egress.
    leaky_outbound = {
        "messages": [{"role": "user", "content": "Contact Anna Schmidt now."}]
    }
    restored = {"content": [{"type": "text", "text": "ok"}]}

    with pytest.raises(LeakError):
        verify_pass(leaky_outbound, restored, session, mapping)


def test_verify_pass_raises_when_an_injected_surrogate_is_left_unresolved():
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
        verify_pass(blinded, unrestored, session, mapping)
