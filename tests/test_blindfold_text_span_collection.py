"""Issue #325: the ~300-line five-stage ``_blindfold_text`` pipeline used to mutate
its working string in-band, rebinding it at four different points -- each stage's
span offsets were valid only because of *where* the previous rebind happened,
documented by a comment rather than enforced by structure (the offset-arithmetic
bug class #310/#311/#179/#289 all independently patched around).

This restructures the L2 and L1 stages so each *collects* replacement spans
(:class:`blindfold.engine.ReplacementSpan`) against one frozen input text, instead
of mutating a shared accumulator mid-detection. These tests exercise the two
stage-level collectors directly, against a literal frozen string, independent of
the full ``_blindfold_text``/``blindfold_payload`` pipeline -- the acceptance
criterion this issue names explicitly ("at least the L2 and L1 stages gain direct
stage-level unit tests against frozen text").
"""

from __future__ import annotations

from blindfold import engine
from blindfold.detection import Entity
from blindfold.surrogates import SurrogateMapping


def test_collect_l2_spans_finds_a_known_entity_against_frozen_text():
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    text = "Please loop in Anna Schmidt on this."

    spans = engine._collect_l2_spans(text, mapping)

    assert len(spans) == 1
    (span,) = spans
    assert (span.start, span.end) == (15, 27)
    assert text[span.start : span.end] == "Anna Schmidt"
    assert span.surrogate == "Berta Vogel"
    assert span.real == "Anna Schmidt"
    assert span.layer == "l2"


def test_collect_l2_spans_is_pure_and_does_not_mutate_its_input():
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    text = "Anna Schmidt signed off."

    engine._collect_l2_spans(text, mapping)

    # The frozen string itself is of course immutable in Python, but the point
    # of this test is architectural: calling the collector a second time against
    # the same frozen text must reproduce the identical span set -- nothing about
    # collection may depend on call order or leftover state (issue #325's "a
    # stale offset becomes impossible by construction").
    spans_again = engine._collect_l2_spans(text, mapping)
    assert spans_again == engine._collect_l2_spans(text, mapping)


def test_collect_l1_spans_finds_an_email_against_frozen_text():
    mapping = SurrogateMapping.from_pairs([])
    text = "Contact me at stefan.wegner@enervia.ch about the patch."

    spans = engine._collect_l1_spans(text, mapping)

    assert len(spans) == 1
    (span,) = spans
    assert text[span.start : span.end] == "stefan.wegner@enervia.ch"
    assert span.real == "stefan.wegner@enervia.ch"
    assert span.layer == "l1:email"
    # The reserved-namespace surrogate was actually minted into the mapping.
    assert mapping.surrogate_for("stefan.wegner@enervia.ch") == span.surrogate


def test_collect_l1_spans_skips_an_occurrence_excluded_by_an_earlier_stage():
    # Mirrors the pre-#325 "if span.value not in result: continue" guard: a PII
    # value whose only occurrence overlaps a range an earlier, higher-precedence
    # stage (L2) already claimed this pass is not re-detected here.
    mapping = SurrogateMapping.from_pairs([])
    text = "Contact me at stefan.wegner@enervia.ch about the patch."
    email_start = text.index("stefan.wegner@enervia.ch")
    email_end = email_start + len("stefan.wegner@enervia.ch")

    spans = engine._collect_l1_spans(
        text, mapping, exclude=[(email_start, email_end)]
    )

    assert spans == []
    assert mapping.surrogate_for("stefan.wegner@enervia.ch") is None


def test_collect_l1_spans_skips_a_value_that_is_already_a_known_surrogate():
    # A previously-minted PII surrogate echoed back verbatim (e.g. quoted from an
    # earlier exchange) must never be re-blindfolded into a second surrogate.
    mapping = SurrogateMapping.from_pairs([])
    known_surrogate = mapping.mint_pii("email", "stefan.wegner@enervia.ch")
    text = f"As discussed at {known_surrogate}, ..."

    spans = engine._collect_l1_spans(text, mapping)

    assert spans == []
