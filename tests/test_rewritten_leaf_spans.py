"""Issue #399 (ADR-0059 §2-§3): while Payload inspection is armed, the engine
records every string leaf it rewrote -- in blindfolded form, with per-span
offsets into that blindfolded text and the surrogate written at each.

Issue #415 (ADR-0051's #406 amendment) promotes the offsets half of this
record to always-on: `ExchangeSession`'s per-leaf accumulator now exists
regardless of `retain_rewritten_leaves`, because the self-poisoning guard
(`_injected_surrogate_ranges`) reads it directly instead of searching text for
surrogate values. Only the retained blindfolded *text* -- and, with it,
`rewritten_leaves()`, the bounded store, its endpoint and the Unprotected-mode
check -- stays armed-gated exactly as ADR-0059 specifies.

This module builds bottom-up: unit tests against `_apply_spans`'s new
output-offset reporting (the "hard part" the issue names -- offsets recorded
in an early phase must be remapped through every later splice on the same
leaf), before any engine wiring is asserted through the public
`blindfold_payload` surface.

Leak-audit: N/A for this file's own unit-level tests (no request path
exercised) -- see the end-to-end tests further down this module and in
sibling files for the request-path leak-audit coverage this issue requires.
"""

from __future__ import annotations

from blindfold import engine
from blindfold.engine import blindfold_chat_completions_payload, blindfold_payload
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _TypedStubAdjudicator:
    """Mirrors tests/test_l3_surrogate_coalescing.py's own stub: confirms
    exactly the candidate texts present in ``types``, carrying each one's
    entity_type; dismisses everything else."""

    def __init__(self, types: dict[str, str | None]) -> None:
        self._types = types

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text not in self._types:
            return L3Adjudication(is_entity=False)
        return L3Adjudication(is_entity=True, entity_type=self._types[candidate.text])


class _ConfirmOnlyWhenRegistering:
    """Mirrors tests/test_mid_exchange_mint_reaches_earlier_hop.py's own stub
    (issue #387): confirms "Kestrel" as an organization only when its own
    hop's context mentions "register", so hop 1 (no such context) is
    correctly dismissed on its own merits while hop 2 confirms and mints --
    landing the referent in the review inbox one hop too late for hop 1's
    OWN pass to have seen it. The #387 catch-up pass then re-applies the
    provisional pair to hop 1's ALREADY-blindfolded leaf.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text != "Kestrel":
            return L3Adjudication(is_entity=False)
        if "register" in candidate.context:
            return L3Adjudication(is_entity=True, entity_type="organization")
        return L3Adjudication(is_entity=False)


def test_apply_spans_reports_a_single_splice_at_its_own_output_offset():
    text = "Hello Anna, welcome."
    spans = [engine.ReplacementSpan(6, 10, "Berta", "Anna", "l2")]
    leaf = engine._LeafAccumulator(leaf_id="leaf-0", label="text")

    result = engine._apply_spans(text, spans, leaf=leaf)

    assert result == "Hello Berta, welcome."
    assert [(s.start, s.end, s.surrogate, s.layer) for s in leaf.spans] == [
        (6, 11, "Berta", "l2")
    ]
    assert leaf.text == result


def test_apply_spans_with_no_leaf_is_unaffected_by_the_new_parameter():
    text = "Hello Anna, welcome."
    spans = [engine.ReplacementSpan(6, 10, "Berta", "Anna", "l2")]

    result = engine._apply_spans(text, spans)

    assert result == "Hello Berta, welcome."


def test_apply_spans_remaps_an_earlier_splices_recorded_offsets_through_a_later_one():
    # The hard part the issue names: a leaf rewritten once already (phase 1)
    # and rewritten again on the evolving text (phase 2, e.g. L3's own second
    # splice in `_blindfold_text`) must keep BOTH span sets correctly indexed
    # into the FINAL text -- not just the second splice's own new span.
    text = "Anna met Kurt at the office."
    leaf = engine._LeafAccumulator(leaf_id="leaf-0", label="text")

    # Phase 1: "Anna" (a short surrogate) -> "Berta Vogel" (a longer one),
    # shifting everything after it.
    phase1_spans = [engine.ReplacementSpan(0, 4, "Berta Vogel", "Anna", "l2")]
    phase1_result = engine._apply_spans(text, phase1_spans, leaf=leaf)
    assert phase1_result == "Berta Vogel met Kurt at the office."

    # Phase 2 (L3, against the phase-1 output): "Kurt" -> "Otto", a SHORTER
    # surrogate, sitting entirely after phase 1's own splice.
    kurt_start = phase1_result.index("Kurt")
    phase2_spans = [
        engine.ReplacementSpan(kurt_start, kurt_start + 4, "Otto", "Kurt", "l3")
    ]
    phase2_result = engine._apply_spans(phase1_result, phase2_spans, leaf=leaf)
    assert phase2_result == "Berta Vogel met Otto at the office."

    # Phase 1's own recorded span must still point at "Berta Vogel" in the
    # FINAL text -- unaffected by phase 2's later splice, which happened
    # entirely to its right.
    recorded = {(s.start, s.end, s.surrogate, s.layer) for s in leaf.spans}
    berta_start = phase2_result.index("Berta Vogel")
    otto_start = phase2_result.index("Otto")
    assert (berta_start, berta_start + len("Berta Vogel"), "Berta Vogel", "l2") in recorded
    assert (otto_start, otto_start + len("Otto"), "Otto", "l3") in recorded
    assert len(recorded) == 2
    assert leaf.text == phase2_result


def test_apply_spans_remaps_an_earlier_recorded_offset_when_a_later_splice_lands_to_its_left():
    # Same composition, but this time phase 2's splice sits BEFORE phase 1's
    # own recorded span in text order, and changes length -- phase 1's
    # recorded offset must shift to track it.
    text = "Kurt told Anna the news."
    leaf = engine._LeafAccumulator(leaf_id="leaf-0", label="text")

    anna_start = text.index("Anna")
    phase1_spans = [
        engine.ReplacementSpan(anna_start, anna_start + 4, "Berta Vogel", "Anna", "l2")
    ]
    phase1_result = engine._apply_spans(text, phase1_spans, leaf=leaf)
    assert phase1_result == "Kurt told Berta Vogel the news."

    phase2_spans = [engine.ReplacementSpan(0, 4, "Otto Steinmetz", "Kurt", "l3")]
    phase2_result = engine._apply_spans(phase1_result, phase2_spans, leaf=leaf)
    assert phase2_result == "Otto Steinmetz told Berta Vogel the news."

    recorded = {(s.start, s.end, s.surrogate, s.layer) for s in leaf.spans}
    otto_start = phase2_result.index("Otto Steinmetz")
    berta_start = phase2_result.index("Berta Vogel")
    assert (otto_start, otto_start + len("Otto Steinmetz"), "Otto Steinmetz", "l3") in recorded
    assert (berta_start, berta_start + len("Berta Vogel"), "Berta Vogel", "l2") in recorded
    assert len(recorded) == 2


def test_session_tracks_leaf_spans_but_not_text_when_not_armed():
    # Issue #415 (ADR-0051's #406 amendment): the span record is now a
    # request-path invariant the self-poisoning guard depends on, so it is
    # built regardless of Payload inspection's own armed flag -- only the
    # ADR-0059 *text* retention (and, with it, `rewritten_leaves()`) stays
    # armed-gated.
    session = engine.ExchangeSession()

    leaf = session._begin_leaf("text")

    assert leaf is not None
    assert session._current_leaf() is leaf
    result = engine._apply_spans(
        "Anna was here",
        [engine.ReplacementSpan(0, 4, "Berta", "Anna", "l2")],
        leaf=leaf,
    )

    assert result == "Berta was here"
    assert [(s.start, s.end, s.surrogate) for s in leaf.spans] == [(0, 5, "Berta")]
    assert leaf.text == ""
    assert session.rewritten_leaves() == ()


def test_session_begins_a_new_leaf_slot_each_call_when_armed():
    session = engine.ExchangeSession(retain_rewritten_leaves=True)

    first = session._begin_leaf("user: text block")
    second = session._begin_leaf("tool_result: tool-result body")

    assert first is not None and second is not None
    assert first is not second
    assert first.label == "user: text block"
    assert second.label == "tool_result: tool-result body"
    assert session._current_leaf() is second


def test_session_reset_leaf_walk_replays_the_same_slots_by_position():
    session = engine.ExchangeSession(retain_rewritten_leaves=True)
    first = session._begin_leaf("a")
    second = session._begin_leaf("b")

    session.reset_leaf_walk()
    replay_first = session._begin_leaf("ignored on reuse")
    replay_second = session._begin_leaf("also ignored")

    assert replay_first is first
    assert replay_second is second
    # Reuse never overwrites the label recorded at first creation.
    assert first.label == "a"
    assert second.label == "b"


def test_rewritten_leaves_omits_a_leaf_with_no_recorded_span():
    session = engine.ExchangeSession(retain_rewritten_leaves=True)
    untouched = session._begin_leaf("text")
    untouched.text = "nothing changed here"
    touched = session._begin_leaf("text")
    engine._apply_spans(
        "Anna was here",
        [engine.ReplacementSpan(0, 4, "Berta", "Anna", "l2")],
        leaf=touched,
    )

    leaves = session.rewritten_leaves()

    assert len(leaves) == 1
    assert leaves[0].text == "Berta was here"
    assert leaves[0].label == "text"
    assert leaves[0].spans[0].surrogate == "Berta"
    # Stable, distinct ids.
    assert leaves[0].leaf_id


def test_blindfold_payload_retains_the_system_prompts_rewritten_leaf_when_armed():
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    payload = {
        "model": "m",
        "system": "You are assisting Anna Schmidt today.",
        "messages": [{"role": "user", "content": "Hi."}],
    }

    blinded, session = blindfold_payload(
        payload, mapping, retain_rewritten_leaves=True
    )

    leaves = session.rewritten_leaves()
    assert len(leaves) == 1
    leaf = leaves[0]
    assert leaf.text == blinded["system"]
    assert "Berta Vogel" in leaf.text
    assert leaf.label.startswith("system")
    (span,) = leaf.spans
    assert leaf.text[span.start : span.end] == "Berta Vogel"
    assert span.surrogate == "Berta Vogel"


def test_blindfold_payload_retains_nothing_when_not_armed():
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    payload = {
        "model": "m",
        "system": "You are assisting Anna Schmidt today.",
        "messages": [{"role": "user", "content": "Hi."}],
    }

    _blinded, session = blindfold_payload(payload, mapping)

    assert session.rewritten_leaves() == ()


def test_a_tool_result_body_leaf_is_labeled_distinctly_from_a_plain_text_block():
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "Hi there."},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "Lookup found Anna Schmidt in the CRM.",
                    }
                ],
            },
        ],
    }

    _blinded, session = blindfold_payload(payload, mapping, retain_rewritten_leaves=True)

    leaves = session.rewritten_leaves()
    assert len(leaves) == 1
    assert "tool-result body" in leaves[0].label
    assert "Berta Vogel" in leaves[0].text


def test_a_tool_call_inputs_leaf_is_labeled_as_such():
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "lookup",
                        "input": {"query": "Anna Schmidt"},
                    }
                ],
            },
        ],
    }

    _blinded, session = blindfold_payload(payload, mapping, retain_rewritten_leaves=True)

    leaves = session.rewritten_leaves()
    assert len(leaves) == 1
    assert "tool-call input" in leaves[0].label
    assert "Berta Vogel" in leaves[0].text


def test_a_tool_descriptions_leaf_is_retained_and_labeled():
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Hi."}],
        "tools": [
            {
                "name": "lookup",
                "description": "Looks up Anna Schmidt's account.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    }

    _blinded, session = blindfold_payload(payload, mapping, retain_rewritten_leaves=True)

    leaves = session.rewritten_leaves()
    assert len(leaves) == 1
    assert "tool description" in leaves[0].label
    assert "Berta Vogel" in leaves[0].text


def test_chat_completions_payload_retains_a_rewritten_leaf_when_armed():
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    payload = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "Be nice."},
            {"role": "user", "content": "Please help Anna Schmidt today."},
        ],
    }

    _blinded, session = blindfold_chat_completions_payload(
        payload, mapping, retain_rewritten_leaves=True
    )

    leaves = session.rewritten_leaves()
    assert len(leaves) == 1
    assert "Berta Vogel" in leaves[0].text
    assert leaves[0].label.startswith("user")


def test_a_referent_minted_in_a_later_hop_leaves_the_earlier_hops_leaf_offsets_correct():
    # Acceptance criterion: "a test that mints a referent in a later hop and
    # asserts the earlier hop's recorded offsets still index its own
    # rewritten surrogates" -- the #387 catch-up pass rewrites hop 1's leaf
    # AFTER that hop has already finished and its own accumulator already
    # exists; this pins that the accumulator survives the catch-up splice
    # with correct offsets, not just correct text.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlyWhenRegistering())
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "They said Kestrel emailed again about the invoice."},
            {"role": "user", "content": "Our vendor said please register Kestrel now."},
        ],
    }

    blinded, session = blindfold_payload(
        payload, mapping, detector, inbox, retain_rewritten_leaves=True
    )

    item = inbox.list()[0]
    assert item.real == "Kestrel"
    surrogate = item.provisional_surrogate

    first_hop_text = blinded["messages"][0]["content"]
    second_hop_text = blinded["messages"][1]["content"]
    assert surrogate in first_hop_text
    assert surrogate in second_hop_text

    leaves = session.rewritten_leaves()
    assert len(leaves) == 2
    first_leaf, second_leaf = leaves

    # Each retained leaf's own text must match what actually landed in the
    # payload for that hop...
    assert first_leaf.text == first_hop_text
    assert second_leaf.text == second_hop_text

    # ...and its recorded span must index the surrogate correctly INSIDE
    # that leaf's own text -- including hop 1's, whose only substitution came
    # from the catch-up pass, entirely after its own main-walk visit.
    (first_span,) = first_leaf.spans
    assert first_leaf.text[first_span.start : first_span.end] == surrogate
    (second_span,) = second_leaf.spans
    assert second_leaf.text[second_span.start : second_span.end] == surrogate


def test_overlapping_l3_mint_and_coverage_sweep_spans_are_both_recorded_not_merged_or_dropped():
    # Acceptance criterion / engine.py's own `_apply_spans` docstring: "a
    # coalesced 'Alex Brenner' mint and a separate bare-'Alex' mint's #295
    # coverage sweep both claim the same 'Alex' substring" (issue #292) --
    # the live repro named in this module's own docstring. `_apply_spans` is
    # deliberately called with `assert_no_overlap=False` for L3's splice, so
    # this is not a hypothetical: two DIFFERENT referents ("Alex Brenner"
    # and a separately-occurring bare "Alex") both confirm, and the second
    # referent's #295 coverage sweep re-matches "Alex" nested inside the
    # first referent's own already-confirmed span.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_TypedStubAdjudicator({"Alex": "person", "Brenner": "person"}))
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": "Alex Brenner sent the report. Later, Alex called back.",
            },
        ],
    }

    blinded, session = blindfold_payload(
        payload, mapping, detector, inbox, retain_rewritten_leaves=True
    )

    # Confirm the overlap actually happened (not a hypothetical): two
    # DIFFERENT review items, one of whose surrogate now sits inside the
    # other's own confirmed span in the blinded text.
    assert len(inbox.list()) == 2
    coalesced = next(item for item in inbox.list() if item.real == "Alex Brenner")
    bare = next(item for item in inbox.list() if item.real == "Alex")
    text = blinded["messages"][0]["content"]
    # The bare referent's own surrogate does survive intact (its span is the
    # LAST one spliced, so nothing overwrites it afterward) -- the coalesced
    # referent's surrogate does NOT necessarily survive as a literal
    # substring (its territory is partially overwritten by the later,
    # overlapping splice), which is exactly the "not a bug to fix here"
    # quirk this test documents rather than papers over.
    assert bare.provisional_surrogate in text

    leaves = session.rewritten_leaves()
    assert len(leaves) == 1
    spans = leaves[0].spans

    # Both the coalesced mint's span and the bare mint's coverage-sweep span
    # must be present -- neither dropped, nor collapsed into one record.
    recorded_surrogates = [span.surrogate for span in spans]
    assert recorded_surrogates.count(coalesced.provisional_surrogate) == 1
    assert recorded_surrogates.count(bare.provisional_surrogate) == 2
    assert len(spans) == 3

    # The bare mint's SECOND (non-overlapping) occurrence -- "Later, Alex
    # called back." -- is disjoint from the overlap entirely, so its offset
    # must precisely index its own surrogate in the leaf's own final text.
    non_overlapping = [
        span
        for span in spans
        if span.surrogate == bare.provisional_surrogate
        and leaves[0].text[span.start : span.end] == bare.provisional_surrogate
    ]
    assert len(non_overlapping) >= 1

    # Every recorded offset is a well-formed, in-bounds position -- faithful
    # representation of an overlap never means a nonsensical (e.g. negative)
    # offset, even though the two overlapping spans' own slices cannot both
    # independently reconstruct their surrogate text from a single shared
    # region of the final text (the splice itself has no well-defined
    # disjoint answer for that -- ADR-0059 §3's own "not a bug to fix here").
    for span in spans:
        assert 0 <= span.start <= span.end <= len(leaves[0].text)


def test_detection_reproducibility_armed_and_disarmed_produce_identical_verdicts():
    # ADR-0059 §3: "the flag is read once per exchange, it gates a record
    # rather than a behaviour, and no detection, minting, gating or restore
    # outcome may depend on it." Deterministic-only (L1/L2, no L3/adjudicator
    # wired) per the acceptance criterion, so this measures rather than
    # flakes on L3 sampling.
    payload = {
        "model": "m",
        "system": "You are assisting Anna Schmidt today.",
        "messages": [
            {"role": "user", "content": "Please email anna.schmidt@example.com."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "lookup",
                        "input": {"query": "Anna Schmidt"},
                    }
                ],
            },
        ],
        "tools": [
            {
                "name": "lookup",
                "description": "Looks up Anna Schmidt's account.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    }

    disarmed_blinded, disarmed_session = blindfold_payload(
        payload, SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    )
    armed_blinded, armed_session = blindfold_payload(
        payload,
        SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")]),
        retain_rewritten_leaves=True,
    )

    def _verdict_shape(hop_dict: dict) -> dict:
        # Everything except wall-clock timings, which are inherently
        # non-deterministic and not a "verdict" -- ADR-0059 §3's own
        # reproducibility claim is about detection/minting/gating/restore
        # outcomes, not timing noise.
        return {k: v for k, v in hop_dict.items() if not k.endswith("_duration_ms")}

    assert disarmed_blinded == armed_blinded
    assert disarmed_session.injected == armed_session.injected
    assert [_verdict_shape(hop.to_dict()) for hop in disarmed_session.hops] == [
        _verdict_shape(hop.to_dict()) for hop in armed_session.hops
    ]
    # And, of course, retention itself only happens when armed.
    assert disarmed_session.rewritten_leaves() == ()
    assert armed_session.rewritten_leaves() != ()


def test_client_typed_text_resembling_a_live_surrogate_is_blinded_not_skipped():
    # Issue #415 (ADR-0051's #406 amendment): the one intended behaviour
    # change from promoting the span record to always-on and reading it
    # instead of searching for surrogate values. entity-a's surrogate is a
    # live, known surrogate (minted for a referent this exchange never
    # mentions) whose second word happens to equal entity-b's own bare-word
    # component. The old search-based guard treated the client's own literal
    # occurrence of entity-a's surrogate text as "already injected" and
    # skipped blinding entity-b's real word sitting inside it -- a real value
    # reaching the stub upstream unblinded. The new splice-derived record has
    # nothing recorded at that position (the blinder never wrote there in
    # THIS leaf), so entity-b's component is now correctly detected and
    # blinded.
    #
    # Leak-audit clause A: the real value that used to leak now never reaches
    # `blinded` at all -- asserted directly below, not just "no exception".
    mapping = SurrogateMapping.from_pairs(
        [
            ("entity-a-real", "surrogate-word-one surrogate-word-two"),
            (
                "entity-b-word-one surrogate-word-two",
                "entity-b-surrogate-one entity-b-surrogate-two",
            ),
        ]
    )
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": "Note: surrogate-word-one surrogate-word-two was mentioned.",
            }
        ],
    }

    blinded, session = blindfold_payload(payload, mapping)

    text = blinded["messages"][0]["content"]
    assert "surrogate-word-two" not in text
    assert "entity-b-surrogate-two" in text
    assert session.injected["entity-b-surrogate-two"] == "surrogate-word-two"


def test_untouched_leaves_are_not_retained():
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    payload = {
        "model": "m",
        "system": "Nothing sensitive in here.",
        "messages": [{"role": "user", "content": "Hi, just saying hello."}],
    }

    _blinded, session = blindfold_payload(
        payload, mapping, retain_rewritten_leaves=True
    )

    assert session.rewritten_leaves() == ()
