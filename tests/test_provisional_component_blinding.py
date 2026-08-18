"""Issue #306 (#74 run 7): mirror #304's positional-alignment rule onto the
*blinding* side. #304 taught restore that a two-word surrogate/real pair decomposes
into aligned word components (``_component_restore_map``, ``engine.py:1436``);
nothing taught the blinder the inverse, so a later bare component of an already-
provisional referent's real value (bare "Priya" once "Priya Nadkarni" -> "Alex
Brenner" is in the review inbox) was genuinely novel to L2/the deterministic
provisional-pair pass, reached L3 as a fresh candidate, and minted a second referent
with an independently drawn surrogate -- one person counted twice in the same inbox.

ADR-0036 (amended by #304) supplies the rule; this issue mirrors it, sharing
ADR-0051's single-derivation discipline: ``leak_gate`` and
``engine._apply_provisional_pairs`` both read ``engine._provisional_pair_map`` --
the gate checks its keys, the blinder rewrites source to target -- so the gate's
checked surface and the blinder's rewritten surface can never drift apart.

Leak-audit clauses:
- A: proven directly -- a bare component of an already-provisional real value never
  egresses, and (via the run-before-L3 ordering #300 already established) never
  even reaches the adjudicator as a fresh candidate.
- E (stable): reproven -- one person, one referent, one surrogate; run 7's defect
  (a second referent minted for the same person) does not recur.
- F: leak_gate's checked surface is proven to widen in lockstep with the blinder's
  (ADR-0051 symmetry), pinned the same way #299/#300 pinned their own single
  derivation.
N/A this slice: B/C/D/G -- restore (`_component_restore_map` itself), the mapping
store, and mint-time collision-avoidance are all unchanged code paths; B/C are
exercised generically here (restore round-trip) but not newly implemented.
"""

from __future__ import annotations

import pytest

from blindfold import engine
from blindfold.engine import LeakError, blindfold_payload, leak_gate, restore_response
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import _PROVISIONAL_POOL, ReviewInbox
from blindfold.surrogates import SurrogateMapping

# Issue #338 enlarged review._PROVISIONAL_POOL from 8 to 32 entries, position-stable --
# these tests fill the WHOLE named pool (whatever its current size) to reach the
# opaque BFX fallback, rather than a hardcoded 8.
_POOL_SIZE = len(_PROVISIONAL_POOL)
_FALLBACK = f"BFX{_POOL_SIZE:04d}"


class _ConfirmPriya:
    """Would confirm bare "Priya" as a fresh person candidate -- stands in to prove
    L3 never gets the chance, mirroring
    ``test_the_provisional_substitution_runs_before_l3_so_the_hop_never_re_mints_a_second_row``'s
    own precedent (#300): if the provisional-pair substitution didn't run first,
    this detector WOULD confirm the bare token and mint a second row.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text != "Priya":
            return L3Adjudication(is_entity=False)
        return L3Adjudication(is_entity=True, entity_type="person")


def test_bare_first_name_blinds_to_the_aligned_surrogate_component_no_second_mint():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert(
        "Priya Nadkarni",
        context="...Priya Nadkarni signed off on the deploy...",
        entity_type="person",
    )
    item = inbox.list()[0]
    assert item.provisional_surrogate == "Alex Brenner"  # pool's first person entry

    detector = L3Detector(_ConfirmPriya())
    later_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Ask Priya to review the mapping."}],
    }

    blinded, _session = blindfold_payload(later_payload, mapping, detector, inbox)

    text = blinded["messages"][0]["content"]
    assert "Priya" not in text
    assert "Alex" in text
    # E-stable (run 7's own defect): one person, one referent -- not a second row.
    assert len(inbox.list()) == 1


class _ConfirmTheFullNameOccurrence:
    """Confirms the candidate token "Priya" only where its context shows the full
    "Priya Nadkarni" occurrence (mirrors ``_ConfirmOnlyTheSuffixedOccurrence`` in
    ``test_provisional_variation_surface.py``) -- so a *later* hop's bare "Priya"
    is never independently (re-)confirmed by L3; it must be caught by the
    deterministic component pass instead.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        full = "Priya Nadkarni"
        if candidate.text != "Priya":
            return L3Adjudication(is_entity=False)
        occurrence = candidate.context[
            candidate.context_offset : candidate.context_offset + len(full)
        ]
        if occurrence != full:
            return L3Adjudication(is_entity=False)
        span_start = candidate.start
        return L3Adjudication(
            is_entity=True, entity_type="person",
            span_start=span_start, span_end=span_start + len(full),
        )


def test_restore_round_trips_both_the_bare_component_and_the_full_surrogate_in_one_response():
    # Acceptance criterion 2: "Alex" in the response comes back as "Priya", and
    # "Alex Brenner" still comes back as "Priya Nadkarni", in the same response.
    # Both pairs get recorded within one exchange: the first hop's full-name
    # occurrence mints item 1 (and records the whole pair, #299/#300, unchanged);
    # the later hop's bare "Priya" is caught by #306's new component pass.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmTheFullNameOccurrence())
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Priya Nadkarni signed off on the deploy."},
            {"role": "assistant", "content": "Noted, thanks."},
            {"role": "user", "content": "Ask Priya to review the mapping too."},
        ],
    }
    _blinded, session = blindfold_payload(payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.provisional_surrogate == "Alex Brenner"

    third_hop_text = _blinded["messages"][2]["content"]
    assert "Priya" not in third_hop_text
    assert "Alex" in third_hop_text

    response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": "Sure, I'll ping Alex now -- Alex Brenner signed off already.",
            }
        ],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    restored = restore_response(response, session)

    assert restored["content"][0]["text"] == (
        "Sure, I'll ping Priya now -- Priya Nadkarni signed off already."
    )


def test_leak_gate_fails_closed_on_a_bare_real_word_component_of_a_provisional_referent():
    # Acceptance criterion 3 (ADR-0051 symmetry): bare "Priya" is in leak_gate's
    # checked set for item 1, from the same derivation the blinder uses.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert(
        "Priya Nadkarni",
        context="...Priya Nadkarni signed off on the deploy...",
        entity_type="person",
    )

    leaky_outbound = {
        "messages": [{"role": "user", "content": "Priya asked for an update."}]
    }

    with pytest.raises(LeakError):
        leak_gate(leaky_outbound, mapping, inbox)


def test_a_provisional_surrogate_fallback_label_contributes_no_components():
    # Acceptance criterion 5 (#286, shape updated by ADR-0052/#330): past pool
    # exhaustion the fallback is a single opaque, whitespace-free token
    # (_FALLBACK) rather than the old three-word numbered label -- word counts
    # between a multi-word real and the one-word fallback can never align, so
    # the component pass contributes nothing by construction (no digit-only-
    # word guard needed at all). If a real word were ever allowed to align to
    # a positional fragment of the fallback, blinding would inject that
    # fragment into the outbound payload AND poison restore with a stray key,
    # corrupting ordinary text in the response ("utf-8" -> "utf-Holdings") the
    # same way #286 did.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    for i in range(_POOL_SIZE):
        inbox.upsert(
            f"Filler Person {i}",
            context=f"...Filler Person {i} joined the call...",
            entity_type="person",
        )
    inbox.upsert(
        "Kestrel Dynamics Holdings",
        context="...Kestrel Dynamics Holdings reported record profits...",
        entity_type="person",
    )
    item = inbox.list()[-1]
    assert item.real == "Kestrel Dynamics Holdings"
    assert item.provisional_surrogate == _FALLBACK

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Ask Holdings for the report."}],
    }
    blinded, _session = blindfold_payload(payload, mapping, None, inbox)

    text = blinded["messages"][0]["content"]
    assert text == "Ask Holdings for the report."


def test_a_component_ambiguous_across_two_live_rows_contributes_nothing():
    # Acceptance criterion 6: two live inbox rows share the real word "Priya"
    # but align to two different surrogate words -- ambiguous, so neither
    # registers it as a blinding component. The bare token is left untouched
    # (mirroring test_component_shared_by_two_surrogates_is_left_untouched on
    # the restore side, #304/ADR-0036 acceptance criterion 5).
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert(
        "Priya Nadkarni",
        context="...Priya Nadkarni signed off on the deploy...",
        entity_type="person",
    )
    inbox.upsert(
        "Priya Shah",
        context="...Priya Shah reviewed the contract...",
        entity_type="person",
    )
    real_items = {item.real: item for item in inbox.list()}
    assert real_items["Priya Nadkarni"].provisional_surrogate == "Alex Brenner"
    assert real_items["Priya Shah"].provisional_surrogate == "Berta Falke"

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Ask Priya to review the mapping."}],
    }
    blinded, _session = blindfold_payload(payload, mapping, None, inbox)

    text = blinded["messages"][0]["content"]
    assert text == "Ask Priya to review the mapping."
    assert len(inbox.list()) == 2


def test_widening_the_shared_component_derivation_widens_both_the_blinder_and_the_gate(
    monkeypatch,
):
    # Pinned exactly like #299/#300's own single-derivation test
    # (test_the_message_hop_substitution_set_is_derived_from_the_same_shared_function_leak_gate_uses):
    # this fails if a component is ever added to one side only -- widening
    # ``_provisional_component_map`` (the shared derivation) must move the
    # blinder's rewritten surface and leak_gate's checked surface together.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert(
        "Priya Nadkarni",
        context="...Priya Nadkarni signed off on the deploy...",
        entity_type="person",
    )

    def _widened(items):
        return {"Nadkarni": "Brenner"}

    monkeypatch.setattr(engine, "_provisional_component_map", _widened)

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Tell Nadkarni the plan changed."}],
    }
    blinded, _session = blindfold_payload(payload, mapping, None, inbox)
    text = blinded["messages"][0]["content"]
    assert "Nadkarni" not in text
    assert "Brenner" in text

    with pytest.raises(LeakError):
        leak_gate(
            {"messages": [{"role": "user", "content": "Nadkarni called."}]},
            mapping,
            inbox,
        )


def test_a_length_mismatched_pair_contributes_no_components():
    # Acceptance criterion 4: "Kestrel Dynamics GmbH" -> "Rheinblick Consulting" is
    # length-mismatched (3 real words vs. 2 surrogate words) -- a later bare
    # "Kestrel" is left to #289/#296's existing legal-form path (which only ever
    # adds the suffix-stripped bare *organization name*, "Kestrel Dynamics", never
    # a single word alone), not to this issue's new mechanism. Unchanged from
    # before #306: bare "Kestrel" alone was never blinded.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert(
        "Acme Corp", context="...Acme Corp is our vendor...", entity_type="organization"
    )
    inbox.upsert(
        "Kestrel Dynamics GmbH",
        context="...signed with Kestrel Dynamics GmbH last week...",
        entity_type="organization",
    )
    item = inbox.list()[1]
    assert item.real == "Kestrel Dynamics GmbH"
    assert item.provisional_surrogate == "Rheinblick Consulting"

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Kestrel called about the invoice."}],
    }
    blinded, _session = blindfold_payload(payload, mapping, None, inbox)

    text = blinded["messages"][0]["content"]
    assert text == "Kestrel called about the invoice."


def test_a_fallback_label_with_equal_word_count_contributes_no_components():
    # Issue #329 (post-merge gate finding on #306), reconciled with ADR-0052
    # (issue #330): the fallback label is a single opaque token
    # ("BFX{N:04d}"), carrying no entity meaning despite having alphabetic
    # characters (the "BFX" prefix). A real value that happens to share the
    # label's word count (1 word, here "Kestrel" against _FALLBACK) would
    # otherwise let the per-word alphabetic guard register "Kestrel" ->
    # _FALLBACK as a component pair, since both sides have alphabetic
    # characters. The whole label must be skipped before decomposition, not
    # word-by-word.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    for i in range(_POOL_SIZE):
        inbox.upsert(
            f"Filler Person {i}",
            context=f"...Filler Person {i} joined the call...",
            entity_type="person",
        )
    inbox.upsert(
        "Kestrel",
        context="...Kestrel reported record profits...",
        entity_type="person",
    )
    item = inbox.list()[-1]
    assert item.real == "Kestrel"
    assert item.provisional_surrogate == _FALLBACK

    assert engine._provisional_component_map(inbox.list()) == {}


def test_a_fallback_label_whole_value_still_blinds_but_bare_component_does_not_egress():
    # Issue #329, acceptance criterion 2 (reconciled with ADR-0052/#330's
    # opaque single-token fallback): the whole-value pair
    # (_provisional_known_value_set) is untouched by this fix -- the full real
    # value still blinds to the fallback label -- but a bare component word of
    # that real ("Kestrel") is no longer rewritten, since it no longer aligns
    # to a meaningless label word.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    for i in range(_POOL_SIZE):
        inbox.upsert(
            f"Filler Person {i}",
            context=f"...Filler Person {i} joined the call...",
            entity_type="person",
        )
    inbox.upsert(
        "Kestrel Dynamics Holdings",
        context="...Kestrel Dynamics Holdings reported record profits...",
        entity_type="person",
    )
    item = inbox.list()[-1]
    assert item.provisional_surrogate == _FALLBACK

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Kestrel Dynamics Holdings reported earnings. "
                    "Ask Kestrel for the follow-up."
                ),
            }
        ],
    }
    blinded, _session = blindfold_payload(payload, mapping, None, inbox)

    text = blinded["messages"][0]["content"]
    assert _FALLBACK in text
    assert "Kestrel Dynamics Holdings" not in text
    assert text.endswith("Ask Kestrel for the follow-up.")


def test_an_ordinary_response_containing_the_fallback_labels_own_words_round_trips_unchanged():
    # Issue #329, acceptance criterion 3 (leak-audit: restore returns real
    # values exactly): "Provisional" and "Surrogate" must never become Pass-1
    # restore keys, so an ordinary response using those words as themselves
    # (not as the injected label) is untouched by restore.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    for i in range(_POOL_SIZE):
        inbox.upsert(
            f"Filler Person {i}",
            context=f"...Filler Person {i} joined the call...",
            entity_type="person",
        )
    inbox.upsert(
        "Kestrel Dynamics Holdings",
        context="...Kestrel Dynamics Holdings reported record profits...",
        entity_type="person",
    )

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Ask Holdings for the report."}],
    }
    _blinded, session = blindfold_payload(payload, mapping, None, inbox)

    upstream_response = {
        "content": [
            {
                "type": "text",
                "text": "This is a Provisional Surrogate for testing purposes.",
            }
        ]
    }
    restored = restore_response(upstream_response, session)
    assert (
        restored["content"][0]["text"]
        == "This is a Provisional Surrogate for testing purposes."
    )


def test_leak_gate_does_not_block_on_the_skipped_fallback_component_words():
    # Issue #329, acceptance criterion 4 (ADR-0051 symmetry): "Kestrel" and
    # "Dynamics" are no longer keys of _provisional_pair_map, so leak_gate
    # must not flag their bare occurrence outbound.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    for i in range(_POOL_SIZE):
        inbox.upsert(
            f"Filler Person {i}",
            context=f"...Filler Person {i} joined the call...",
            entity_type="person",
        )
    inbox.upsert(
        "Kestrel Dynamics Holdings",
        context="...Kestrel Dynamics Holdings reported record profits...",
        entity_type="person",
    )

    outbound = {
        "messages": [{"role": "user", "content": "Ask Kestrel about Dynamics."}]
    }
    leak_gate(outbound, mapping, inbox)


def test_an_ordinary_response_round_trips_unchanged_when_the_fallback_labels_whole_value_is_actually_injected():
    # Strengthens the criterion-3 test above, which only egresses a single bare
    # component word ("Holdings") and so never populates session.injected with
    # the fallback label's whole-value pair -- it therefore never reaches
    # _component_restore_map's guard at all. This test injects the WHOLE real
    # value instead, the only way session.injected actually gets populated.
    # Issue #329 (maintainer rescope, 2026-08-17): _component_restore_map now
    # skips a fallback-labeled pair the same way _provisional_component_map
    # does on the blinding side -- this was cycle 2's strict-xfail pin,
    # flipped to an expected pass now that the restore-side guard exists.
    # Reconciled with ADR-0052/#330's opaque single-token fallback format.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    for i in range(_POOL_SIZE):
        inbox.upsert(
            f"Filler Person {i}",
            context=f"...Filler Person {i} joined the call...",
            entity_type="person",
        )
    inbox.upsert(
        "Kestrel Dynamics Holdings",
        context="...Kestrel Dynamics Holdings reported record profits...",
        entity_type="person",
    )
    item = inbox.list()[-1]
    assert item.provisional_surrogate == _FALLBACK

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Kestrel Dynamics Holdings reported earnings."}
        ],
    }
    _blinded, session = blindfold_payload(payload, mapping, None, inbox)
    assert session.injected == {_FALLBACK: "Kestrel Dynamics Holdings"}

    upstream_response = {
        "content": [
            {
                "type": "text",
                "text": "This is a Provisional Surrogate for testing purposes.",
            }
        ]
    }
    restored = restore_response(upstream_response, session)
    assert (
        restored["content"][0]["text"]
        == "This is a Provisional Surrogate for testing purposes."
    )
