"""ADR-0051 stage 2 (issue #300): #299 fixed the *total* gap in tool descriptions
(ADR-0023 §3 forbids L3 there, so nothing could ever re-blind a provisional value in
tool prose). The underlying asymmetry is general, not tool-description-specific: the
deterministic pass matches ``mapping.entities()`` (the entity graph, grows only on
confirm) while ``leak_gate`` checks ``mapping.real_values() | inbox.list()``. In
ordinary message text, a **provisional** entity's real value was re-blindfolded in a
later hop only if L3 happened to confirm it *again* there -- when it didn't, the
result was the same permanent deadlock #299 fixed for tool descriptions, just on a
request with no tool array to point at.

Fix: extend the same deterministic substitution (:func:`engine._apply_provisional_pairs`,
#299) into :func:`engine._blindfold_text` itself, so every message hop (system, user,
assistant, tool_result text, tool_use JSON args) gets it too -- strictly after the
entity-graph (L2) pass, strictly before L1/L3, on the same terms #299 established.

Leak-audit clauses:
- A: the outbound payload carries zero occurrences of a provisional real value in ANY
  message hop, once minted anywhere earlier -- even with L3 disabled/dismissing, i.e.
  protection no longer depends on re-confirmation (the core acceptance criterion).
- F: ``leak_gate`` (unmodified) no longer fires on a message hop that would otherwise
  have deadlocked the request.
- B: an applied provisional surrogate restores to the real value; ``resolution_gate``
  reports nothing unresolved.
N/A this slice: C/D/G -- no restore-closed-world-edge-case/store-schema change; E
(stable) reproven only insofar as the existing reuse-not-remint behavior (#299) is
exercised again over a new call site, not reimplemented.
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
    """Confirms only the candidate token "Asana" as an organization."""

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text != "Asana":
            return L3Adjudication(is_entity=False)
        return L3Adjudication(is_entity=True, entity_type="organization")


class _AlwaysDismiss:
    """Dismisses every candidate -- simulates L3 never re-confirming."""

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=False)


def _mint_payload() -> dict:
    return {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Please help me connect Asana to my workspace."}
        ],
    }


def test_a_provisional_pair_minted_in_an_earlier_request_is_blindfolded_in_a_later_requests_message_hop_with_l3_disabled():
    # Core acceptance criterion: protection no longer depends on re-confirmation.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())

    blindfold_payload(_mint_payload(), mapping, detector, inbox)
    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Asana"

    later_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Can you also loop in Asana on this thread?"}
        ],
    }

    # L3 disabled entirely for the later request.
    blinded, _session = blindfold_payload(later_payload, mapping, None, inbox)

    later_text = blinded["messages"][0]["content"]
    assert "Asana" not in later_text
    assert item.provisional_surrogate in later_text


def test_a_provisional_pair_minted_in_an_earlier_request_is_blindfolded_when_l3_is_stubbed_to_dismiss_it():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())

    blindfold_payload(_mint_payload(), mapping, detector, inbox)
    item = inbox.list()[0]

    later_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Can you also loop in Asana on this thread?"}
        ],
    }

    # L3 wired but stubbed to dismiss -- re-confirmation does NOT happen here.
    dismissive_detector = L3Detector(_AlwaysDismiss())
    blinded, _session = blindfold_payload(later_payload, mapping, dismissive_detector, inbox)

    later_text = blinded["messages"][0]["content"]
    assert "Asana" not in later_text
    assert item.provisional_surrogate in later_text


def test_run6_shaped_regression_leak_gate_does_not_raise_on_the_later_message_hop():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())

    blindfold_payload(_mint_payload(), mapping, detector, inbox)

    later_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Can you also loop in Asana on this thread?"}
        ],
    }
    blinded, _session = blindfold_payload(later_payload, mapping, None, inbox)

    leak_gate(blinded, mapping, inbox)


def test_the_same_value_in_a_later_hop_of_the_same_request_reuses_the_existing_provisional_surrogate_no_second_mint():
    # Acceptance criterion: "The same value in the same request maps to the same
    # existing provisional_surrogate; no second surrogate for one referent."
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Please help me connect Asana to my workspace."},
            {"role": "assistant", "content": "Sure, connecting now."},
            {"role": "user", "content": "Also loop in Asana on the follow-up thread."},
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    first_hop_text = blinded["messages"][0]["content"]
    second_user_hop_text = blinded["messages"][2]["content"]
    assert "Asana" not in first_hop_text
    assert "Asana" not in second_user_hop_text
    assert item.provisional_surrogate in first_hop_text
    assert item.provisional_surrogate in second_user_hop_text


def test_the_message_hop_substitution_set_is_derived_from_the_same_shared_function_leak_gate_uses(
    monkeypatch,
):
    # Acceptance criterion 3 (ADR-0051's invariant): "the blinder's applied set equals
    # leak_gate's checked set, from shared code -- a test fails if one side is widened
    # without the other." Pinned exactly like #299's own tool-description test, just
    # over a message hop this time -- monkeypatching the one shared derivation
    # (engine._provisional_known_value_set) must widen both sites at once.
    def _widened(item):
        return frozenset({item.real, *item.variations, "ExtraAlias"})

    monkeypatch.setattr(engine, "_provisional_known_value_set", _widened)

    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert("Asana", context="...connect Asana...", entity_type="organization")
    item = inbox.list()[0]

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Ping ExtraAlias about the rollout."}],
    }

    blinded, _session = blindfold_payload(payload, mapping, None, inbox)
    text = blinded["messages"][0]["content"]
    assert "ExtraAlias" not in text
    assert item.provisional_surrogate in text

    with pytest.raises(LeakError):
        leak_gate(
            {"messages": [{"role": "user", "content": "ExtraAlias called."}]},
            mapping,
            inbox,
        )


def test_registered_term_wins_over_a_provisional_pair_for_the_same_real_value_in_a_message_hop():
    # Acceptance criterion: entity graph wins over a provisional pair -- achieved by
    # ordering (L2 entity-graph pass runs first and removes the real text, so the
    # provisional scan that follows in the same hop has nothing left to match).
    mapping = SurrogateMapping.from_pairs([("Acme Corp", "Bramblewick Ltd")])
    inbox = ReviewInbox()
    inbox.upsert("Acme Corp", context="...signed with Acme Corp...", entity_type="organization")
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Please contact Acme Corp for details."}],
    }

    blinded, _session = blindfold_payload(payload, mapping, None, inbox)

    text = blinded["messages"][0]["content"]
    item = inbox.list()[0]
    assert "Acme Corp" not in text
    assert "Bramblewick Ltd" in text
    assert item.provisional_surrogate not in text


def test_a_rejected_inbox_row_stops_being_applied_in_a_later_message_hop():
    # Acceptance criterion: "A rejected inbox row stops being applied -- reject (#294)
    # remains the recovery path and actually recovers."
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())

    blindfold_payload(_mint_payload(), mapping, detector, inbox)
    item = inbox.list()[0]
    inbox.remove(item.id)
    assert inbox.list() == []

    later_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Can you also loop in Asana on this thread?"}
        ],
    }
    blinded, _session = blindfold_payload(later_payload, mapping, None, inbox)

    text = blinded["messages"][0]["content"]
    assert text == "Can you also loop in Asana on this thread?"
    assert item.provisional_surrogate not in text


def test_applied_provisional_surrogate_in_a_message_hop_is_restored_and_resolution_gate_stays_clean():
    # Acceptance criterion: "Restore round-trips every applied provisional surrogate;
    # resolution_gate clean."
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())

    blindfold_payload(_mint_payload(), mapping, detector, inbox)
    item = inbox.list()[0]

    later_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Can you also loop in Asana on this thread?"}
        ],
    }
    _blinded, session = blindfold_payload(later_payload, mapping, None, inbox)

    response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": f"Sure, I'll loop in {item.provisional_surrogate} now.",
            }
        ],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }

    restored = restore_response(response, session)

    restored_text = restored["content"][0]["text"]
    assert restored_text == "Sure, I'll loop in Asana now."
    resolution_gate(restored, session)


def test_chat_completions_message_hop_also_gets_the_provisional_pair_applied():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())
    mint_payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "Please help me connect Asana to my workspace."}
        ],
    }

    blindfold_chat_completions_payload(mint_payload, mapping, detector, inbox)
    item = inbox.list()[0]

    later_payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "Can you also loop in Asana on this thread?"}
        ],
    }
    blinded, _session = blindfold_chat_completions_payload(
        later_payload, mapping, None, inbox
    )

    text = blinded["messages"][0]["content"]
    assert "Asana" not in text
    assert item.provisional_surrogate in text


def test_a_provisional_pair_is_applied_inside_a_tool_use_blocks_json_args_and_a_tool_results_text():
    # Issue #11: tool-call JSON args and tool-result text are hops too (ADR-0002).
    # "Every hop of every payload" (this issue's own phrasing) must include them, not
    # just plain text blocks.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())

    blindfold_payload(_mint_payload(), mapping, detector, inbox)
    item = inbox.list()[0]

    later_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "lookup",
                        "input": {"query": "Asana workspace settings"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [{"type": "text", "text": "Asana has 3 open tasks."}],
                    }
                ],
            },
        ],
    }

    blinded, _session = blindfold_payload(later_payload, mapping, None, inbox)

    tool_use_args = blinded["messages"][0]["content"][0]["input"]
    tool_result_text = blinded["messages"][1]["content"][0]["content"][0]["text"]
    assert "Asana" not in tool_use_args["query"]
    assert item.provisional_surrogate in tool_use_args["query"]
    assert "Asana" not in tool_result_text
    assert item.provisional_surrogate in tool_result_text


def test_a_sub_word_occurrence_of_the_provisional_real_value_is_untouched_in_a_message_hop():
    # Acceptance criterion: "sub-word occurrences untouched" -- the same word-boundary
    # matcher (_real_value_pattern) leak_gate and #299's tool-description pass already
    # use, reused unchanged here.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())

    blindfold_payload(_mint_payload(), mapping, detector, inbox)
    item = inbox.list()[0]

    later_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Check out Asananet, an unrelated product."}
        ],
    }
    blinded, _session = blindfold_payload(later_payload, mapping, None, inbox)

    text = blinded["messages"][0]["content"]
    assert text == "Check out Asananet, an unrelated product."
    assert item.provisional_surrogate not in text


def test_the_provisional_substitution_runs_before_l3_so_the_hop_never_re_mints_a_second_row():
    # Acceptance criterion / #292's self-poisoning guard: applying the provisional
    # pair strictly before L3 means the occurrence is already a surrogate by the time
    # the adjudicator sees this hop -- proven here with a detector that WOULD confirm
    # "Asana" again if it ever saw the bare real text, showing L3 never gets the
    # chance because the substitution already ran.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAsana())

    blindfold_payload(_mint_payload(), mapping, detector, inbox)
    assert len(inbox.list()) == 1
    item = inbox.list()[0]

    later_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Can you also loop in Asana on this thread?"}
        ],
    }
    # Same would-confirm detector wired for the later hop too -- if the provisional
    # substitution did not run before L3, this detector would confirm "Asana" as a
    # fresh novel candidate and mint a second inbox row.
    blinded, _session = blindfold_payload(later_payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    assert inbox.list()[0].id == item.id
    text = blinded["messages"][0]["content"]
    assert "Asana" not in text
    assert item.provisional_surrogate in text


def test_a_full_legal_form_occurrence_is_not_corrupted_by_its_own_bare_form_variation():
    # Regression: the known-value set for one referent can contain a value that is a
    # strict prefix of another (issue #289/#296's legal-form-suffix variation --
    # "Kestrel Dynamics" is a variation of "Kestrel Dynamics GmbH"). Substituting the
    # shorter one first would consume only its own span and strand the legal-form
    # suffix (" GmbH") glued onto the surrogate -- corrupting the text instead of
    # replacing the whole occurrence. Longest-first ordering (mirroring
    # _apply_restore_pass) avoids it.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert(
        "Kestrel Dynamics GmbH",
        context="...signed with Kestrel Dynamics GmbH...",
        entity_type="organization",
    )
    item = inbox.list()[0]
    assert item.variations == frozenset({"Kestrel Dynamics GmbH", "Kestrel Dynamics"})

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": "Please follow up with Kestrel Dynamics GmbH about the invoice.",
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, None, inbox)

    text = blinded["messages"][0]["content"]
    assert text == (
        f"Please follow up with {item.provisional_surrogate} about the invoice."
    )
    assert "Kestrel" not in text
