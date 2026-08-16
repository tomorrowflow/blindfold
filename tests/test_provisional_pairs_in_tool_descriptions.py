"""ADR-0051 stage 1 (issue #299, the #74 run-7 unblocker): a **provisional** entity
minted from an earlier hop in the same request is invisible to
``_blindfold_tool_descriptions`` -- it runs deterministic-only (ADR-0023 §3), consulting
``mapping`` (the entity graph) alone. ``mapping`` only grows on confirm, so a provisional
real value occurring as a whole word in a *later* tool description was never rewritten,
while ``leak_gate`` checks the review inbox's reals across the whole payload, tool
descriptions included. Run 6: 11 consecutive fail-closed exchanges on
``Provisional Surrogate 14`` = ``"Asana"``, minted from a system hop, then present in
an MCP tool's description.

Fix: apply each already-minted provisional pair as a deterministic whole-word
substitution over ``{item.real, *item.variations}`` (the identical set ``leak_gate``
checks, via the same ``engine._real_value_pattern`` matcher) -- reusing the item's
existing ``provisional_surrogate``, never minting a second one.

Leak-audit clauses:
- A: the outbound payload (tool descriptions included) carries zero occurrences of a
  provisional real value once it has been minted anywhere earlier in the same request.
- F: ``leak_gate`` (unmodified) no longer fires on a tool description that would
  otherwise have deadlocked the request.
N/A this slice: B/C/D/E/G -- no restore/mint-stability/store-schema change; the
restore-fidelity behavior of an applied provisional surrogate is exactly #295/#296's own
(session.record always pairs the surrogate with item.real), reproven here only insofar
as the existing session.record call is reused, not reimplemented.
"""

from __future__ import annotations

import pytest

from blindfold import engine
from blindfold.engine import (
    LeakError,
    blindfold_chat_completions_payload,
    blindfold_payload,
    leak_gate,
    resolution_gate,
    restore_response,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _ConfirmAsana:
    """Confirms only the candidate token "Asana" as an organization -- mirrors the
    run-6 live capture (Provisional Surrogate 14 = "Asana", minted from a system/
    message hop's own safety-rules-shaped prose).
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text != "Asana":
            return L3Adjudication(is_entity=False)
        return L3Adjudication(is_entity=True, entity_type="organization")


def _run6_payload() -> dict:
    return {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Please help me connect Asana to my workspace."}
        ],
        "tools": [
            {
                "name": "mcp__claude_ai_Asana__search",
                "description": "The `claude.ai Asana` MCP server lets you search tasks.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    }


def test_a_provisional_pair_minted_earlier_in_the_request_is_applied_to_a_later_tool_description():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())

    blinded, _session = blindfold_payload(_run6_payload(), mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Asana"

    description = blinded["tools"][0]["description"]
    assert "Asana" not in description
    assert item.provisional_surrogate in description

    # ADR-0023 §3: the declared tool name itself is never touched, and the "Asana"
    # component glued inside it (an underscore is a word character) was never a
    # whole-word occurrence to begin with.
    assert blinded["tools"][0]["name"] == "mcp__claude_ai_Asana__search"


def test_run6_regression_leak_gate_does_not_raise_once_the_provisional_pair_is_applied():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())

    blinded, _session = blindfold_payload(_run6_payload(), mapping, detector, inbox)

    # Previously: leak_gate raised LeakError on every subsequent exchange once
    # "Asana" was minted, because the tool description still carried it in plaintext.
    leak_gate(blinded, mapping, inbox)


def test_chat_completions_tool_description_also_gets_the_provisional_pair_applied():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "Please help me connect Asana to my workspace."}
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "asana_search",
                    "description": "The claude.ai Asana MCP server lets you search tasks.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    }

    blinded, _session = blindfold_chat_completions_payload(
        payload, mapping, detector, inbox
    )

    item = inbox.list()[0]
    description = blinded["tools"][0]["function"]["description"]
    assert "Asana" not in description
    assert item.provisional_surrogate in description


def test_registered_term_wins_over_a_provisional_pair_for_the_same_real_value():
    # Acceptance criterion: "A registered Term equal to a provisional real still
    # resolves via the entity graph / mapping surrogate, not the provisional one."
    # Achieved by ordering: the entity-graph pass runs first and removes the real
    # text, so the provisional scan that follows has nothing left to match.
    mapping = SurrogateMapping.from_pairs([("Acme Corp", "Bramblewick Ltd")])
    inbox = ReviewInbox()
    inbox.upsert("Acme Corp", context="...signed with Acme Corp...", entity_type="organization")
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "checking in now."}],
        "tools": [
            {
                "name": "lookup",
                "description": "Contact Acme Corp for details.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, None, inbox)

    description = blinded["tools"][0]["description"]
    item = inbox.list()[0]
    assert "Acme Corp" not in description
    assert "Bramblewick Ltd" in description
    assert item.provisional_surrogate not in description


class _AlwaysConfirmPerson:
    """Would confirm any candidate as a person entity, if ever asked."""

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=True, entity_type="person")


def test_provisional_substitution_runs_no_l3_and_mints_no_new_inbox_row():
    # Acceptance criterion: deterministic only -- no L3 adjudication over tool
    # descriptions, and no review-inbox row is created by this pass. An
    # always-confirm adjudicator proves it: if L3 ever ran over the description,
    # "Zolfgang Pemberton" would mint a second inbox row.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert("Asana", context="...connect Asana...", entity_type="organization")
    detector = L3Detector(_AlwaysConfirmPerson())
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "checking in now."}],
        "tools": [
            {
                "name": "escalate",
                "description": "Escalate to Asana and notify Zolfgang Pemberton.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    item = inbox.list()[0]
    description = blinded["tools"][0]["description"]
    assert description == (
        f"Escalate to {item.provisional_surrogate} and notify Zolfgang Pemberton."
    )
    assert len(inbox.list()) == 1


def test_applied_provisional_surrogate_is_restored_and_resolution_gate_stays_clean():
    # Acceptance criterion: applied provisional surrogates are recorded in the
    # session, so restore reverses them and resolution_gate reports nothing
    # unresolved -- exercised end-to-end via the same session the blindfold pass
    # produced, mirroring how the response path actually consumes it.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())

    blinded, session = blindfold_payload(_run6_payload(), mapping, detector, inbox)
    item = inbox.list()[0]

    response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": f"Sure, I'll use the {item.provisional_surrogate} MCP server.",
            }
        ],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }

    restored = restore_response(response, session)

    restored_text = restored["content"][0]["text"]
    assert restored_text == "Sure, I'll use the Asana MCP server."
    resolution_gate(restored, session)


def test_the_substitution_set_is_derived_from_the_same_shared_function_leak_gate_uses(
    monkeypatch,
):
    # Acceptance criterion 3: "the substitution set is {item.real, *item.variations}
    # matched by _real_value_pattern, i.e. the same set and matcher leak_gate checks,
    # derived from shared code. A test fails if one side is widened without the
    # other." Pinned by monkeypatching the one shared derivation
    # (engine._provisional_known_value_set) and observing that BOTH the tool-
    # description substitution and leak_gate immediately pick up the widened set --
    # proof neither call site re-derives its own copy.
    def _widened(item):
        return frozenset({item.real, *item.variations, "ExtraAlias"})

    monkeypatch.setattr(engine, "_provisional_known_value_set", _widened)

    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert("Asana", context="...connect Asana...", entity_type="organization")
    item = inbox.list()[0]

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "checking in now."}],
        "tools": [
            {
                "name": "lookup",
                "description": "Ping ExtraAlias about the rollout.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, None, inbox)
    description = blinded["tools"][0]["description"]
    assert "ExtraAlias" not in description
    assert item.provisional_surrogate in description

    with pytest.raises(LeakError):
        leak_gate(
            {"messages": [{"role": "user", "content": "ExtraAlias called."}]},
            mapping,
            inbox,
        )
