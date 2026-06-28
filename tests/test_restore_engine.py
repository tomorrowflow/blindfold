"""Blindfold-engine seam: restore is closed-world (ADR-0006).

Covers leak-audit clause B (restore returns real values) and clause C (closed-world:
only surrogates injected this exchange are reversed).
"""

from blindfold.engine import blindfold_payload, restore_response
from blindfold.surrogates import seeded_mapping


def _exchange():
    mapping = seeded_mapping()
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Summarize Stefan Wegner's review."}
        ],
    }
    _blinded, session = blindfold_payload(payload, mapping)
    return mapping, session


def test_restore_swaps_injected_surrogates_back_to_real_values_in_prose():
    mapping, session = _exchange()
    stefan_surrogate = mapping.surrogate_for("Stefan Wegner")

    provider_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": f"{stefan_surrogate} approved the change."}
        ],
    }

    restored = restore_response(provider_response, session)

    text = restored["content"][0]["text"]
    assert text == "Stefan Wegner approved the change."
    # No surrogate remains client-visible.
    assert stefan_surrogate not in text


def test_restore_is_closed_world_and_leaves_coincidental_lookalikes_untouched():
    mapping, session = _exchange()

    # "Tobias Lehmann" is the surrogate for "Markus Eberhardt" — a real entity NOT
    # injected in this exchange (only Stefan was). The provider emitting it on its own
    # is a coincidental lookalike and must NOT be restored to the real value.
    coincidental = mapping.surrogate_for("Markus Eberhardt")
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
    assert "Markus Eberhardt" not in text


def test_restore_does_not_mutate_the_input_response():
    mapping, session = _exchange()
    stefan_surrogate = mapping.surrogate_for("Stefan Wegner")
    provider_response = {
        "content": [{"type": "text", "text": f"{stefan_surrogate} done."}]
    }

    restore_response(provider_response, session)

    assert provider_response["content"][0]["text"] == f"{stefan_surrogate} done."
