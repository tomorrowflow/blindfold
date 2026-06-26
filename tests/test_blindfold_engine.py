"""Blindfold-engine seam (in-process): blindfold every hop before egress.

Per ADR-0002, every hop (system prompt, user turns, tool-result messages) is
blindfolded — not just the first prompt.
"""

from blindfold.engine import blindfold_payload
from blindfold.surrogates import seeded_mapping


def _anthropic_request_with_entities_in_every_hop():
    # Real entities appear in: the system prompt, a user-turn text block, and a
    # tool-result message's text — three distinct hops.
    return {
        "model": "claude-3-5-sonnet",
        "system": "You assist Anna Schmidt with code review.",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Ask Markus Wagner about the patch."}
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [
                            {"type": "text", "text": "Owner: Anna Schmidt (line 1)."}
                        ],
                    }
                ],
            },
        ],
    }


def test_blindfold_replaces_real_entities_in_every_hop_with_surrogates():
    mapping = seeded_mapping()
    payload = _anthropic_request_with_entities_in_every_hop()

    blinded, _session = blindfold_payload(payload, mapping)

    anna = "Anna Schmidt"
    markus = "Markus Wagner"
    anna_surrogate = mapping.surrogate_for(anna)
    markus_surrogate = mapping.surrogate_for(markus)

    system_text = blinded["system"]
    user_text = blinded["messages"][0]["content"][0]["text"]
    tool_result_text = blinded["messages"][1]["content"][0]["content"][0]["text"]

    # No real entity value survives in any hop.
    for hop_text in (system_text, user_text, tool_result_text):
        assert anna not in hop_text
        assert markus not in hop_text

    # The surrogates are present where the entities were.
    assert anna_surrogate in system_text
    assert markus_surrogate in user_text
    assert anna_surrogate in tool_result_text


def test_blindfold_leaves_non_entity_content_byte_identical():
    mapping = seeded_mapping()
    payload = _anthropic_request_with_entities_in_every_hop()

    blinded, _session = blindfold_payload(payload, mapping)

    # Untouched scalar fields are preserved exactly.
    assert blinded["model"] == "claude-3-5-sonnet"
    assert blinded["messages"][1]["content"][0]["tool_use_id"] == "toolu_1"
    # Surrounding prose around the entity is preserved.
    assert blinded["messages"][0]["content"][0]["text"].startswith("Ask ")
    assert blinded["messages"][0]["content"][0]["text"].endswith(" about the patch.")


def test_blindfold_does_not_mutate_the_input_payload():
    mapping = seeded_mapping()
    payload = _anthropic_request_with_entities_in_every_hop()

    blindfold_payload(payload, mapping)

    assert payload["system"] == "You assist Anna Schmidt with code review."
