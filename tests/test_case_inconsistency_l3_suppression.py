"""Case-inconsistency suppression (ADR-0023, "Update (issue #342)"): the fifth L3
suppression layer. Run 10 measured 14 of 21 false-positive mints sharing a
lowercase prose occurrence of their own capitalized form in the same payload
(`Pass`/`pass`, `Both`/`both`, ...) at a cost of 0 of 6 genuine referents.
Issue #344 built two candidate aggressiveness rules behind a parameter, default
off, plus the dictionary-word fixture (test_case_inconsistency_dictionary_word_
fixture.py) needed to choose between them because every planted name in the
#74 brief is a deliberately novel non-dictionary word. That fixture answered
it -- proportionate evidence keeps both dictionary-word referents while still
suppressing every false-positive shape -- so issue #345 makes proportionate
evidence the only rule and turns the condition on.

This file covers the condition itself: :class:`CaseInconsistencyEvidence`'s
proportionate-evidence rule and the conjunctive, run-granular mechanics in
:func:`select_candidate_spans` / :meth:`L3Detector.detect`. The prose-only
extraction exclusion (email/URL/dotted-hyphenated identifiers) and the app-
boundary extractors live in test_case_inconsistency_evidence_extraction.py;
the app-boundary wiring that makes this condition on by default for real
traffic lives in test_system_confined_l3_suppression.py's sibling integration
pattern, exercised here in the ``/v1/messages`` wiring test below. This file
constructs :class:`CaseInconsistencyEvidence` directly, exercising the
suppression mechanics independent of how the evidence was gathered.

Leak-audit clauses for this slice:
- A: N/A directly for a suppressed token itself -- but reproven for the
  co-occurring case: a registered Term/L1-PII value sharing a suppressed
  token's payload is still blindfolded (L1/L2 win over suppression),
  including now that the condition is on by default.
- F: an unrelated genuine novel candidate with no case-inconsistency evidence
  still reaches L3 in the same traffic -- suppression is token-run-scoped,
  never a blanket payload skip.
- B/C/D/E/G: N/A -- no restore, mapping, or store change this slice.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
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


def test_no_case_inconsistency_argument_reproduces_todays_candidate_selection():
    text = "Pass this along to Mark Stone before the meeting."

    candidates = select_candidate_spans(text, known_entities=[])

    assert {c.text for c in candidates} == {"Pass", "Mark", "Stone"}


def test_has_evidence_takes_no_threshold_and_applies_proportionate_evidence():
    # Issue #345: the threshold parameter is gone -- proportionate evidence
    # (lowercase occurrences must outnumber capitalized ones) is the only rule.
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"pass": 5, "mark": 1}, capitalized_counts={"pass": 1, "mark": 3}
    )

    assert evidence.has_evidence("Pass") is True
    assert evidence.has_evidence("Mark") is False


def test_case_inconsistency_suppression_no_longer_carries_a_threshold_field():
    evidence = CaseInconsistencyEvidence(lowercase_counts={"pass": 1})

    suppression = CaseInconsistencySuppression(evidence=evidence)

    assert suppression.evidence is evidence


def test_no_lowercase_evidence_leaves_a_candidate_untouched():
    text = "Please brief Quentin tomorrow."
    evidence = CaseInconsistencyEvidence(lowercase_counts={}, capitalized_counts={})
    suppression = CaseInconsistencySuppression(evidence=evidence)

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
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert any(c.text == "Mark" for c in candidates)


def test_proportionate_evidence_suppresses_when_lowercase_dominates():
    text = "Pass this test."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"pass": 5}, capitalized_counts={"pass": 1}
    )
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert not any(c.text == "Pass" for c in candidates)


def test_incidental_evidence_protects_an_ordinary_word_real_name():
    # The exact case ADR-0023/#344's fixture measured: one incidental
    # lowercase occurrence of an ordinary-word real name amid many capitalized
    # ones does not dominate, so proportionate evidence keeps the referent.
    text = "Mark Stone is the client contact."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"mark": 1, "stone": 1},
        capitalized_counts={"mark": 4, "stone": 4},
    )
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert {c.text for c in candidates} == {"Mark", "Stone"}


def test_conjunctive_rule_protects_a_multiword_candidate_missing_one_tokens_evidence():
    # ADR-0023's own pinned case: "Project Halyard" must not be suppressed on
    # the strength of lowercase "project" alone -- "halyard" has no evidence.
    text = "Project Halyard is the codename for this engagement."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"project": 2}, capitalized_counts={"project": 1, "halyard": 1}
    )
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert {c.text for c in candidates} == {"Project", "Halyard"}


def test_conjunctive_rule_suppresses_whole_run_when_every_token_has_evidence():
    text = "Northern Data handles this account."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"northern": 2, "data": 2},
        capitalized_counts={"northern": 1, "data": 1},
    )
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert {c.text for c in candidates} == set()


def test_l3_detector_detect_threads_case_inconsistency_through_to_candidacy():
    text = "Pass the review to Zolfgang."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"pass": 2}, capitalized_counts={"pass": 1}
    )
    suppression = CaseInconsistencySuppression(evidence=evidence)
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)

    detector.detect(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert "Pass" not in adjudicator.calls
    assert "Zolfgang" in adjudicator.calls


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([])


def test_blindfold_payload_omitting_case_inconsistency_reproduces_unsuppressed_selection():
    # blindfold_payload's own parameter default (None -- no evidence
    # computed) leaves candidate selection unaffected by this condition, even
    # though the payload itself carries plenty of lowercase evidence for
    # "Pass". Production traffic never hits this path bare: the app boundary
    # (test_messages_endpoint_wires_case_inconsistency_suppression_by_default,
    # below) always constructs the evidence and threads it through.
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


def test_blindfold_payload_suppresses_case_inconsistent_token_when_constructed():
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
    suppression = CaseInconsistencySuppression(evidence=evidence)

    blindfold_payload(payload, mapping, detector, inbox, case_inconsistency=suppression)

    assert "Pass" not in adjudicator.calls


def test_blindfold_chat_completions_payload_omitting_case_inconsistency_reproduces_unsuppressed_selection():
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


def test_blindfold_chat_completions_payload_suppresses_case_inconsistent_token_when_constructed():
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "Please pass the review along; pass rate matters."},
            {"role": "user", "content": "Pass this to the team."},
        ],
    }
    evidence = extract_case_inconsistency_evidence_chat_completions(payload)
    suppression = CaseInconsistencySuppression(evidence=evidence)

    blindfold_chat_completions_payload(
        payload, mapping, detector, inbox, case_inconsistency=suppression
    )

    assert "Pass" not in adjudicator.calls


def test_registered_term_with_case_inconsistency_evidence_is_still_blindfolded():
    # Protection wins over suppression: a registered Term sharing its
    # lowercase form with prose elsewhere in the payload is still blindfolded
    # by L2, unaffected by this suppression layer -- now proven for the
    # shipped, on-by-default rule rather than the removed bare-presence one.
    mapping = SurrogateMapping.from_pairs([("Pass", "Northern Vault")])
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "system": "Please pass this along; most people pass here too.",
        "messages": [{"role": "user", "content": "Pass handles the credential."}],
    }
    evidence = extract_case_inconsistency_evidence_messages(payload)
    suppression = CaseInconsistencySuppression(evidence=evidence)

    blinded, _session = blindfold_payload(
        payload, mapping, detector, inbox, case_inconsistency=suppression
    )

    assert "Pass" not in blinded["messages"][0]["content"]
    surrogate = mapping.surrogate_for("Pass")
    assert surrogate is not None
    assert surrogate in blinded["messages"][0]["content"]


def _make_stub_upstream():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Acknowledged."}],
                "model": "claude-3-5-sonnet",
                "stop_reason": "end_turn",
            },
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    from blindfold.upstream import UpstreamClient

    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_messages_endpoint_wires_case_inconsistency_suppression_by_default():
    # Issue #345 acceptance criterion: the condition is on by default in
    # production -- /v1/messages must compute the evidence itself
    # (extract_case_inconsistency_evidence_messages) and thread it through
    # with no opt-in, mirroring how payload-region confinement is wired
    # (test_system_confined_l3_suppression.py's sibling test). A caller of
    # blindfold_payload directly is not enough evidence the app actually
    # wires this.
    adjudicator = _RecordingAdjudicator()
    app.dependency_overrides[get_upstream_client] = _make_stub_upstream
    app.dependency_overrides[get_mapping] = lambda: _seeded_mapping()
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(adjudicator)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "system": "Standard instructions for this workspace.",
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Most files Pass validation; pass rates matter, and "
                                "something should pass every time. Please brief "
                                "Quentin tomorrow."
                            ),
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert "Pass" not in adjudicator.calls
    assert "Quentin" in adjudicator.calls
