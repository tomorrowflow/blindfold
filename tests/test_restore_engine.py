"""Blindfold-engine seam: restore is closed-world (ADR-0006).

Covers leak-audit clause B (restore returns real values) and clause C (closed-world:
only surrogates injected this exchange are reversed).
"""

from blindfold.engine import blindfold_payload, restore_response
from blindfold.surrogates import SurrogateMapping


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


def test_restore_does_not_mutate_the_input_response():
    mapping, session = _exchange()
    anna_surrogate = mapping.surrogate_for("Anna Schmidt")
    provider_response = {
        "content": [{"type": "text", "text": f"{anna_surrogate} done."}]
    }

    restore_response(provider_response, session)

    assert provider_response["content"][0]["text"] == f"{anna_surrogate} done."
