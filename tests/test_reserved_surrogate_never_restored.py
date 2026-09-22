"""ADR-0060 §4/§5: restore never reverses a reserved-namespace surrogate.

Containment (ADR-0060 §3, sibling #410) draws a world-acting request's plausible-pool
surrogates from the reserved namespace instead. Without this change, restore would
still reverse that opaque token on the way back -- the provider searches it, finds
nothing, and the model faithfully reports no results about what is, to the user, a
confident false negative concerning a real person. The opaque token surviving restore
*is* the disclosure (ADR-0060 §4): a reserved-form token is never reversed, on any leg,
while an ordinary plausible-pool surrogate is still restored exactly as before.

The recognizer is the existing closed syntactic class,
``store._mint.is_reserved_provisional_surrogate_form`` (ADR-0052) -- never a judgement
call or an allowlist.

Leak-audit: this slice is restore-only (clauses B/C). It only ever *omits* a restore
that used to happen, so closed-world (C) cannot widen -- proven here by the sibling
assertion that a non-reserved surrogate is still restored exactly as before. Clause A
(pre-egress: no real value crosses egress) is unaffected; nothing here changes what the
blinder sends outbound. Fail-closed (F): the resolution gate must not fail-close on the
now-deliberately-unrestored reserved token while still failing closed on any other
unresolved surrogate -- covered below.
"""

from __future__ import annotations

import pytest

from blindfold.engine import (
    ExchangeSession,
    StreamingRestorer,
    UnresolvedSurrogateError,
    resolution_gate,
    restore_response,
    restore_tool_call_json,
)


def _session_with(injected: dict[str, str]) -> ExchangeSession:
    session = ExchangeSession()
    for surrogate, real in injected.items():
        session.record(surrogate, real)
    return session


def test_a_reserved_form_surrogate_in_a_buffered_response_is_not_restored():
    # AC1: a reserved-form token (ADR-0052's `BFP{NNNN}` shape) reaches the client
    # unrestored on the buffered path.
    session = _session_with({"BFP0007": "Elena Voss"})
    provider_response = {
        "content": [{"type": "text", "text": "No results found for BFP0007."}]
    }

    restored = restore_response(provider_response, session)

    assert restored["content"][0]["text"] == "No results found for BFP0007."


def test_a_plausible_pool_surrogate_alongside_it_is_still_restored():
    # AC3: the exemption is exactly the reserved-form class and nothing wider -- a
    # plausible pool surrogate in the very same response is restored as before.
    session = _session_with({"BFP0007": "Elena Voss", "Anna Schmidt": "Berta Vogel"})
    provider_response = {
        "content": [
            {
                "type": "text",
                "text": "No results found for BFP0007. Anna Schmidt agrees.",
            }
        ]
    }

    restored = restore_response(provider_response, session)

    assert (
        restored["content"][0]["text"]
        == "No results found for BFP0007. Berta Vogel agrees."
    )


def test_a_reserved_form_surrogate_streamed_in_prose_is_not_restored():
    # AC2 (streaming, prose leg): the same guarantee holds via StreamingRestorer.
    session = _session_with({"BFP0007": "Elena Voss"})
    restorer = StreamingRestorer(session)

    emitted = [
        restorer.feed("No results found for "),
        restorer.feed("BFP0007, sorry."),
        restorer.flush(),
    ]

    joined = "".join(emitted)
    assert joined == "No results found for BFP0007, sorry."


def test_a_reserved_form_surrogate_inside_a_streamed_tool_call_argument_is_not_restored():
    # AC2 (streaming, tool-call-argument leg): restore_tool_call_json is the seam the
    # streaming path's held-back-and-rejoin strategy (engine.py, app.py
    # _restore_tool_use_json) hands the fully reassembled tool-call JSON to.
    session = _session_with({"BFP0007": "Elena Voss"})
    assembled = '{"query": "BFP0007"}'

    restored = restore_tool_call_json(assembled, session)

    assert restored == '{"query": "BFP0007"}'


def test_resolution_gate_does_not_fail_close_on_an_unrestored_reserved_form_token():
    # AC4 (first half): a reserved-form token left in the client-visible response by
    # design (this restore change) must not trip the post-restore resolution gate
    # (ADR-0020) -- it is the intended disclosure, not a restore miss.
    session = _session_with({"BFP0007": "Elena Voss"})
    restored = {
        "content": [{"type": "text", "text": "No results found for BFP0007."}]
    }

    resolution_gate(restored, session)  # must not raise


def test_resolution_gate_still_raises_on_a_genuinely_unresolved_plausible_surrogate_planted_alongside_a_reserved_one():
    # AC4 (second half): plant one of each in the same response -- the reserved token
    # must be ignored while the plausible-pool surrogate left unresolved still fails
    # closed exactly as before.
    session = _session_with({"BFP0007": "Elena Voss", "Anna Schmidt": "Berta Vogel"})
    unrestored = {
        "content": [
            {
                "type": "text",
                "text": "No results found for BFP0007. Anna Schmidt agrees.",
            }
        ]
    }

    with pytest.raises(UnresolvedSurrogateError):
        resolution_gate(unrestored, session)


def test_component_restore_does_not_reverse_a_sub_span_of_a_reserved_form_token():
    # AC5: component restore (ADR-0036) must not reverse a reserved-form token OR any
    # sub-span of one. A reserved-form token is always a single whitespace-free
    # word (ADR-0052), so it can never itself contribute a positional component key
    # (the >=2-word guard) -- proven here through the public restore seam: neither a
    # lone real-value word nor a bare prefix of the reserved token is rewritten.
    session = _session_with({"BFP0007": "Elena Voss"})
    provider_response = {
        "content": [
            {"type": "text", "text": "No Voss found; the reference was BFP."}
        ]
    }

    restored = restore_response(provider_response, session)

    assert (
        restored["content"][0]["text"]
        == "No Voss found; the reference was BFP."
    )
