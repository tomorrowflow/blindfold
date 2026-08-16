"""ADR-0051 amendment (issue #303 -> #308): free-text prose nested inside a tool
schema -- ``input_schema.properties.*.description`` (Messages) / the
``parameters`` equivalent (Chat Completions) -- is gate-checked by ``leak_gate``
(``walk_string_leaves`` walks every string leaf, property descriptions included)
but was never rewritten: :func:`_blindfold_tool_descriptions` only touched
``container["description"]``, the tool's own top-level description, never
anything nested inside ``input_schema``/``parameters``.

This widens the existing deterministic tool pass (no L3, ADR-0023 section 3) to
every free-text ``description`` string reachable inside the schema, while
leaving every JSON-Schema structural token -- property keys, ``type``,
``required``, ``enum`` values -- byte-identical. Per the amendment's rule:
prose the blinder *could* rewrite joins the blinder; structure stays out of its
reach entirely (untouched by this issue).

Leak-audit clauses:
- A: a registered Term inside a nested schema description never reaches the stub
  upstream unsurrogated.
- E-stable: the schema-description surrogate equals the message-text surrogate
  for the same Term (restore coherence, same `SurrogateMapping`).
- F: no L3 adjudication ever runs over tool schema prose, nested or not --
  asserted as zero adjudicator calls.
- B: an applied surrogate is `session.record`ed, so restore is closed-world and
  `resolution_gate` reports nothing unresolved.
N/A this slice: C/D/G -- no closed-world-edge-case, mint-stability, or
store-schema change; ADR-0051 stage 1's own pass (`_apply_provisional_pairs`) is
reused unmodified.
"""

from __future__ import annotations

from blindfold.engine import (
    blindfold_chat_completions_payload,
    blindfold_payload,
    leak_gate,
    resolution_gate,
    restore_response,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


def _mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([("Acme Corp", "Bramblewick Ltd")])


class _ConfirmAsana:
    """Confirms only the candidate token "Asana" as an organization."""

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text != "Asana":
            return L3Adjudication(is_entity=False)
        return L3Adjudication(is_entity=True, entity_type="organization")


def test_registered_term_in_property_description_is_blindfolded_with_same_surrogate_as_message_text():
    mapping = _mapping()
    payload = {
        "model": "claude-3-5-sonnet",
        "tools": [
            {
                "name": "lookup_customer",
                "description": "Looks up a customer record.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {
                            "type": "string",
                            "description": "The customer ID, e.g. for Acme Corp.",
                        }
                    },
                },
            }
        ],
        "messages": [{"role": "user", "content": "Please check on Acme Corp for me."}],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    prop_description = blinded["tools"][0]["input_schema"]["properties"]["customer_id"][
        "description"
    ]
    message_text = blinded["messages"][0]["content"]
    surrogate = mapping.surrogate_for("Acme Corp")

    assert "Acme Corp" not in prop_description
    assert surrogate in prop_description
    assert surrogate in message_text


def test_nested_items_description_and_defs_description_are_both_blindfolded():
    mapping = _mapping()
    payload = {
        "model": "claude-3-5-sonnet",
        "tools": [
            {
                "name": "bulk_lookup",
                "description": "Looks up customer records.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customers": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "description": "A customer name, e.g. Acme Corp.",
                            },
                        },
                        "account": {"$ref": "#/$defs/account"},
                    },
                    "$defs": {
                        "account": {
                            "type": "object",
                            "description": "An account tied to Acme Corp.",
                        }
                    },
                },
            }
        ],
        "messages": [{"role": "user", "content": "Please check on Acme Corp for me."}],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    schema = blinded["tools"][0]["input_schema"]
    items_description = schema["properties"]["customers"]["items"]["description"]
    defs_description = schema["$defs"]["account"]["description"]
    surrogate = mapping.surrogate_for("Acme Corp")

    assert "Acme Corp" not in items_description
    assert surrogate in items_description
    assert "Acme Corp" not in defs_description
    assert surrogate in defs_description


def test_structural_tokens_stay_byte_identical_including_a_property_key_equal_to_a_term():
    mapping = _mapping()
    payload = {
        "model": "claude-3-5-sonnet",
        "tools": [
            {
                "name": "lookup_customer",
                "description": "Looks up a customer record.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "Acme Corp": {
                            "type": "string",
                            "enum": ["Acme Corp", "other"],
                            "description": "Mentions Acme Corp again in prose.",
                        }
                    },
                    "required": ["Acme Corp"],
                },
            }
        ],
        "messages": [{"role": "user", "content": "Please check on Acme Corp for me."}],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    schema = blinded["tools"][0]["input_schema"]
    assert schema["type"] == "object"
    assert list(schema["properties"].keys()) == ["Acme Corp"]
    assert schema["properties"]["Acme Corp"]["type"] == "string"
    assert schema["properties"]["Acme Corp"]["enum"] == ["Acme Corp", "other"]
    assert schema["required"] == ["Acme Corp"]

    surrogate = mapping.surrogate_for("Acme Corp")
    description = schema["properties"]["Acme Corp"]["description"]
    assert "Acme Corp" not in description
    assert surrogate in description


class _CountingAlwaysConfirm:
    """Would confirm any candidate as a person -- proves whether L3 ran at all."""

    def __init__(self) -> None:
        self.calls = 0

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls += 1
        return L3Adjudication(is_entity=True, entity_type="person")


def test_no_l3_adjudicator_call_over_nested_schema_prose():
    mapping = SurrogateMapping()
    adjudicator = _CountingAlwaysConfirm()
    detector = L3Detector(adjudicator)
    payload = {
        "model": "claude-3-5-sonnet",
        "tools": [
            {
                "name": "lookup_customer",
                "description": "Looks up a customer record.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {
                            "type": "string",
                            "description": "Contact Zolfgang Pemberton for details.",
                        }
                    },
                },
            }
        ],
        "messages": [{"role": "user", "content": "checking in now."}],
    }

    blindfold_payload(payload, mapping, detector)

    assert adjudicator.calls == 0


def test_already_minted_provisional_pair_is_applied_to_nested_schema_prose():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Please help me connect Asana to my workspace."}
        ],
        "tools": [
            {
                "name": "lookup",
                "description": "Looks up integrations.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "integration": {
                            "type": "string",
                            "description": "The `Asana` integration ID.",
                        }
                    },
                },
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Asana"

    prop_description = blinded["tools"][0]["input_schema"]["properties"]["integration"][
        "description"
    ]
    assert "Asana" not in prop_description
    assert item.provisional_surrogate in prop_description


def test_applied_surrogate_in_schema_prose_is_restored_and_resolution_gate_stays_clean():
    mapping = _mapping()
    payload = {
        "model": "claude-3-5-sonnet",
        "tools": [
            {
                "name": "lookup_customer",
                "description": "Looks up a customer record.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {
                            "type": "string",
                            "description": "The customer ID, e.g. for Acme Corp.",
                        }
                    },
                },
            }
        ],
        "messages": [{"role": "user", "content": "Please check on Acme Corp for me."}],
    }

    blinded, session = blindfold_payload(payload, mapping)
    surrogate = mapping.surrogate_for("Acme Corp")

    leak_gate(blinded, mapping, ReviewInbox())

    response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": f"Sure, I'll use {surrogate} for this lookup."}
        ],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }

    restored = restore_response(response, session)

    assert restored["content"][0]["text"] == "Sure, I'll use Acme Corp for this lookup."
    resolution_gate(restored, session)


def test_chat_completions_parameters_property_description_also_gets_blindfolded():
    mapping = _mapping()
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Please check on Acme Corp for me."}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lookup_customer",
                    "description": "Looks up a customer record.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "customer_id": {
                                "type": "string",
                                "description": "The customer ID, e.g. for Acme Corp.",
                            }
                        },
                    },
                },
            }
        ],
    }

    blinded, _session = blindfold_chat_completions_payload(payload, mapping)

    prop_description = blinded["tools"][0]["function"]["parameters"]["properties"][
        "customer_id"
    ]["description"]
    surrogate = mapping.surrogate_for("Acme Corp")

    assert "Acme Corp" not in prop_description
    assert surrogate in prop_description
