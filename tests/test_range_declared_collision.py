"""Issue #416 (ADR-0051's #406 amendment): ``leak_gate`` becomes leaf-aware and
reads the session's blinder-written range record. A gate match that falls WHOLLY
inside a range the blinder itself spliced into that same leaf is a declared
collision, not a leak -- those characters are the blinder's own output, never a
real value that escaped it.

The join is by TRAVERSAL, never by index or content (the ADR's own words): a leaf
found by the leak gate's exhaustive walk cannot be matched to a recorded range by
position, because the two walks visit leaves in different orders (the ADR's own
measured four-leaf-vs-eleven-leaf table). ``leak_gate`` instead re-walks only the
regions the blinder itself visits (``system``, ``messages[*].content``,
``tools[].description`` + nested schema ``description``) in the blinder's own
traversal order, reusing ``ExchangeSession._begin_leaf``'s positional contract to
pair each leaf with the accumulator the original blind pass created for it.

Leak-audit clauses:
- A/D: the excused characters are the blinder's own output; no real value the
  blinder could have rewritten reaches the provider. Proven end to end (through
  ``leak_gate`` and ``restore_response``) below.
- F: fail-closed is narrowed deliberately and exactly -- a straddling or
  outside-range match, or a leaf that cannot be positively paired to a recorded
  range, still raises.
- B/C: restore semantics are unaffected -- the excused characters restore with
  their containing surrogate, proven below via ``resolution_gate`` staying clean.

No real value is quoted -- every referent below is a fictional test fixture, the
same convention the rest of this suite already uses (not a live-verify finding,
so ``docs/agents/domain.md``'s role-placeholder rule does not itself apply, but
no fixture here is drawn from ``tests/live-verify/74-engagement-brief.md`` either).
"""

from __future__ import annotations

import re

import pytest

from blindfold.engine import (
    ExchangeSession,
    LeakError,
    _collect_text,
    blindfold_payload,
    leak_gate,
    resolution_gate,
    restore_response,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _ConfirmAnyNameShapedTokenAdjudicator:
    """Confirms EVERY candidate span as an entity -- mirrors the same stub used
    throughout ``tests/test_surrogate_component_remint_guard.py``: a hand-scripted
    confirm-list can't reproduce this class of collision, since a real model has
    no such list.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=True)


class _ConfirmSet:
    """Confirms exactly the candidate texts named in ``confirm``."""

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=candidate.text in self._confirm)


def test_a_confirmed_real_value_wholly_inside_a_just_injected_surrogate_is_a_declared_collision():
    # "Confirmed reals" set (mapping.real_values()): a confirmed entity's own
    # bare real value ("Systems") is never typed by the client -- it only
    # exists in the outbound text because it is a whole word inside a
    # DIFFERENT, unrelated confirmed entity's own just-spliced surrogate.
    # "Systems" is registered as its own confirmed entity ONLY AFTER
    # blindfold_payload has already finished, never present in the mapping
    # the blind pass itself consulted. This isolates the GATE's own logic:
    # ``blindfold_payload``'s cross-hop closing sweep (#386) re-runs ordinary
    # L2 confirmed-whole-value detection over each hop's already-blinded text
    # unconditionally, with NO self-poisoning guard of its own (unlike the
    # confirmed-COMPONENT pass, #394) -- a real "Systems" known to the blind
    # pass AT BLIND TIME would simply get re-detected and re-spliced by that
    # sweep, self-healing before leak_gate ever ran, and this test would
    # exercise that unrelated (and out of scope here) blinder behaviour
    # instead of the gate's own range-scoped exclusion. ``leak_gate`` reads
    # ``mapping.real_values()`` fresh on every call -- exactly like a
    # provisional entity becoming known between blinding and gate-check --
    # so a real value the blind pass never saw is still correctly checked.
    mapping = SurrogateMapping.from_pairs([("Org A", "Aurora Systems")])
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Org A signed the agreement."}],
    }

    blinded, session = blindfold_payload(payload, mapping, None, None)
    text = blinded["messages"][0]["content"]
    assert text == "Aurora Systems signed the agreement."

    mapping.seed("Systems", "Ridge Holdings")
    collisions = leak_gate(blinded, mapping, None, session)
    assert len(collisions) == 1
    assert collisions[0].startswith("declared collision:")
    assert "range the blinder itself wrote" in collisions[0]
    # Distinct in shape from the FIELD-scoped declared-collision reason
    # (ADR-0051's #303 amendment), so the two are countable apart.
    assert "forbidden to rewrite" not in collisions[0]


def test_a_match_that_straddles_a_blinder_written_range_boundary_still_raises():
    # #415's AC 6 / this issue's own acceptance criterion: a match that is
    # only PARTLY the blinder's own output is a genuine miss and must still
    # block. "Systems signed" starts inside the injected "Aurora Systems"
    # splice and ends past it, in the client's own untouched text.
    #
    # As above, "Systems signed" is registered only AFTER blindfold_payload
    # has already produced ``blinded``/``session``, so the blinder's own
    # (unguarded, out-of-scope-here) cross-hop L2 rescan never gets a chance
    # to independently re-detect and re-splice it first.
    mapping = SurrogateMapping.from_pairs([("Org A", "Aurora Systems")])
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Org A signed the agreement."}],
    }

    blinded, session = blindfold_payload(payload, mapping, None, None)
    text = blinded["messages"][0]["content"]
    assert text == "Aurora Systems signed the agreement."

    mapping.seed("Systems signed", "SingleWordSurrogate")
    with pytest.raises(LeakError):
        leak_gate(blinded, mapping, None, session)


def test_a_confirmed_components_component_wholly_inside_a_blinder_written_range_is_a_declared_collision():
    # "Confirmed components" set (issue #394's _confirmed_pair_map): "Doe" is
    # the positionally-aligned bare component of the confirmed entity
    # "Jane Doe" -> "Alex Brenner". It reaches the outbound text only because
    # an UNRELATED confirmed entity's own surrogate happens to contain it.
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")
    mapping.seed("Org Colliding", "Northaven Doe")

    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Org Colliding filed the report."}],
    }

    blinded, session = blindfold_payload(payload, mapping, None, None)
    text = blinded["messages"][0]["content"]
    assert text == "Northaven Doe filed the report."

    collisions = leak_gate(blinded, mapping, None, session)
    assert len(collisions) == 1
    assert "range the blinder itself wrote" in collisions[0]
    assert "Doe" not in collisions[0]


def test_a_provisional_variation_wholly_inside_a_blinder_written_range_is_a_declared_collision():
    # "Provisional variations" set (issue #296's entity_variations, e.g. the
    # legal-form-suffix strip): the item's own bare variation "Kestrel
    # Dynamics" reaches the outbound text only because an UNRELATED confirmed
    # entity's surrogate happens to start with the same two words.
    mapping = SurrogateMapping.from_pairs([("Org X", "Kestrel Dynamics Holdings")])
    inbox = ReviewInbox()
    inbox.upsert(
        "Kestrel Dynamics GmbH",
        context="...signed with Kestrel Dynamics GmbH last week...",
        entity_type="organization",
    )

    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Org X handled the deal."}],
    }

    blinded, session = blindfold_payload(payload, mapping, None, inbox)
    text = blinded["messages"][0]["content"]
    assert text == "Kestrel Dynamics Holdings handled the deal."

    collisions = leak_gate(blinded, mapping, inbox, session)
    assert len(collisions) == 1
    assert "range the blinder itself wrote" in collisions[0]
    assert "Kestrel" not in collisions[0]


def test_a_provisional_components_component_wholly_inside_a_blinder_written_range_is_a_declared_collision():
    # "Provisional components" set (issue #306's _provisional_component_map):
    # once "Priya Nadkarni" is minted with a two-word provisional surrogate,
    # its positionally-aligned bare surname component ("Nadkarni") is checked
    # too. It reaches later outbound text only because an UNRELATED confirmed
    # entity's own surrogate happens to contain that exact word.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())
    mint_payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Priya Nadkarni sent the update."}],
    }
    blindfold_payload(mint_payload, mapping, detector, inbox)

    items = inbox.list()
    assert len(items) == 1
    item = items[0]
    assert item.real == "Priya Nadkarni"
    surrogate_words = item.provisional_surrogate.split()
    assert len(surrogate_words) == 2

    real_component = item.real.split()[1]  # "Nadkarni"
    mapping.seed("Org Colliding", f"Northaven {real_component}")

    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Org Colliding filed the report."}],
    }
    blinded, session = blindfold_payload(payload, mapping, None, inbox)
    text = blinded["messages"][0]["content"]
    assert text == f"Northaven {real_component} filed the report."

    collisions = leak_gate(blinded, mapping, inbox, session)
    assert len(collisions) == 1
    assert "range the blinder itself wrote" in collisions[0]
    assert real_component not in collisions[0]


def test_the_gate_excuses_exactly_the_occurrences_the_blinder_declined_not_more_not_fewer():
    # Acceptance criterion: "Blinder and gate agree" -- not just "the gate
    # excuses", the symmetry is the invariant. Two referents each collide
    # with a DIFFERENT, unrelated confirmed entity's just-injected surrogate
    # (each excused); a third, genuinely novel referent shares no span with
    # any surrogate at all and must be neither a leak nor a collision.
    mapping = SurrogateMapping.from_pairs(
        [
            ("Employer Org", "Kurt Steinmetz"),
            ("Other Org", "Petra Vogt"),
        ]
    )
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmSet({"Kurt", "Vogt", "Marla"}))
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Employer Org and Other Org partnered. Please loop in "
                    "Kurt and Vogt and Marla about the schedule."
                ),
            }
        ],
    }

    blinded, session = blindfold_payload(payload, mapping, detector, inbox)
    text = blinded["messages"][0]["content"]
    assert "Kurt Steinmetz" in text
    assert "Petra Vogt" in text

    reals = {item.real for item in inbox.list()}
    assert reals == {"Kurt", "Vogt", "Marla"}

    collisions = leak_gate(blinded, mapping, inbox, session)
    assert len(collisions) == 2
    joined = "\n".join(collisions)
    assert joined.count("range the blinder itself wrote") == 2
    assert "Kurt" not in joined
    assert "Vogt" not in joined
    assert "Marla" not in joined


def test_end_to_end_through_leak_gate_and_restore_response_resolution_gate_stays_clean():
    # Acceptance criterion: proven through leak_gate AND restore_response, not
    # the blindfold output alone -- the exchange completes, the client gets a
    # correct restore, and resolution_gate reports nothing unresolved.
    mapping = SurrogateMapping.from_pairs([("Employer Org", "Kurt Steinmetz")])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Please loop in Kurt about it. Employer Org already "
                    "signed off."
                ),
            }
        ],
    }

    blinded, session = blindfold_payload(payload, mapping, detector, inbox)
    outbound_text = blinded["messages"][0]["content"]

    collisions = leak_gate(blinded, mapping, inbox, session)
    assert len(collisions) == 1

    # Leak-audit clause A, asserted directly against the egress oracle's own
    # text: the only "Kurt" reaching egress is the one inside "Kurt
    # Steinmetz" -- the blinder's own output -- never a standalone real "Kurt".
    assert re.search(r"\bKurt Steinmetz\b", outbound_text)
    assert not re.search(r"(?<!\w)Kurt(?!\w)(?! Steinmetz)", outbound_text)

    item = inbox.list()[0]
    assert item.real == "Kurt"
    response = {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Understood, {item.provisional_surrogate} and Kurt "
                    "Steinmetz are both noted."
                ),
            }
        ],
        "model": "m",
        "stop_reason": "end_turn",
    }

    restored = restore_response(response, session)
    restored_text = restored["content"][0]["text"]
    assert restored_text == "Understood, Kurt and Employer Org are both noted."

    # Closed-world: resolution_gate finds nothing unresolved.
    resolution_gate(restored, session)


def test_leaf_pairing_holds_regardless_of_client_json_key_order():
    # Acceptance criterion: leaves are paired by shared traversal, not by
    # index or content. The client's own top-level key order puts "messages"
    # before "system" -- the blinder always visits system first regardless --
    # and the payload carries several non-hop string leaves (model, a role
    # string, tools[].name, a JSON-Schema structural token) that must never be
    # mistaken for a paired blinder leaf.
    mapping = SurrogateMapping.from_pairs([("Employer Org", "Kurt Steinmetz")])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())
    payload = {
        "messages": [{"role": "user", "content": "Checking in, nothing else to add."}],
        "model": "claude-3-5-sonnet",
        "tools": [
            {
                "name": "lookup_tool",
                "description": "A neutral tool description naming no referent.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ],
        "system": "Please loop in Kurt about it. Employer Org already signed off.",
    }
    assert list(payload.keys()) == ["messages", "model", "tools", "system"]

    blinded, session = blindfold_payload(payload, mapping, detector, inbox)
    assert "Kurt Steinmetz" in blinded["system"]

    collisions = leak_gate(blinded, mapping, inbox, session)
    assert len(collisions) == 1
    assert "range the blinder itself wrote" in collisions[0]

    # Non-hop leaves are untouched and never paired as a blinder leaf.
    assert blinded["model"] == "claude-3-5-sonnet"
    assert blinded["messages"][0]["role"] == "user"
    assert blinded["tools"][0]["name"] == "lookup_tool"
    assert blinded["tools"][0]["input_schema"]["required"] == ["query"]


def test_an_unpairable_leaf_blocks_rather_than_being_excused():
    # Acceptance criterion: a leaf that cannot be positively paired with its
    # recorded ranges is treated as UNRECORDED -- a match there blocks. A
    # session that never ran the blind pass producing this exact text has no
    # ranges recorded for any of its leaves, so the identical byte-for-byte
    # payload that the REAL session excuses fails closed with an unrelated one.
    mapping = SurrogateMapping.from_pairs([("Some Referent", "Kurt Steinmetz")])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Please loop in Kurt about it. Some Referent already "
                    "signed off."
                ),
            }
        ],
    }

    blinded, real_session = blindfold_payload(payload, mapping, detector, inbox)

    collisions = leak_gate(blinded, mapping, inbox, real_session)
    assert len(collisions) == 1

    unrelated_session = ExchangeSession()
    with pytest.raises(LeakError):
        leak_gate(blinded, mapping, inbox, unrelated_session)


def test_a_stripped_schema_structural_leaf_does_not_shift_a_later_genuine_miss_into_excusal():
    # Reviewer-found hole (cycle 1 -> cycle 2): `_gate_excluded_view` strips
    # `enum`/`type`/`required` subtrees wholesale (`_strip_schema_structural_
    # tokens`) BEFORE the mirror walk runs, but the blinder's own
    # `_blindfold_schema_prose` recurses INTO an `enum` value and blinds any
    # `description` nested there -- a real leaf, a real `_begin_leaf` call, a
    # real slot in `session._leaf_slots`. The mirror walk never re-visits that
    # leaf (its dict key is gone from `gate_view` entirely), so it calls
    # `_begin_leaf` one time FEWER than the original blind pass did. Every
    # leaf paired AFTER that point reuses the wrong slot -- a LATER leaf's
    # real (unrelated) text gets checked against an EARLIER leaf's recorded
    # ranges. Positional pairing degrades silently instead of failing closed.
    mapping = SurrogateMapping.from_pairs([("Org A", "Aurora Systems")])
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "nothing sensitive here."}],
        "tools": [
            {
                "name": "t1",
                "description": "A neutral tool description.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "p": {
                            "type": "string",
                            # A JSON-Schema `enum` doesn't legally carry a
                            # nested "description" object in practice, but
                            # `_blindfold_schema_prose` recurses into ANY
                            # value under a non-"description" key looking for
                            # one, and `_strip_schema_structural_tokens`
                            # drops the whole `enum` subtree regardless of
                            # its shape -- this is the exact traversal
                            # disagreement, independent of schema validity.
                            "enum": [{"description": "Org A is on file."}],
                        }
                    },
                },
            },
            {
                "name": "t2",
                # Seeded into `mapping` only AFTER `blindfold_payload` below
                # has already run, so the blind pass never had a chance to
                # rewrite it -- a genuine miss, byte-for-byte as typed.
                "description": "Ridge Referent stays as typed.",
            },
        ],
    }

    blinded, session = blindfold_payload(payload, mapping, None, None)
    assert (
        blinded["tools"][0]["input_schema"]["properties"]["p"]["enum"][0]["description"]
        == "Aurora Systems is on file."
    )
    assert blinded["tools"][1]["description"] == "Ridge Referent stays as typed."

    mapping.seed("Ridge Referent", "Some Other Surrogate")

    with pytest.raises(LeakError):
        leak_gate(blinded, mapping, None, session)


def test_collect_text_joins_leaves_with_nul_so_a_value_cannot_match_across_fields():
    # Regression guard (salvaged from #418, closed as a duplicate of this
    # issue): pins the exact separator so a future change to it fails loudly
    # instead of silently letting a value match across two unrelated fields.
    assert _collect_text({"a": "foo", "b": "bar"}) == "foo\x00bar"
    assert _collect_text(["one", "two", "three"]) == "one\x00two\x00three"
