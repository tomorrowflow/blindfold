"""L3 detection seam (ADR-0003): local-LLM candidate-span adjudication.

L3 is invoked **only on flagged candidate spans plus minimal context** — never the
full payload (ADR-0003). Cost scales with the number of candidate spans, not payload
size. A content cache prevents re-scanning unchanged chunks across agent turns.

Seam stub: a recording adjudicator stands in for Ollama at its network boundary; the
tests assert what crossed that seam, never internal call shapes (leak-audit).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from blindfold.detection import Entity
from blindfold.l3 import (
    CandidateSpan,
    L3Adjudication,
    L3Detector,
    L3Unavailable,
)


@dataclass
class _Call:
    text: str
    context: str


class _RecordingAdjudicator:
    """Stub for Ollama — records every adjudicate() call without firing real I/O."""

    def __init__(
        self, decisions: dict[str, L3Adjudication] | None = None
    ) -> None:
        self.calls: list[_Call] = []
        self._decisions = decisions or {}

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(_Call(text=candidate.text, context=candidate.context))
        return self._decisions.get(
            candidate.text, L3Adjudication(is_entity=False)
        )


def test_l3_invoked_only_on_candidate_spans_with_minimal_context():
    # ADR-0003: L3 receives flagged spans + minimal context, never the full payload.
    # In a long prose body, the only candidates (unknown capitalized tokens) are
    # "Klaus" and "Yasmin"; the stub adjudicator must see exactly those, with a
    # context window that is a strict subset of the full text.
    padding = "The padding is identical and unrelated. " * 50
    text = padding + "We mention Klaus and Yasmin in passing."
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)

    detector.detect(text, known_entities=[])

    flagged = sorted(call.text for call in adjudicator.calls)
    assert flagged == ["Klaus", "Yasmin"]
    for call in adjudicator.calls:
        assert call.text in call.context
        # Minimal context: strictly shorter than the full payload (the whole point
        # of candidate-span adjudication — latency decoupled from file size).
        assert len(call.context) < len(text)


def test_l3_does_not_re_flag_entities_already_covered_by_l2():
    # ADR-0003: L3 adjudicates the *leftovers* — tokens the entity-graph dictionary
    # didn't already match. A token whose surface is a canonical or variation of a
    # known entity is L2's territory and must not waste an L3 call.
    enervia = Entity(
        canonical="Enervia",
        variations=("Enervia AG",),
        surrogate="Projekt Polarstern",
    )
    text = "Please brief Enervia tomorrow about Yasmin."
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)

    detector.detect(text, known_entities=[enervia])

    flagged = sorted(call.text for call in adjudicator.calls)
    assert flagged == ["Yasmin"]


def test_l3_cost_scales_with_candidate_span_count_not_payload_size():
    # ADR-0003: "L3 cost scales with the number of candidate spans, not payload size."
    # Doubling the payload size without adding spans must NOT double adjudicator calls.
    # Coding agents send large files; a full-document NER would time out. Candidate-span
    # adjudication is the design that makes large payloads tractable.
    spans = "We met Klaus and Yasmin briefly."
    small_payload = "The padding is identical. " * 10 + spans
    large_payload = "The padding is identical. " * 1000 + spans

    small_adjudicator = _RecordingAdjudicator()
    large_adjudicator = _RecordingAdjudicator()
    L3Detector(small_adjudicator).detect(small_payload, known_entities=[])
    L3Detector(large_adjudicator).detect(large_payload, known_entities=[])

    # Same number of candidate spans, same number of adjudicator calls — independent
    # of the 100x payload-size difference.
    assert len(large_adjudicator.calls) == len(small_adjudicator.calls)
    assert len(large_adjudicator.calls) == 2  # Klaus, Yasmin


def test_content_cache_skips_re_scanning_unchanged_chunks_across_turns():
    # ADR-0003: "A content cache prevents re-scanning unchanged chunks across agent
    # turns." Agent turns send overlapping payloads — the system prompt + prior turns
    # are re-sent every step. Re-adjudicating identical (span, context) tuples on
    # every turn would make L3 cost scale with turn count, defeating the bound.
    text = "Please brief Klaus tomorrow about the new initiative."
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)

    detector.detect(text, known_entities=[])
    calls_after_turn_one = len(adjudicator.calls)
    detector.detect(text, known_entities=[])  # same payload, agent turn 2

    assert calls_after_turn_one == 1
    # Identical (span, context) on the second turn served entirely from cache.
    assert len(adjudicator.calls) == 1


def test_content_cache_only_re_scans_spans_whose_context_changed():
    # Subtler version: a real coding-agent turn appends a new sentence to an
    # otherwise-unchanged transcript. The cache must serve the unchanged span from
    # the prior turn and only fire L3 for the new candidate, so L3 cost stays
    # proportional to *new* spans across turns — not the cumulative payload.
    # The added sentence on turn two lies beyond Klaus's context window — Klaus's
    # window is fully contained in turn-one prefix, so its cache key matches.
    turn_one = (
        "Please brief Klaus tomorrow about the new initiative which is launching."
    )
    turn_two = turn_one + " Yasmin asked for the slides."
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)

    detector.detect(turn_one, known_entities=[])
    detector.detect(turn_two, known_entities=[])

    flagged = [call.text for call in adjudicator.calls]
    # "Klaus"'s context is unchanged between turns (window is bounded), so it must
    # NOT trigger a second L3 call on turn two; only "Yasmin" is novel.
    assert flagged.count("Klaus") == 1
    assert flagged.count("Yasmin") == 1


class _UnavailableAdjudicator:
    """Stub for an Ollama outage — every adjudicate() call raises."""

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise ConnectionError("ollama unreachable")


def test_l3_unavailable_blocks_by_default_fail_closed():
    # ADR-0009 / leak-audit clause F: when L3 can't run, fail-closed — block by
    # default rather than letting novel candidates egress unscanned. The detector
    # surfaces this as a typed L3Unavailable error so the proxy can return a clear
    # 503 to the client; silently returning [] would be a privacy regression
    # (a novel candidate would slip through unblindfolded).
    detector = L3Detector(_UnavailableAdjudicator())
    text = "Please brief Klaus tomorrow."

    with pytest.raises(L3Unavailable):
        detector.detect(text, known_entities=[])


def test_l3_unavailable_is_silent_when_no_candidate_spans_exist():
    # Quality: if there's nothing for L3 to adjudicate, an outage is irrelevant —
    # known-entity protection (L1+L2) still works, so we don't manufacture an error
    # for traffic L3 wouldn't have touched anyway. ADR-0009: deterministic L1+L2
    # still protect known entities even when L3 is down.
    detector = L3Detector(_UnavailableAdjudicator())
    text = "Please review the documentation."  # no novel capitalized tokens

    assert detector.detect(text, known_entities=[]) == []


def test_deterministic_only_opt_in_skips_l3_entirely():
    # ADR-0009: the per-workspace opt-in degrades to deterministic-only. With L3
    # skipped, no adjudicator call is made — even when novel candidates exist —
    # and the detector returns no L3 spans. Novelty discovery is lost (the
    # documented trade-off); known-entity protection via L1+L2 still applies.
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator, deterministic_only=True)
    text = "Please brief Klaus tomorrow."

    result = detector.detect(text, known_entities=[])

    assert result == []
    assert adjudicator.calls == []
