"""L3 detection seam (ADR-0003): local-LLM candidate-span adjudication.

L3 is invoked **only on flagged candidate spans plus minimal context** — never the
full payload (ADR-0003). Cost scales with the number of candidate spans, not payload
size. A content cache prevents re-scanning unchanged chunks across agent turns.

Seam stub: a recording adjudicator stands in for Ollama at its network boundary; the
tests assert what crossed that seam, never internal call shapes (leak-audit).
"""

from __future__ import annotations

import logging
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
    count_capitalized_tokens,
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


def test_context_offset_locates_the_correct_occurrence_when_the_token_repeats():
    # ADR-0035 (issue #155): the review inbox highlights the candidate span in
    # place within its context window. That requires the offset to be derived
    # from the candidate's own positional span, not a fragile text search --
    # a search would always land on the first occurrence, which is wrong when
    # the same token repeats in the context window.
    text = "Please tell Klaus that Klaus will call back tomorrow for review notes."

    candidates = select_candidate_spans(text, known_entities=[])

    klaus_candidates = [c for c in candidates if c.text == "Klaus"]
    assert len(klaus_candidates) == 2
    first, second = klaus_candidates
    for candidate in (first, second):
        offset = candidate.context_offset
        assert candidate.context[offset : offset + len(candidate.text)] == "Klaus"
    # Proof it's the *correct* occurrence, not just the first match: the text
    # immediately preceding each highlighted span differs between the two.
    assert first.context[: first.context_offset].endswith("tell ")
    assert second.context[: second.context_offset].endswith("that ")


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


def test_positional_case_heuristic_suppresses_sentence_start_capitalization_noise():
    # ADR-0033: a capitalized token with both vocabulary evidence (its lowercase
    # form appears as a standalone word elsewhere in the hop) and positional
    # evidence (it is capitalized only at sentence start, never mid-sentence) is
    # noise from English sentence-initial capitalization, not a candidate span.
    # "Assist" and "Refuse" only ever appear capitalized at a sentence start here,
    # and their lowercase forms appear later in the same hop.
    text = (
        "Assist the user with their request. Refuse anything that looks unsafe. "
        "You must always assist promptly and never refuse without good cause."
    )

    candidates = select_candidate_spans(text, known_entities=[])

    assert [candidate.text for candidate in candidates] == []


def test_positional_case_heuristic_does_not_suppress_a_name_capitalized_mid_sentence():
    # ADR-0033 §1: vocabulary evidence alone eats real names -- "mark this as
    # done" gives "Mark" the person vocabulary evidence too. The positional gate
    # is the safety net: "Mark" is capitalized mid-sentence ("The lawyer said
    # Mark signed the contract"), which fails positional evidence, so it must
    # stay a candidate span even though "mark" appears lowercase elsewhere.
    text = (
        "Please mark this task as done once you are finished. "
        "The lawyer said Mark signed the contract yesterday."
    )

    candidates = select_candidate_spans(text, known_entities=[])

    assert [candidate.text for candidate in candidates] == ["Mark"]


def test_positional_case_heuristic_is_neutral_for_german_common_nouns():
    # ADR-0033 §1: German capitalizes all nouns mid-sentence, so vocabulary
    # evidence (a) rarely fires -- there is no lowercase "tisch"/"arbeit" form in
    # normal German prose to match against. The heuristic must not suppress
    # German common nouns, even ones that happen to sit at a sentence start.
    text = (
        "Der Tisch steht im Büro. Die Arbeit beginnt morgen früh, und der Tisch "
        "wird dafür gebraucht."
    )

    candidates = select_candidate_spans(text, known_entities=[])

    flagged = {candidate.text for candidate in candidates}
    assert "Tisch" in flagged
    assert "Arbeit" in flagged


def test_positional_case_heuristic_covers_bullet_and_heading_starts():
    # ADR-0033: the ~96% noise class the dismissal log (ADR-0032) surfaced was an
    # agentic system prompt's instruction list -- capitalized verbs at the start
    # of bullet lines, not just sentence starts. "Note" and "Build" sit at bullet
    # starts here, and their lowercase forms appear later in the same hop. "Rules"
    # never recurs lowercase, so it lacks vocabulary evidence and stays a
    # candidate — proof the heuristic only fires when both conditions hold.
    text = (
        "Rules:\n"
        "- Note the user's intent before acting.\n"
        "- Build the change incrementally.\n"
        "Always note edge cases and build tests as you go."
    )

    candidates = select_candidate_spans(text, known_entities=[])

    assert [candidate.text for candidate in candidates] == ["Rules"]


def test_positional_case_heuristic_covers_bullet_with_bold_label_start():
    # Issue #141 (ADR-0033 gap): Claude Code's system prompt structures bullets as
    # bold labels -- "- **Assist**: ..." -- not bare "- Assist: ...". The bullet
    # marker and the bold marker are separated by a space, which the original
    # _POSITION_START_RE (one contiguous marker group) failed to recognise as
    # sentence-initial. "Assist" and "Refuse" sit exclusively at bullet+bold-label
    # starts here, and their lowercase forms recur later in the same hop.
    text = (
        "- **Assist** the user with their request.\n"
        "- **Refuse** anything that looks unsafe.\n"
        "You must always assist promptly and never refuse without good cause."
    )

    candidates = select_candidate_spans(text, known_entities=[])

    assert [candidate.text for candidate in candidates] == []


def test_positional_case_heuristic_covers_numbered_and_underscore_bold_label_starts():
    # Issue #141: the same bullet+bold-label gap applies to numbered lists
    # ("1. **Sending**: ...") and underscore-style bold labels ("__Storing__:
    # ..."), both cited in the issue's dismissal-log examples. Both sit
    # exclusively at marker starts here, with lowercase forms recurring later.
    text = (
        "1. **Sending** the request to the queue.\n"
        "2. __Storing__ the response for later.\n"
        "Always finish sending before storing the result."
    )

    candidates = select_candidate_spans(text, known_entities=[])

    assert [candidate.text for candidate in candidates] == []


def test_positional_case_heuristic_does_not_suppress_inline_bold_mid_sentence():
    # Issue #141 fail-closed invariant: a bold span mid-sentence (not at a line
    # start) must never be treated as sentence-initial just because it is
    # wrapped in "**...**" -- only a bold label that itself begins the line
    # counts as positional evidence. "Mark" here is inline bold mid-sentence,
    # so it must stay a candidate even though "mark" recurs lowercase.
    text = (
        "Please mark this task as done once you are finished. "
        "The lawyer said **Mark** signed the contract yesterday."
    )

    candidates = select_candidate_spans(text, known_entities=[])

    assert [candidate.text for candidate in candidates] == ["Mark"]


def test_positional_case_heuristic_live_regression_agentic_system_prompt_shape():
    # Issue #141 acceptance criterion: the live-test 2026-07-17 dismissal log
    # against a Claude Code system-prompt-shaped hop. "Assist", "Refuse",
    # "Sending", "Storing" sit exclusively at bullet/heading/bold-label starts
    # and recur lowercase -- they must no longer reach L3 candidacy. "Darwin"
    # (a proper noun, no lowercase recurrence) and "January" (mid-sentence
    # capitalization) must continue to be flagged -- fail-closed is unaffected.
    text = (
        "## Behavior\n"
        "- **Assist** the user with their request.\n"
        "- **Refuse** anything that looks unsafe.\n"
        "\n"
        "**Sending:** outbound data is scrubbed first.\n"
        "**Storing:** responses are cached locally.\n"
        "\n"
        "You must always assist promptly, never refuse without good cause, "
        "and keep sending and storing scoped to this session. "
        "The project lead said Darwin approved the plan starting in January."
    )

    candidates = select_candidate_spans(text, known_entities=[])

    flagged = {candidate.text for candidate in candidates}
    assert flagged == {"Behavior", "Darwin", "January"}


def test_registered_entity_colliding_with_positional_case_noise_is_still_blindfolded():
    # Leak-audit / ADR-0033: suppression removes L3 novelty discovery only, never
    # L1/L2 protection. "Build" is positional-case noise here (sentence-start
    # only, "build" also appears lowercase) — but a workspace that has registered
    # "Build" as a known entity must still have every occurrence blindfolded,
    # unaffected by the L3 candidate-span heuristic.
    mapping = SurrogateMapping.from_pairs([("Build", "Projekt Nachtwind")])
    payload = {
        "model": "claude-3-5-sonnet",
        "system": "Build the release before Friday.",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Please build the release now. Build is on the "
                            "critical path this week."
                        ),
                    }
                ],
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    surrogate = mapping.surrogate_for("Build")
    assert surrogate is not None
    assert "Build" not in blinded["system"]
    assert surrogate in blinded["system"]
    message_text = blinded["messages"][0]["content"][0]["text"]
    assert "build" not in message_text.lower()
    assert message_text.count(surrogate) == 2


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


def test_l3_detect_logs_periodic_progress_during_a_long_pass(caplog):
    # Issue #134: a request with many L3 candidates (the live-testing report cited
    # 250+ sequential adjudication calls against a cold allowlist) gave an operator
    # tailing logs no signal of forward progress until the whole pass completed.
    # detect() now surfaces periodic progress -- candidates processed so far this
    # request, elapsed wall-clock since the pass started -- at a fixed interval, so
    # an operator watching logs sees it update mid-pass rather than only at the end.
    nato = (
        "Alfa Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliett Kilo Lima"
    )
    text = " and ".join(nato.split())
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator, progress_log_interval=5)

    with caplog.at_level(logging.INFO, logger="blindfold.l3"):
        result = detector.detect(text, known_entities=[])

    # 12 candidates at an interval of 5 -> progress logged after the 5th and 10th,
    # never a partial multiple, and never after the last (completion isn't "progress").
    progress_records = [
        record for record in caplog.records if "l3_detect_progress" in record.getMessage()
    ]
    assert len(progress_records) == 2
    assert "candidates_processed=5" in progress_records[0].getMessage()
    assert "candidates_processed=10" in progress_records[1].getMessage()
    assert "elapsed" in progress_records[0].getMessage()
    # Observability-only: the L3 verdicts themselves are unaffected.
    assert len(result) == 12


@dataclass
class _BatchCall:
    texts: tuple[str, ...]


class _RecordingBatchAdjudicator:
    """Stub for a batch-capable provider (issue #142) — records every
    adjudicate_batch() call (candidates it received, in order) without firing real
    I/O, and never exposes adjudicate() as a single-candidate fallback path.
    """

    def __init__(
        self, decisions: dict[str, L3Adjudication] | None = None
    ) -> None:
        self.batch_calls: list[_BatchCall] = []
        self._decisions = decisions or {}

    def adjudicate_batch(
        self, candidates: list[CandidateSpan]
    ) -> list[L3Adjudication]:
        self.batch_calls.append(_BatchCall(texts=tuple(c.text for c in candidates)))
        return [
            self._decisions.get(c.text, L3Adjudication(is_entity=False))
            for c in candidates
        ]


def test_batch_adjudication_sends_one_call_for_a_batch_and_maps_results_by_position():
    # Issue #142: a batch-capable adjudicator gets ONE call listing every candidate
    # in the batch, and the returned verdicts map back to N L3Adjudication results
    # in the same order as the candidates were selected — not N separate calls.
    text = "We met Klaus, Yasmin, and Priya at the offsite."
    adjudicator = _RecordingBatchAdjudicator({"Klaus": L3Adjudication(is_entity=True)})
    detector = L3Detector(adjudicator, batch_size=5)

    results = detector.detect(text, known_entities=[])

    # A single round trip carried all three candidates (the round-trip reduction
    # this issue exists for), not one call per candidate.
    assert len(adjudicator.batch_calls) == 1
    assert adjudicator.batch_calls[0].texts == ("Klaus", "Yasmin", "Priya")
    assert len(results) == 3
    by_text = {candidate.text: decision for candidate, decision in results}
    assert by_text["Klaus"].is_entity is True
    assert by_text["Yasmin"].is_entity is False
    assert by_text["Priya"].is_entity is False


def test_batch_adjudication_splits_into_multiple_calls_above_batch_size():
    # Issue #142: batch_size bounds each individual call (the issue's own accuracy
    # note — a batch loses per-span focus as N grows) rather than sending every
    # candidate in one unbounded call. 7 candidates at batch_size=3 must split into
    # 3 calls of sizes 3, 3, 1 — never one call of 7, never 7 calls of 1.
    nato = "Alfa Bravo Charlie Delta Echo Foxtrot Golf"
    text = " and ".join(nato.split())
    adjudicator = _RecordingBatchAdjudicator()
    detector = L3Detector(adjudicator, batch_size=3)

    results = detector.detect(text, known_entities=[])

    assert [len(call.texts) for call in adjudicator.batch_calls] == [3, 3, 1]
    assert len(results) == 7
    assert sorted(t for call in adjudicator.batch_calls for t in call.texts) == sorted(
        nato.split()
    )


def test_batch_adjudication_cache_hits_bypass_the_batch_call_entirely():
    # ADR-0003 content cache is unaffected by batching (issue #142's own framing):
    # a candidate already cached from a prior turn must not be re-sent in a batch
    # call — only genuinely novel (span, context) pairs go to the adjudicator, in
    # batch mode exactly as in the single-candidate path.
    text = "Please brief Klaus tomorrow about Yasmin's initiative."
    adjudicator = _RecordingBatchAdjudicator({"Klaus": L3Adjudication(is_entity=True)})
    detector = L3Detector(adjudicator, batch_size=5)

    detector.detect(text, known_entities=[])  # turn one: both candidates novel
    calls_after_turn_one = len(adjudicator.batch_calls)
    results_turn_two = detector.detect(text, known_entities=[])  # turn two: unchanged

    assert calls_after_turn_one == 1
    assert adjudicator.batch_calls[0].texts == ("Klaus", "Yasmin")
    # Turn two is served entirely from cache — no second batch call at all.
    assert len(adjudicator.batch_calls) == 1
    by_text = {candidate.text: decision for candidate, decision in results_turn_two}
    assert by_text["Klaus"].is_entity is True
    assert by_text["Yasmin"].is_entity is False


def test_batch_adjudication_only_cache_misses_are_sent_in_the_batch():
    # Subtler cache/batch interaction: within a single detect() call, a mix of
    # cache hits and cache misses must only send the misses to adjudicate_batch —
    # a hit is resolved from the cache and never occupies a batch slot. Mirrors
    # test_content_cache_only_re_scans_spans_whose_context_changed's fixture:
    # "Klaus"'s context window is fully contained in turn one, so it's unchanged
    # (and thus a cache hit) on turn two; only the newly-appended "Yasmin" misses.
    turn_one = (
        "Please brief Klaus tomorrow about the new initiative which is launching."
    )
    turn_two = turn_one + " Yasmin asked for the slides."
    warm_adjudicator = _RecordingBatchAdjudicator({"Klaus": L3Adjudication(is_entity=True)})
    shared_cache = L3ContentCache()
    warm_detector = L3Detector(warm_adjudicator, cache=shared_cache, batch_size=5)
    warm_detector.detect(turn_one, known_entities=[])

    adjudicator = _RecordingBatchAdjudicator()
    detector = L3Detector(adjudicator, cache=shared_cache, batch_size=5)

    detector.detect(turn_two, known_entities=[])

    assert len(adjudicator.batch_calls) == 1
    assert adjudicator.batch_calls[0].texts == ("Yasmin",)


class _ShortResponseBatchAdjudicator:
    """Stub for a malformed/short batch response (issue #142): returns fewer
    verdicts than candidates it was handed, as a real LLM might on a truncated or
    malformed JSON array.
    """

    def __init__(self, verdict_count: int) -> None:
        self._verdict_count = verdict_count

    def adjudicate_batch(
        self, candidates: list[CandidateSpan]
    ) -> list[L3Adjudication]:
        return [L3Adjudication(is_entity=False)] * self._verdict_count


class _RetryCapableShortResponseBatchAdjudicator:
    """Stub for the real-world shape (issue #148, #142 regression): a provider
    that under-returns on the batch call — exactly like a weak local model that
    ignores the numbered-list instruction — but, unlike
    ``_ShortResponseBatchAdjudicator``, also implements the plain single-
    candidate ``adjudicate()`` the same provider classes (Ollama, oMLX) always
    ship. L3Detector should recover the shortfall through that already-reliable
    seam instead of immediately over-redacting.
    """

    def __init__(self, verdict_count: int, single_decisions: dict[str, L3Adjudication]) -> None:
        self._verdict_count = verdict_count
        self._single_decisions = single_decisions
        self.single_calls: list[str] = []

    def adjudicate_batch(
        self, candidates: list[CandidateSpan]
    ) -> list[L3Adjudication]:
        return [L3Adjudication(is_entity=False)] * self._verdict_count

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.single_calls.append(candidate.text)
        return self._single_decisions[candidate.text]


def test_batch_short_response_recovers_missing_verdicts_via_single_candidate_retry(caplog):
    # Issue #148 (#142 regression): live testing showed batch calls almost always
    # under-return. Root cause is the prompt/format (see
    # test_build_batch_prompt_states_the_exact_expected_verdict_count), not the
    # parser -- but a genuinely short response still needs handling. Rather than
    # immediately over-redacting every missing candidate, L3Detector retries the
    # shortfall through the adjudicator's plain single-candidate adjudicate()
    # seam (the reliable, already-battle-tested path predating batching) before
    # falling back to the fail-closed pad. A full recovery is not a "genuine"
    # model shortfall, so no warning fires.
    text = "We met Klaus, Yasmin, and Priya at the offsite."
    adjudicator = _RetryCapableShortResponseBatchAdjudicator(
        verdict_count=2,
        single_decisions={"Priya": L3Adjudication(is_entity=True)},
    )
    detector = L3Detector(adjudicator, batch_size=5)

    with caplog.at_level(logging.WARNING, logger="blindfold.l3"):
        results = detector.detect(text, known_entities=[])

    by_text = {candidate.text: decision for candidate, decision in results}
    assert len(by_text) == 3
    assert by_text["Klaus"].is_entity is False
    assert by_text["Yasmin"].is_entity is False
    # Recovered via retry, not the fail-closed pad -- reflects the real verdict.
    assert by_text["Priya"].is_entity is True
    assert adjudicator.single_calls == ["Priya"]

    # Fully recovered -- not a "genuine" model shortfall, so no warning.
    mismatch_records = [
        r for r in caplog.records if "l3_batch_adjudication_short_response" in r.getMessage()
    ]
    assert mismatch_records == []


class _RetryFailsBatchAdjudicator:
    """Stub for the genuine-shortfall case (issue #148): both the batch call AND
    the single-candidate retry come up short for the same candidate (e.g. the
    same outage/flake truncated both). This must still fail closed and still
    warn — the retry is a best-effort recovery, not a second silent-drop risk.
    """

    def __init__(self, verdict_count: int) -> None:
        self._verdict_count = verdict_count

    def adjudicate_batch(
        self, candidates: list[CandidateSpan]
    ) -> list[L3Adjudication]:
        return [L3Adjudication(is_entity=False)] * self._verdict_count

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise RuntimeError("local daemon flaked on the retry too")


def test_batch_short_response_retry_failure_still_fails_closed_and_warns(caplog):
    # Issue #148: when the per-candidate retry (test_batch_short_response_
    # recovers_missing_verdicts_via_single_candidate_retry) is ALSO unable to
    # resolve a candidate, this is a genuine model/daemon shortfall, not a
    # transient batch-format hiccup — the existing #142 fail-closed contract
    # (over-redact, warn with scrubbed counts) still applies unchanged.
    text = "We met Klaus, Yasmin, and Priya at the offsite."
    adjudicator = _RetryFailsBatchAdjudicator(verdict_count=2)
    detector = L3Detector(adjudicator, batch_size=5)

    with caplog.at_level(logging.WARNING, logger="blindfold.l3"):
        results = detector.detect(text, known_entities=[])

    by_text = {candidate.text: decision for candidate, decision in results}
    assert len(by_text) == 3
    assert by_text["Priya"].is_entity is True  # fail-closed: retry also failed

    mismatch_records = [
        r for r in caplog.records if "l3_batch_adjudication_short_response" in r.getMessage()
    ]
    assert len(mismatch_records) == 1
    message = mismatch_records[0].getMessage()
    assert "expected=3" in message
    assert "received=2" in message
    assert "missing=1" in message
    for token in ("Klaus", "Yasmin", "Priya"):
        assert token not in message


def test_batch_short_response_treats_missing_candidates_as_is_entity_true(caplog):
    # Issue #142 fail-closed contract: a short/malformed batch response (fewer
    # verdicts than candidates) must NOT silently dismiss the unadjudicated
    # candidates. The two verdicts present are honored (both false here); the
    # third candidate, missing a verdict entirely, is over-redacted
    # (is_entity=True) rather than risking an unresolved real entity slipping
    # through as if it had been reviewed and cleared.
    text = "We met Klaus, Yasmin, and Priya at the offsite."
    adjudicator = _ShortResponseBatchAdjudicator(verdict_count=2)
    detector = L3Detector(adjudicator, batch_size=5)

    with caplog.at_level(logging.WARNING, logger="blindfold.l3"):
        results = detector.detect(text, known_entities=[])

    by_text = {candidate.text: decision for candidate, decision in results}
    assert len(by_text) == 3
    assert by_text["Klaus"].is_entity is False
    assert by_text["Yasmin"].is_entity is False
    assert by_text["Priya"].is_entity is True  # fail-closed: no verdict returned

    # Scrubbed: the mismatch is logged with candidate counts only, never the
    # candidate text itself (leak-audit — this is still real, un-blindfolded text).
    mismatch_records = [
        r for r in caplog.records if "l3_batch_adjudication_short_response" in r.getMessage()
    ]
    assert len(mismatch_records) == 1
    message = mismatch_records[0].getMessage()
    assert "expected=3" in message
    assert "received=2" in message
    assert "missing=1" in message
    for token in ("Klaus", "Yasmin", "Priya"):
        assert token not in message


def test_batch_short_response_still_caches_the_fail_closed_verdict():
    # The fail-closed is_entity=True verdict for a missing candidate is cached
    # like any other decision — a subsequent turn with the same (span, context)
    # doesn't re-adjudicate it (ADR-0003), and doesn't need a fresh short response
    # to stay protected.
    text = "We met Priya at the offsite."
    detector = L3Detector(_ShortResponseBatchAdjudicator(verdict_count=0), batch_size=5)

    first = detector.detect(text, known_entities=[])
    second = detector.detect(text, known_entities=[])

    assert dict(first)[first[0][0]].is_entity is True
    assert dict(second)[second[0][0]].is_entity is True


def test_batch_adjudication_cuts_round_trips_5x_at_default_batch_size_over_100_candidates():
    # Acceptance criterion (issue #142): "latency for a 100-candidate pass is lower
    # than sequential baseline at batch size 5." HTTP round-trips (not wall-clock,
    # which a unit test can't measure meaningfully against a stub) is the cost this
    # issue set out to cut -- ADR-0022's own deferred-followup framing ("the HTTP
    # round-trip overhead ... is paid separately for every candidate"). A single-
    # candidate adjudicator pays exactly 100 round-trips for 100 candidates; the
    # default batch size (5) cuts that to 20 -- a 5x reduction, documented here as
    # the before/after figure for the PR.
    nato_words = [
        "Alfa", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
        "India", "Juliett", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
        "Quebec", "Romeo", "Sierra", "Tango",
    ]
    # A hyphen suffix keeps each occurrence's (text, context) content-cache key
    # unique (the candidate span itself is still the bare word, e.g. "Alfa" — the
    # regex stops at the non-word hyphen) so all 100 occurrences are genuine cache
    # misses, not artificially deduped within a single detect() pass.
    words = [f"{w}-{i}" for i in range(5) for w in nato_words]  # 100 occurrences
    assert len(words) == 100
    text = " and ".join(words)

    sequential_adjudicator = _RecordingAdjudicator()
    L3Detector(sequential_adjudicator).detect(text, known_entities=[])

    batch_adjudicator = _RecordingBatchAdjudicator()
    L3Detector(batch_adjudicator, batch_size=5).detect(text, known_entities=[])

    assert len(sequential_adjudicator.calls) == 100
    assert len(batch_adjudicator.batch_calls) == 20
    assert len(sequential_adjudicator.calls) == 5 * len(batch_adjudicator.batch_calls)


class _PartlyShortRetryCapableBatchAdjudicator:
    """Records both batch calls and single-candidate retry calls, and under-
    returns by a fixed amount on every batch — the representative live-traffic
    shape from issue #148's report (short, but not empty, most batches).
    """

    def __init__(self, shortfall_per_batch: int) -> None:
        self._shortfall_per_batch = shortfall_per_batch
        self.batch_calls: list[int] = []
        self.single_calls: list[str] = []

    def adjudicate_batch(
        self, candidates: list[CandidateSpan]
    ) -> list[L3Adjudication]:
        self.batch_calls.append(len(candidates))
        verdict_count = max(0, len(candidates) - self._shortfall_per_batch)
        return [L3Adjudication(is_entity=False)] * verdict_count

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.single_calls.append(candidate.text)
        return L3Adjudication(is_entity=False)


def test_batch_adjudication_still_cuts_inner_llm_calls_when_batches_under_return():
    # Issue #148 acceptance criterion: batching must still measurably reduce
    # inner-LLM call count vs per-candidate even on a representative batch where
    # most batch calls under-return by a small amount (the live-testing shape) —
    # the retry-recovery this issue adds (test_batch_short_response_recovers_
    # missing_verdicts_via_single_candidate_retry) trades one extra call per
    # missing candidate, not a full per-candidate fallback for the whole batch.
    nato_words = [
        "Alfa", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
        "India", "Juliett", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
        "Quebec", "Romeo", "Sierra", "Tango",
    ]
    words = [f"{w}-{i}" for i in range(5) for w in nato_words]  # 100 occurrences
    text = " and ".join(words)

    # Every 5-candidate batch under-returns by 1 (4 of 5), the report's "off by
    # a little" case -- each shortfall is recovered by exactly one retry call.
    adjudicator = _PartlyShortRetryCapableBatchAdjudicator(shortfall_per_batch=1)
    L3Detector(adjudicator, batch_size=5).detect(text, known_entities=[])

    total_calls = len(adjudicator.batch_calls) + len(adjudicator.single_calls)
    assert len(adjudicator.batch_calls) == 20
    assert len(adjudicator.single_calls) == 20  # one retry per under-returned batch
    assert total_calls == 40
    # Still well under the 100 calls a fully per-candidate pass would cost.
    assert total_calls < 100


def test_count_capitalized_tokens_counts_every_raw_capitalized_occurrence():
    # Issue #153: the processing trace's per-hop "suppressed" count is derived as
    # (raw capitalized tokens) - (candidates actually handed to L3), so this raw
    # count must include tokens select_candidate_spans() would later filter out
    # (stopwords, known entities, declared tools, positional-case noise) --
    # otherwise "suppressed" would always read zero.
    text = "Please brief Klaus and Petra tomorrow. Petra agreed."

    assert count_capitalized_tokens(text) == 4  # Please, Klaus, Petra, Petra


def test_l3_detector_provider_name_defaults_to_ollama():
    # Issue #153: the processing trace's L3 column needs a provider label even
    # for existing callers that construct L3Detector without naming one --
    # "ollama" reproduces today's only production default (config.DEFAULT_L3_PROVIDER).
    detector = L3Detector(_RecordingAdjudicator())

    assert detector.provider_name == "ollama"


def test_l3_detector_provider_name_reflects_configured_provider():
    detector = L3Detector(_RecordingAdjudicator(), provider_name="omlx")

    assert detector.provider_name == "omlx"
