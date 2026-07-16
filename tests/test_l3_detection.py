"""L3 detection seam (ADR-0003): local-LLM candidate-span adjudication.

L3 is invoked **only on flagged candidate spans plus minimal context** — never the
full payload (ADR-0003). Cost scales with the number of candidate spans, not payload
size. A content cache prevents re-scanning unchanged chunks across agent turns.

Seam stub: a recording adjudicator stands in for Ollama at its network boundary; the
tests assert what crossed that seam, never internal call shapes (leak-audit).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from blindfold.detection import Entity
from blindfold.engine import blindfold_payload
from blindfold.l3 import (
    CandidateSpan,
    L3Adjudication,
    L3ContentCache,
    L3Detector,
    L3Unavailable,
    _SENTENCE_STOPWORDS,
    select_candidate_spans,
)
from blindfold.surrogates import SurrogateMapping

# Mirrors the shape L3 candidate detection matches on (an initial-cap word) — used
# only to measure the *un-suppressed* flood a fixture would produce, as a baseline
# for the "material drop" assertion below. Not a call into l3.py's internals.
_TITLE_CASE_WORD_RE = re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]+\b")


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


def test_stopwords_cover_closed_class_function_words_beyond_sentence_starters():
    # ADR-0023 "expanded stopwords" layer: the closed-class function-word list
    # (EN+DE) must cover more than sentence-starters — prepositions, auxiliaries,
    # and WH-pronouns are essentially never entity names, so they must never
    # reach L3 candidacy either. The old ~30-token set only had sentence-starters/
    # articles/a few pronouns, so "For", "Is", "Für", and "Ist" were still flagged.
    text = "For the record, Is Klaus still traveling? Für alle: Ist Yasmin da?"

    candidates = select_candidate_spans(text, known_entities=[])

    flagged = sorted(candidate.text for candidate in candidates)
    assert flagged == ["Klaus", "Yasmin"]


def test_registered_entity_colliding_with_a_stopword_is_still_blindfolded():
    # Acceptance criterion (issue #70) / ADR-0023: suppression is token-granularity
    # only and never affects L1/L2 — a registered Term or entity-graph surface
    # always wins over the stopword list. "Will" is a real closed-class entry now
    # (English future auxiliary, German modal "will") but is also a plausible
    # first name; a workspace that has registered "Will" as a known entity must
    # still have it blindfolded on every hop, unaffected by L3 suppression.
    assert "Will" in _SENTENCE_STOPWORDS
    mapping = SurrogateMapping.from_pairs([("Will", "Renate Kestler")])
    payload = {
        "model": "claude-3-5-sonnet",
        "system": "Will is the point of contact for the finance rollout.",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please loop in Will on this thread."}
                ],
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    surrogate = mapping.surrogate_for("Will")
    assert surrogate is not None
    assert "Will" not in blinded["system"]
    assert surrogate in blinded["system"]
    assert "Will" not in blinded["messages"][0]["content"][0]["text"]
    assert surrogate in blinded["messages"][0]["content"][0]["text"]


def test_candidate_span_count_drops_materially_over_a_representative_agentic_system_prompt():
    # Acceptance criterion (issue #70): candidate-span count over a representative
    # agentic system prompt drops materially once L3 stopwords cover the real
    # closed-class function-word list, not just sentence-starters. This is the
    # candidate-span flood ADR-0023 names — instructional prose sprinkled with
    # capitalized sentence-starters and connectives ("Before", "If", "Although",
    # "Since", ...) that used to reach L3 as unknown candidates. The two genuine
    # novel names ("Klaus", "Yasmin") must still surface — suppression is a
    # quality optimisation, never a protection loss.
    fixture = (
        "You are an autonomous coding assistant embedded in the user's editor. "
        "Before you make any changes, you should read the relevant files and "
        "understand what is being asked. If you are unsure about an instruction, "
        "ask for clarification instead of guessing. Do not delete files without "
        "confirmation from the user, and do not commit code that has not been "
        "tested. When you are done with a task, summarize what you changed and "
        "why. Please make sure that Klaus is copied on any message about the "
        "finance rollout, and that Yasmin approves the final release before it "
        "ships. For every request, you must first check whether the change is "
        "within scope, and you should never assume that a prior turn's context "
        "is still valid without verifying it again. Although the environment is "
        "sandboxed, you should still be careful, and although mistakes can be "
        "undone, they cost time. Since the user trusts you with their codebase, "
        "act accordingly."
    )
    raw_capitalized_tokens = _TITLE_CASE_WORD_RE.findall(fixture)

    candidates = select_candidate_spans(fixture, known_entities=[])

    # The flood: 9 capitalized function-word occurrences alongside the 2 real names.
    assert len(raw_capitalized_tokens) == 11
    # Materially fewer candidates reach L3 — only the genuine novel names remain.
    assert sorted(candidate.text for candidate in candidates) == ["Klaus", "Yasmin"]


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


def test_content_cache_is_bounded_and_evicts_the_least_recently_used_entry():
    # ADR-0022: the content cache is a real-value store (keys are un-blindfolded
    # candidate text) so it must be bounded, never allowed to grow without limit for
    # the life of the process. Least-recently-used eviction keeps hot (recurring)
    # candidates cached while a cold one ages out first.
    cache = L3ContentCache(max_entries=2)
    klaus = CandidateSpan(text="Klaus", start=0, end=5, context="ctx-klaus")
    yasmin = CandidateSpan(text="Yasmin", start=0, end=6, context="ctx-yasmin")
    priya = CandidateSpan(text="Priya", start=0, end=5, context="ctx-priya")
    decision = L3Adjudication(is_entity=True)

    cache.put(klaus, decision)
    cache.put(yasmin, decision)
    # Touch klaus so it's the most-recently-used of the two; yasmin becomes the
    # least-recently-used and is the one evicted when the cache is over capacity.
    cache.get(klaus)
    cache.put(priya, decision)

    assert cache.get(klaus) is not None
    assert cache.get(priya) is not None
    assert cache.get(yasmin) is None


def test_l3_dismissal_log_writes_dismissed_token(tmp_path):
    # ADR-0032 / issue #133: when BLINDFOLD_L3_DISMISSAL_LOG is set, a dismissed
    # candidate (is_entity: false) is appended to the file so a curator can review
    # it to seed the allowlist.
    log_path = tmp_path / "dismissals.txt"
    adjudicator = _RecordingAdjudicator({"Klaus": L3Adjudication(is_entity=False)})
    detector = L3Detector(adjudicator, dismissal_log_path=str(log_path))

    detector.detect("Please brief Klaus tomorrow.", known_entities=[])

    assert log_path.read_text(encoding="utf-8") == "Klaus\n"


def test_l3_dismissal_log_dedups_the_same_token_across_contexts_and_turns(tmp_path):
    # ADR-0032 / issue #133 acceptance criterion: the same word dismissed 200 times
    # across one system prompt (here: three occurrences, each in a different context
    # window, across two separate detect() calls/agent turns) writes exactly one
    # line -- dedup is per-token, not per (text, context) cache slot.
    log_path = tmp_path / "dismissals.txt"
    adjudicator = _RecordingAdjudicator({"Klaus": L3Adjudication(is_entity=False)})
    detector = L3Detector(adjudicator, dismissal_log_path=str(log_path))
    turn_one = "Klaus opened the meeting. " * 3 + "Klaus closed it too."

    detector.detect(turn_one, known_entities=[])
    detector.detect(turn_one, known_entities=[])  # agent turn 2, same transcript

    assert len(adjudicator.calls) > 1  # distinct contexts really did fire multiple calls
    assert log_path.read_text(encoding="utf-8") == "Klaus\n"


def test_l3_dismissal_log_unset_creates_no_file(tmp_path):
    # Acceptance criterion (issue #133): BLINDFOLD_L3_DISMISSAL_LOG unset (the
    # dismissal_log_path default) preserves today's exact behavior -- no file
    # created or written, even when candidates are dismissed.
    log_path = tmp_path / "dismissals.txt"
    adjudicator = _RecordingAdjudicator({"Klaus": L3Adjudication(is_entity=False)})
    detector = L3Detector(adjudicator)

    detector.detect("Please brief Klaus tomorrow.", known_entities=[])

    assert not log_path.exists()


def test_l3_dismissal_log_never_logs_context_or_confirmed_candidates(tmp_path):
    # Acceptance criterion (issue #133): only the bare token text is ever logged --
    # never candidate.context, never a confirmed candidate's text (is_entity: true
    # already flows to the review inbox unchanged; it has nowhere to go here).
    log_path = tmp_path / "dismissals.txt"
    adjudicator = _RecordingAdjudicator(
        {"Klaus": L3Adjudication(is_entity=False), "Yasmin": L3Adjudication(is_entity=True)}
    )
    detector = L3Detector(adjudicator, dismissal_log_path=str(log_path))
    text = "the padding is identical and unrelated. " * 3 + "We met Klaus and Yasmin today."

    detector.detect(text, known_entities=[])

    assert log_path.read_text(encoding="utf-8") == "Klaus\n"
    assert "context" not in log_path.read_text(encoding="utf-8")


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
