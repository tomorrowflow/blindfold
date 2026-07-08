"""Blindfold-engine seam: restore is closed-world (ADR-0006).

Covers leak-audit clause B (restore returns real values) and clause C (closed-world:
only surrogates injected this exchange are reversed).
"""

from blindfold.engine import (
    _SUFFIXES,
    ExchangeSession,
    blindfold_payload,
    restore_response,
    restore_tool_call_json,
)
from blindfold.surrogates import SurrogateMapping


def _session_with(injected: dict[str, str]) -> ExchangeSession:
    session = ExchangeSession()
    for surrogate, real in injected.items():
        session.record(surrogate, real)
    return session


def _mapping() -> SurrogateMapping:
    # Engine-mechanics tests own their fixture data (decoupled from the entity-graph seed).
    return SurrogateMapping.from_pairs(
        [("Anna Schmidt", "Berta Vogel"), ("Markus Wagner", "Tobias Lehmann")]
    )


def _exchange():
    mapping = _mapping()
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Summarize Anna Schmidt's review."}
        ],
    }
    _blinded, session = blindfold_payload(payload, mapping)
    return mapping, session


def test_restore_swaps_injected_surrogates_back_to_real_values_in_prose():
    mapping, session = _exchange()
    anna_surrogate = mapping.surrogate_for("Anna Schmidt")

    provider_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": f"{anna_surrogate} approved the change."}
        ],
    }

    restored = restore_response(provider_response, session)

    text = restored["content"][0]["text"]
    assert text == "Anna Schmidt approved the change."
    # No surrogate remains client-visible.
    assert anna_surrogate not in text


def test_restore_is_closed_world_and_leaves_coincidental_lookalikes_untouched():
    mapping, session = _exchange()

    # "Tobias Lehmann" is the surrogate for "Markus Wagner" — a real entity NOT
    # injected in this exchange (only Anna was). The provider emitting it on its own
    # is a coincidental lookalike and must NOT be restored to the real value.
    coincidental = mapping.surrogate_for("Markus Wagner")
    provider_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": f"A user named {coincidental} also commented."}
        ],
    }

    restored = restore_response(provider_response, session)

    text = restored["content"][0]["text"]
    assert coincidental in text
    assert "Markus Wagner" not in text


def test_restore_transfers_a_closed_set_suffix_onto_the_real_value():
    # ADR-0024: an injected surrogate followed by a German genitive "-s" restores
    # with the suffix transferred to the real value, not left dangling on the surrogate.
    session = _session_with({"Müller": "Weber"})

    provider_response = {
        "content": [{"type": "text", "text": "Müllers report was thorough."}]
    }

    restored = restore_response(provider_response, session)

    text = restored["content"][0]["text"]
    assert text == "Webers report was thorough."


def test_restore_leaves_a_sub_token_containment_untouched():
    # ADR-0024 / DESIGN.md Top Risk #2: surrogate "Müller" is a sub-token of the
    # unrelated common noun "Müllerei" ("waste-disposal business") — restoring it
    # would silently turn an unrelated word into a leaked-shaped fragment of the
    # real value. Word-boundary matching keeps this word untouched.
    session = _session_with({"Müller": "Weber"})

    provider_response = {
        "content": [{"type": "text", "text": "Die Müllerei war geschlossen."}]
    }

    restored = restore_response(provider_response, session)

    text = restored["content"][0]["text"]
    assert text == "Die Müllerei war geschlossen."


def test_restore_tool_call_json_transfers_a_closed_set_suffix_onto_the_real_value():
    # ADR-0024: the tool-call JSON restore path shares _restore_text with prose, so
    # the same suffix-transfer behavior applies inside structured-arg string values.
    session = _session_with({"Müller": "Weber"})

    restored = restore_tool_call_json(
        {"recipient": "Müller", "body": "Please follow up with Müllers report."},
        session,
    )

    assert restored == {
        "recipient": "Weber",
        "body": "Please follow up with Webers report.",
    }


def test_restore_tool_call_json_leaves_a_sub_token_containment_untouched():
    # ADR-0024 / DESIGN.md Top Risk #2: same sub-token guard inside tool-call JSON.
    session = _session_with({"Müller": "Weber"})

    restored = restore_tool_call_json(
        {"note": "Die Müllerei war geschlossen."}, session
    )

    assert restored == {"note": "Die Müllerei war geschlossen."}


def test_restore_suffix_set_is_exactly_the_adr_0024_closed_list():
    # ADR-0024: the suffix set is a reviewed, pinned list — growing it is a code
    # change with tests, not a runtime tuning knob.
    assert set(_SUFFIXES) == {"s", "n", "en", "'s", "'"}


def test_restore_does_not_transfer_a_suffix_outside_the_closed_set():
    # A trailing run of word characters that is NOT one of the closed-set suffixes
    # is sub-token containment, not an inflection — left untouched like "Weberei".
    session = _session_with({"Müller": "Weber"})

    provider_response = {
        "content": [{"type": "text", "text": "Müllerxyz report."}]
    }

    restored = restore_response(provider_response, session)

    text = restored["content"][0]["text"]
    assert text == "Müllerxyz report."


def test_restore_does_not_mutate_the_input_response():
    mapping, session = _exchange()
    anna_surrogate = mapping.surrogate_for("Anna Schmidt")
    provider_response = {
        "content": [{"type": "text", "text": f"{anna_surrogate} done."}]
    }

    restore_response(provider_response, session)

    assert provider_response["content"][0]["text"] == f"{anna_surrogate} done."
