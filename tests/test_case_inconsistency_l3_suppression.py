"""Case-inconsistency suppression (ADR-0023, "Update (issue #342)"): the fifth L3
suppression layer. Run 10 measured 14 of 21 false-positive mints sharing a
lowercase prose occurrence of their own capitalized form in the same payload
(`Pass`/`pass`, `Both`/`both`, ...) at a cost of 0 of 6 genuine referents -- but
every planted name in the #74 brief is a deliberately novel non-dictionary word,
so run 10 cannot choose an aggressiveness threshold. Issue #344 builds both
candidate thresholds behind a parameter, **default off**, plus the dictionary-
word fixture (test_case_inconsistency_dictionary_word_fixture.py) that does.

This file covers the condition itself: :class:`CaseInconsistencyEvidence`'s two
threshold rules and the conjunctive, run-granular mechanics in
:func:`select_candidate_spans` / :meth:`L3Detector.detect`. The prose-only
extraction exclusion (email/URL/dotted-hyphenated identifiers) and the app-
boundary extractors live in test_case_inconsistency_evidence_extraction.py --
this file constructs :class:`CaseInconsistencyEvidence` directly, exercising the
suppression mechanics independent of how the evidence was gathered.

Leak-audit clauses for this slice:
- A: N/A directly for a suppressed token itself -- but reproven for the
  co-occurring case: a registered Term/L1-PII value sharing a suppressed
  token's payload is still blindfolded (L1/L2 win over suppression).
- F: an unrelated genuine novel candidate with no case-inconsistency evidence
  still reaches L3 in the same traffic -- suppression is token-run-scoped,
  never a blanket payload skip.
- B/C/D/E/G: N/A -- no restore, mapping, or store change this slice; the
  condition ships default off, so no production behavior changes either.
"""

from __future__ import annotations

from blindfold.engine import (
    blindfold_chat_completions_payload,
    blindfold_payload,
    extract_case_inconsistency_evidence_chat_completions,
    extract_case_inconsistency_evidence_messages,
)
from blindfold.l3 import (
    CandidateSpan,
    CaseInconsistencyEvidence,
    CaseInconsistencySuppression,
    L3Adjudication,
    L3Detector,
    select_candidate_spans,
)
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _RecordingAdjudicator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        return L3Adjudication(is_entity=False)


def test_default_off_reproduces_todays_candidate_selection():
    text = "Pass this along to Mark Stone before the meeting."

    candidates = select_candidate_spans(text, known_entities=[])

    assert {c.text for c in candidates} == {"Pass", "Mark", "Stone"}


def test_bare_presence_suppresses_a_single_word_candidate_with_any_lowercase_evidence():
    text = "Pass the review; pass rate matters here."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"pass": 1}, capitalized_counts={"pass": 1}
    )
    suppression = CaseInconsistencySuppression(
        evidence=evidence, threshold="bare_presence"
    )

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert not any(c.text == "Pass" for c in candidates)


def test_bare_presence_leaves_a_candidate_with_no_lowercase_evidence_untouched():
    text = "Please brief Quentin tomorrow."
    evidence = CaseInconsistencyEvidence(lowercase_counts={}, capitalized_counts={})
    suppression = CaseInconsistencySuppression(
        evidence=evidence, threshold="bare_presence"
    )

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert any(c.text == "Quentin" for c in candidates)


def test_proportionate_evidence_requires_lowercase_to_outnumber_capitalized():
    text = "Mark signed off; Mark Mark Mark reviewed it too."
    # "mark" occurs lowercase once but "Mark" is capitalized three times --
    # lowercase does not dominate, so proportionate evidence must not suppress.
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"mark": 1}, capitalized_counts={"mark": 3}
    )
    suppression = CaseInconsistencySuppression(
        evidence=evidence, threshold="proportionate_evidence"
    )

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert any(c.text == "Mark" for c in candidates)


def test_proportionate_evidence_suppresses_when_lowercase_dominates():
    text = "Pass this test."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"pass": 5}, capitalized_counts={"pass": 1}
    )
    suppression = CaseInconsistencySuppression(
        evidence=evidence, threshold="proportionate_evidence"
    )

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert not any(c.text == "Pass" for c in candidates)


def test_bare_presence_and_proportionate_diverge_on_incidental_evidence():
    # The exact case ADR-0023 says the two thresholds diverge on: one
    # incidental lowercase occurrence of an ordinary-word real name amid many
    # capitalized ones. Bare presence suppresses (loses the referent);
    # proportionate evidence does not.
    text = "Mark Stone is the client contact."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"mark": 1, "stone": 1},
        capitalized_counts={"mark": 4, "stone": 4},
    )

    bare = select_candidate_spans(
        text,
        known_entities=[],
        case_inconsistency=CaseInconsistencySuppression(
            evidence=evidence, threshold="bare_presence"
        ),
    )
    proportionate = select_candidate_spans(
        text,
        known_entities=[],
        case_inconsistency=CaseInconsistencySuppression(
            evidence=evidence, threshold="proportionate_evidence"
        ),
    )

    assert {c.text for c in bare} == set()
    assert {c.text for c in proportionate} == {"Mark", "Stone"}


def test_conjunctive_rule_protects_a_multiword_candidate_missing_one_tokens_evidence():
    # ADR-0023's own pinned case: "Project Halyard" must not be suppressed on
    # the strength of lowercase "project" alone -- "halyard" has no evidence.
    text = "Project Halyard is the codename for this engagement."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"project": 1}, capitalized_counts={"project": 1, "halyard": 1}
    )
    suppression = CaseInconsistencySuppression(
        evidence=evidence, threshold="bare_presence"
    )

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert {c.text for c in candidates} == {"Project", "Halyard"}


def test_conjunctive_rule_suppresses_whole_run_when_every_token_has_evidence():
    text = "Northern Data handles this account."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"northern": 1, "data": 1},
        capitalized_counts={"northern": 1, "data": 1},
    )
    suppression = CaseInconsistencySuppression(
        evidence=evidence, threshold="bare_presence"
    )

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert {c.text for c in candidates} == set()


def test_l3_detector_detect_threads_case_inconsistency_through_to_candidacy():
    text = "Pass the review to Zolfgang."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"pass": 1}, capitalized_counts={"pass": 1}
    )
    suppression = CaseInconsistencySuppression(
        evidence=evidence, threshold="bare_presence"
    )
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)

    detector.detect(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert "Pass" not in adjudicator.calls
    assert "Zolfgang" in adjudicator.calls


def test_unknown_threshold_raises():
    evidence = CaseInconsistencyEvidence(lowercase_counts={"pass": 1})
    import pytest

    with pytest.raises(ValueError):
        evidence.has_evidence("Pass", "made_up_threshold")


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([])


def test_blindfold_payload_default_off_reproduces_todays_candidate_selection():
    # Acceptance criterion: the condition is default off -- candidate
    # selection through the full request path is unchanged with no
    # ``case_inconsistency`` argument at all, even though the payload itself
    # carries plenty of lowercase evidence for "Pass".
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "system": "Please pass the review along; pass rate matters.",
        "messages": [{"role": "user", "content": "Pass this to the team."}],
    }

    blindfold_payload(payload, mapping, detector, inbox)

    assert "Pass" in adjudicator.calls


def test_blindfold_payload_suppresses_case_inconsistent_token_when_opted_in():
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "system": "Please pass the review along; pass rate matters.",
        "messages": [{"role": "user", "content": "Pass this to the team."}],
    }
    evidence = extract_case_inconsistency_evidence_messages(payload)
    suppression = CaseInconsistencySuppression(
        evidence=evidence, threshold="bare_presence"
    )

    blindfold_payload(payload, mapping, detector, inbox, case_inconsistency=suppression)

    assert "Pass" not in adjudicator.calls


def test_blindfold_chat_completions_payload_default_off_reproduces_todays_candidate_selection():
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "Please pass the review along."},
            {"role": "user", "content": "Pass this to the team."},
        ],
    }

    blindfold_chat_completions_payload(payload, mapping, detector, inbox)

    assert "Pass" in adjudicator.calls


def test_blindfold_chat_completions_payload_suppresses_case_inconsistent_token_when_opted_in():
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "Please pass the review along."},
            {"role": "user", "content": "Pass this to the team."},
        ],
    }
    evidence = extract_case_inconsistency_evidence_chat_completions(payload)
    suppression = CaseInconsistencySuppression(
        evidence=evidence, threshold="bare_presence"
    )

    blindfold_chat_completions_payload(
        payload, mapping, detector, inbox, case_inconsistency=suppression
    )

    assert "Pass" not in adjudicator.calls


def test_registered_term_with_case_inconsistency_evidence_is_still_blindfolded():
    # Protection wins over suppression: a registered Term sharing its
    # lowercase form with prose elsewhere in the payload is still blindfolded
    # by L2, unaffected by this suppression layer.
    mapping = SurrogateMapping.from_pairs([("Pass", "Northern Vault")])
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "system": "Please pass this along.",
        "messages": [{"role": "user", "content": "Pass handles the credential."}],
    }
    evidence = extract_case_inconsistency_evidence_messages(payload)
    suppression = CaseInconsistencySuppression(
        evidence=evidence, threshold="bare_presence"
    )

    blinded, _session = blindfold_payload(
        payload, mapping, detector, inbox, case_inconsistency=suppression
    )

    assert "Pass" not in blinded["messages"][0]["content"]
    surrogate = mapping.surrogate_for("Pass")
    assert surrogate is not None
    assert surrogate in blinded["messages"][0]["content"]
