"""Per-candidate suppression provenance (issue #350).

#74 run 11 measured #59 precision at 43% and produced eight false positives
whose source text is identifiable but whose *cause* is not: `select_candidate_
spans` (l3.py) evaluates the five ADR-0023 suppression conditions (seeded
allowlist, declared tool vocabulary, expanded stopwords, system-confined
region, case-inconsistency) in sequence and drops a suppressed token silently,
so a token that survives carries nothing about how those conditions were
evaluated. Two very different diagnoses -- no prose-lowercase evidence at all
(a source-vocabulary cause) vs. a qualifying token protected by a non-
qualifying run-mate under the conjunctive rule (a rule cause) -- were
indistinguishable from run 11's own output.

This suite covers the fix in three layers:
- `select_candidate_spans` (l3.py), when asked (`trace_suppression=True`),
  attaches a `SuppressionTrace` naming all five conditions and their outcome
  to every surviving `CandidateSpan` -- read-only, never consulted by
  selection itself (pinned by the purity test below).
- `L3Detector.detect` always asks for the trace, so every review-worthy
  candidate carries one; `engine.py`'s mint pass and `mining.py` carry it,
  unresolved across coalescing, onto `ReviewItem.suppression_trace` -- not a
  persisted store column, mirroring `adjudicator` (issue #348).
- the review-inbox management API exposes it alongside the existing fields.

Leak-audit clauses for this slice:
- The trace carries candidate token text, so it must never reach an outbound
  payload (clause covered explicitly below) and must not create a new on-disk
  artifact (ADR-0047) -- it is never a store column, mirroring `adjudicator`.
- Read-only: candidate selection is provably identical with the trace enabled
  and disabled (clause covered explicitly below).
- A-D/G otherwise N/A -- no restore/mapping/gate code touched this slice.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_rbac, get_review_inbox
from blindfold.engine import blindfold_payload
from blindfold.mining import mine_transcripts
from blindfold.l3 import (
    CandidateSpan,
    CaseInconsistencyEvidence,
    CaseInconsistencySuppression,
    L3Adjudication,
    L3Detector,
    SUPPRESSION_CONDITION_CASE_INCONSISTENCY,
    SUPPRESSION_CONDITION_DECLARED_TOOL_VOCABULARY,
    SUPPRESSION_CONDITION_EXPANDED_STOPWORDS,
    SUPPRESSION_CONDITION_SEEDED_ALLOWLIST,
    SUPPRESSION_CONDITION_SYSTEM_CONFINED_REGION,
    select_candidate_spans,
)
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


def test_survivor_carries_no_trace_by_default():
    # Existing/default behavior (no opt-in) is untouched -- no test elsewhere
    # in the suite that constructs a CandidateSpan by hand needs to change.
    text = "Please contact Klaus tomorrow."

    candidates = select_candidate_spans(text, known_entities=[])

    klaus = next(c for c in candidates if c.text == "Klaus")
    assert klaus.suppression_trace is None


def test_survivor_trace_names_all_five_conditions_with_outcomes():
    # No allowlist/declared_tools/system_confined_tokens/case_inconsistency
    # wired -- "Klaus" survives, and the trace says so for each of the five
    # ADR-0023 conditions: the two None-able ones (seeded allowlist,
    # case-inconsistency) report evaluated=False, the other three
    # evaluated=True against their empty default -- none suppressed it.
    text = "Please contact Klaus tomorrow."

    candidates = select_candidate_spans(
        text, known_entities=[], trace_suppression=True
    )

    klaus = next(c for c in candidates if c.text == "Klaus")
    trace = klaus.suppression_trace
    assert trace is not None
    assert [c.name for c in trace.conditions] == [
        SUPPRESSION_CONDITION_SEEDED_ALLOWLIST,
        SUPPRESSION_CONDITION_DECLARED_TOOL_VOCABULARY,
        SUPPRESSION_CONDITION_EXPANDED_STOPWORDS,
        SUPPRESSION_CONDITION_SYSTEM_CONFINED_REGION,
        SUPPRESSION_CONDITION_CASE_INCONSISTENCY,
    ]
    assert all(not c.suppressed for c in trace.conditions)
    outcomes = {c.name: c.evaluated for c in trace.conditions}
    assert outcomes[SUPPRESSION_CONDITION_SEEDED_ALLOWLIST] is False
    assert outcomes[SUPPRESSION_CONDITION_DECLARED_TOOL_VOCABULARY] is True
    assert outcomes[SUPPRESSION_CONDITION_EXPANDED_STOPWORDS] is True
    assert outcomes[SUPPRESSION_CONDITION_SYSTEM_CONFINED_REGION] is True
    assert outcomes[SUPPRESSION_CONDITION_CASE_INCONSISTENCY] is False


def test_case_inconsistency_detail_attributes_survival_to_the_conjunctive_rule_not_absent_evidence():
    # ADR-0023's own pinned case (test_case_inconsistency_l3_suppression.py's
    # test_conjunctive_rule_protects_a_multiword_candidate_missing_one_tokens_
    # evidence): "Project" individually clears the proportionate-evidence bar
    # (lowercase "project" outnumbers capitalized "Project"), but "Halyard"
    # never appears lowercase at all -- the conjunctive rule requires BOTH, so
    # neither is suppressed. This is exactly #74 run 11's "rule cause": a
    # qualifying token surviving because of a non-qualifying run-mate, not
    # because it lacks evidence of its own.
    text = "Project Halyard is the codename for this engagement."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"project": 2}, capitalized_counts={"project": 1, "halyard": 1}
    )
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression, trace_suppression=True
    )

    project = next(c for c in candidates if c.text == "Project")
    condition = next(
        c
        for c in project.suppression_trace.conditions
        if c.name == SUPPRESSION_CONDITION_CASE_INCONSISTENCY
    )
    assert condition.evaluated is True
    assert condition.suppressed is False
    detail = condition.detail
    assert (detail.run_start, detail.run_end) == (0, len("Project Halyard"))
    tokens_by_text = {t.token: t for t in detail.tokens}
    project_token = tokens_by_text["Project"]
    halyard_token = tokens_by_text["Halyard"]
    # "Project"'s own numbers clear the bar -- its survival is NOT attributable
    # to its own absent evidence.
    assert project_token.lowercase_count > project_token.capitalized_count
    # "Halyard"'s numbers do not -- it is the run-mate the conjunctive rule
    # protected the whole run on account of.
    assert halyard_token.lowercase_count <= halyard_token.capitalized_count


def test_trace_suppression_does_not_change_candidate_selection():
    # Read-only: the trace is a pure side-channel over a decision already
    # made -- it must never change which tokens are selected, their order, or
    # their spans, whether or not tracing is asked for.
    text = "Project Halyard mentioned Klaus and the Pass along the way."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"project": 2, "pass": 2}, capitalized_counts={"project": 1, "halyard": 1, "pass": 1}
    )
    suppression = CaseInconsistencySuppression(evidence=evidence)

    traced = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression, trace_suppression=True
    )
    untraced = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression, trace_suppression=False
    )

    def _core(candidate: CandidateSpan) -> tuple:
        return (
            candidate.text,
            candidate.start,
            candidate.end,
            candidate.context,
            candidate.context_offset,
        )

    assert [_core(c) for c in traced] == [_core(c) for c in untraced]
    assert all(c.suppression_trace is not None for c in traced)
    assert all(c.suppression_trace is None for c in untraced)


def test_l3_detector_detect_always_attaches_a_suppression_trace():
    # detect() is the seam feeding the review record (engine.py's mint pass) --
    # it always asks select_candidate_spans for a trace, so a caller doesn't
    # have to know this parameter exists to get provenance on what it mints.
    adjudicator_calls: list[str] = []

    class _Adjudicator:
        def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
            adjudicator_calls.append(candidate.text)
            return L3Adjudication(is_entity=True)

    detector = L3Detector(_Adjudicator())

    results = detector.detect("Please brief Klaus tomorrow.", known_entities=[])

    assert adjudicator_calls == ["Klaus"]
    (candidate, decision) = results[0]
    assert candidate.suppression_trace is not None
    assert decision.is_entity is True


def test_review_inbox_upsert_carries_the_suppression_trace_onto_the_review_item():
    candidates = select_candidate_spans(
        "Please contact Klaus tomorrow.", known_entities=[], trace_suppression=True
    )
    klaus = next(c for c in candidates if c.text == "Klaus")
    inbox = ReviewInbox()

    item = inbox.upsert(
        "Klaus",
        context="Please contact Klaus tomorrow.",
        suppression_trace=klaus.suppression_trace,
    )

    assert item.suppression_trace is klaus.suppression_trace


def test_review_item_defaults_suppression_trace_to_none():
    # Existing callers across the suite construct ReviewItem/upsert() with no
    # suppression_trace kwarg at all -- must not force every one to change.
    inbox = ReviewInbox()

    item = inbox.upsert("Klaus", context="Please contact Klaus tomorrow.")

    assert item.suppression_trace is None


class _ConfirmAdjudicator:
    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=True)


def test_mint_pass_carries_the_suppression_trace_onto_the_review_item():
    # End-to-end: a real request through blindfold_payload's own mint pass
    # (engine.py) must thread the confirming candidate's suppression trace
    # onto the resulting ReviewItem -- not just the direct upsert() call.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAdjudicator())
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Please brief Klaus tomorrow."}],
    }

    blindfold_payload(payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Klaus"
    assert item.suppression_trace is not None
    names = [c.name for c in item.suppression_trace.conditions]
    assert names == [
        SUPPRESSION_CONDITION_SEEDED_ALLOWLIST,
        SUPPRESSION_CONDITION_DECLARED_TOOL_VOCABULARY,
        SUPPRESSION_CONDITION_EXPANDED_STOPWORDS,
        SUPPRESSION_CONDITION_SYSTEM_CONFINED_REGION,
        SUPPRESSION_CONDITION_CASE_INCONSISTENCY,
    ]


def test_mining_carries_the_suppression_trace_onto_the_proposed_review_item():
    # Mining (mining.py) is the other route onto the review record -- the same
    # candidate-span seam, no coalescing pass. Must not silently drop the
    # trace live requests get.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAdjudicator())

    report = mine_transcripts(
        ["I later met Klaus at the office."], detector, mapping, inbox
    )

    klaus = next(item for item in report.proposed if item.real == "Klaus")
    assert klaus.suppression_trace is not None


@pytest.mark.anyio
async def test_review_inbox_response_exposes_the_suppression_trace():
    # "management surface" (issue #350's privacy constraint): the trace lives
    # only where the review inbox already lives -- the same viewer-gated
    # endpoint #348 exposed adjudicator/entity_type on.
    from blindfold.rbac import RbacRegistry

    candidates = select_candidate_spans(
        "Please contact Klaus tomorrow.", known_entities=[], trace_suppression=True
    )
    klaus = next(c for c in candidates if c.text == "Klaus")
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    inbox = ReviewInbox()
    inbox.upsert(
        "Klaus",
        context="Please contact Klaus tomorrow.",
        suppression_trace=klaus.suppression_trace,
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
        ) as client:
            resp = await client.get(
                "/v1/management/review-inbox",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    item = resp.json()["items"][0]
    trace = item["suppression_trace"]
    names = [c["name"] for c in trace["conditions"]]
    assert names == [
        SUPPRESSION_CONDITION_SEEDED_ALLOWLIST,
        SUPPRESSION_CONDITION_DECLARED_TOOL_VOCABULARY,
        SUPPRESSION_CONDITION_EXPANDED_STOPWORDS,
        SUPPRESSION_CONDITION_SYSTEM_CONFINED_REGION,
        SUPPRESSION_CONDITION_CASE_INCONSISTENCY,
    ]
    assert all(not c["suppressed"] for c in trace["conditions"])


@pytest.mark.anyio
async def test_review_inbox_response_renders_null_suppression_trace_when_absent():
    # Back-compat: an item minted before this issue (or via a direct upsert()
    # call that never passed one) renders null, not a missing key or a crash.
    from blindfold.rbac import RbacRegistry

    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    inbox = ReviewInbox()
    inbox.upsert("Klaus", context="Please contact Klaus tomorrow.")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
        ) as client:
            resp = await client.get(
                "/v1/management/review-inbox",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.json()["items"][0]["suppression_trace"] is None


def test_suppression_trace_never_appears_in_the_outbound_payload():
    # Leak-audit clause: the trace carries candidate token text (issue #350's
    # own privacy constraint), so it must never cross provider egress and must
    # add no new egress surface. Walk every string leaf of the payload
    # blindfold_payload actually returns for the provider (engine.py's own
    # walk_string_leaves primitive, the leak gate's own traversal) and assert
    # none of the condition names / field name ever appear.
    from blindfold.engine import walk_string_leaves

    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAdjudicator())
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Please brief Klaus tomorrow."}],
    }

    blindfolded, _session = blindfold_payload(payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    assert inbox.list()[0].suppression_trace is not None  # sanity: a trace exists to leak
    leaves: list[str] = []
    walk_string_leaves(blindfolded, leaves.append)
    forbidden = {
        "suppression_trace",
        SUPPRESSION_CONDITION_SEEDED_ALLOWLIST,
        SUPPRESSION_CONDITION_DECLARED_TOOL_VOCABULARY,
        SUPPRESSION_CONDITION_EXPANDED_STOPWORDS,
        SUPPRESSION_CONDITION_SYSTEM_CONFINED_REGION,
        SUPPRESSION_CONDITION_CASE_INCONSISTENCY,
    }
    for leaf in leaves:
        assert not any(token in leaf for token in forbidden)
