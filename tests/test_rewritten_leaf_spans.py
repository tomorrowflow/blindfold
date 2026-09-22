"""Issue #399 (ADR-0059 §2-§3): while Payload inspection is armed, the engine
records every string leaf it rewrote -- in blindfolded form, with per-span
offsets into that blindfolded text and the surrogate written at each.

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
from blindfold.surrogates import SurrogateMapping


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


def test_session_does_not_track_leaves_when_not_armed():
    session = engine.ExchangeSession()

    assert session._begin_leaf("text") is None
    assert session._current_leaf() is None
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
