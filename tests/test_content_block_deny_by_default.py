"""Blinder deny-by-default over content-block string leaves (issue #323).

``_blindfold_block`` enumerated known content-block types (``text``, ``tool_result``,
``tool_use``) and returned every other block unchanged, while ``leak_gate`` walks
every string leaf of the whole payload exhaustively
(``_collect_text``/``walk_string_leaves``). A known entity inside an unhandled block
type (``thinking``, ``document``, ``search_result``, ``mcp_tool_use``, ...) therefore
reached the gate un-blinded and fail-closed as an unexplained 503; a *novel* entity
inside such a block never reached L3 at all and egressed silently -- the one outcome
the every-hop invariant (ADR-0002) exists to prevent.

This file pins the inversion: the blinder now walks every content-block string leaf
deny-by-default, with a small, explicit, justified non-hop exclusion set for
protocol-structural fields (the block-type discriminator, cross-reference ids, the
thinking block's provider-signed ``signature``).
"""

from blindfold.engine import (
    ExchangeSession,
    blindfold_payload,
    leak_gate,
    resolution_gate,
    restore_response,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


def _mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])


def _payload_with_entity_in_a_document_block():
    return {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "text",
                            "media_type": "text/plain",
                            "data": "irrelevant filler text",
                        },
                        "title": "Notes",
                        "context": "Prepared for Anna Schmidt's review.",
                    }
                ],
            }
        ],
    }


def test_a_known_entity_inside_a_document_block_is_blindfolded_and_leak_gate_stays_clean():
    mapping = _mapping()
    payload = _payload_with_entity_in_a_document_block()

    blinded, _session = blindfold_payload(payload, mapping)

    context_field = blinded["messages"][0]["content"][0]["context"]
    assert "Anna Schmidt" not in context_field
    assert mapping.surrogate_for("Anna Schmidt") in context_field

    # Leak-audit: the gate must stay clean -- pre-fix this block sailed through
    # un-blinded and leak_gate's own exhaustive walk fail-closed a 503 here.
    leak_gate(blinded, mapping)


def test_a_surrogate_inside_a_response_thinking_block_restores_and_the_signature_is_untouched():
    # AC1's second half ("... and restores"): a thinking block is realistic
    # assistant-echoed content -- it carries a provider-signed `signature` field
    # (issue #323's own named wrinkle) that must round-trip byte-identical while
    # the `thinking` prose itself gets the real value back.
    mapping = _mapping()
    session = ExchangeSession()
    surrogate = mapping.surrogate_for("Anna Schmidt")
    session.record(surrogate, "Anna Schmidt")

    response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "thinking",
                "thinking": f"I should mention {surrogate} in the summary.",
                "signature": "sig-opaque-blob",
            }
        ],
    }

    restored = restore_response(response, session)

    thinking_block = restored["content"][0]
    assert surrogate not in thinking_block["thinking"]
    assert "Anna Schmidt" in thinking_block["thinking"]
    # The signature is opaque, provider-signed ciphertext -- never a restore target.
    assert thinking_block["signature"] == "sig-opaque-blob"

    resolution_gate(restored, session)  # clause D: verify pass clean


class _ConfirmingAdjudicator:
    """Stub for Ollama/GLiNER: confirms exactly the whitelisted candidate texts,
    dismisses everything else. Records every candidate text it was asked to
    adjudicate (issue #323 AC2: "a novel-entity candidate inside such a block
    reaches L3 candidacy [...] via stubbed adjudicator")."""

    def __init__(self, confirm: set[str]) -> None:
        self.calls: list[str] = []
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        if candidate.text in self._confirm:
            return L3Adjudication(is_entity=True, entity_type="person")
        return L3Adjudication(is_entity=False)


def test_a_novel_entity_inside_a_search_result_block_reaches_l3_candidacy():
    # AC2: search_result is one of the issue's own named previously-unhandled
    # block types, and its nested `content` is itself a content-block list (a
    # text block), so this also exercises the generic fallback's recursion into a
    # nested block shape, not just a single flat string field. "Petra"/"Lindqvist"
    # are adjacent confirmed tokens, coalesced (#162) into one referent.
    mapping = SurrogateMapping.from_pairs([])
    petra = "Petra Lindqvist"
    adjudicator = _ConfirmingAdjudicator(confirm={"Petra", "Lindqvist"})
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()

    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "search_result",
                        "source": "https://example.test/doc",
                        "title": "Result",
                        "content": [
                            {"type": "text", "text": f"Contact {petra} for details."}
                        ],
                    }
                ],
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    # Both tokens reached the adjudicator -- L3 candidacy, not a silent skip.
    assert "Petra" in adjudicator.calls
    assert "Lindqvist" in adjudicator.calls

    minted = [item for item in inbox.list() if item.real == petra]
    assert len(minted) == 1

    nested_text = blinded["messages"][0]["content"][0]["content"][0]["text"]
    assert petra not in nested_text
    assert minted[0].provisional_surrogate in nested_text
    leak_gate(blinded, mapping, inbox)  # leak-audit: gate stays clean


def test_a_synthetic_never_before_seen_block_type_is_blinded_by_default_with_no_code_change():
    # AC3: coverage must hold for a payload shape that doesn't exist yet -- a
    # made-up block "type" this code was never written to recognize -- proving
    # the walk is deny-by-default (every string leaf is a candidate unless
    # explicitly excluded) rather than an enumeration that happens to be long
    # enough today.
    mapping = _mapping()
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "a_block_type_invented_for_this_test",
                        "id": "blk_1",
                        "some_future_field": "Ping Anna Schmidt about the release.",
                    }
                ],
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    block = blinded["messages"][0]["content"][0]
    # The invented structural fields (type/id) are untouched.
    assert block["type"] == "a_block_type_invented_for_this_test"
    assert block["id"] == "blk_1"
    # But the free-text field carrying the known entity is blindfolded.
    assert "Anna Schmidt" not in block["some_future_field"]
    assert mapping.surrogate_for("Anna Schmidt") in block["some_future_field"]
    leak_gate(blinded, mapping)


def test_mcp_tool_use_block_blindfolds_input_but_leaves_name_and_server_name_untouched():
    # mcp_tool_use is one of the issue's own named examples. Its "input" is the
    # same free-form JSON-call-args shape as plain tool_use (issue #11); "name"
    # and "server_name" are protocol identifiers, the same class as
    # tools[].name (ADR-0051/#307), never rewritten.
    mapping = _mapping()
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "mcp_tool_use",
                        "id": "mcptu_1",
                        "name": "search_contacts",
                        "server_name": "crm",
                        "input": {"query": "Anna Schmidt"},
                    }
                ],
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    block = blinded["messages"][0]["content"][0]
    assert block["name"] == "search_contacts"
    assert block["server_name"] == "crm"
    assert block["id"] == "mcptu_1"
    assert block["input"]["query"] == mapping.surrogate_for("Anna Schmidt")
    leak_gate(blinded, mapping)
