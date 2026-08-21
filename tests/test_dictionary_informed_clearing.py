"""Dictionary-informed clearing (ADR-0023, "Update (run-14 gate decisions)",
issue #362): the fifth suppression condition's third clearing path.

Runs 11, 12 and 13 (#74) each plateaued on the same residual: one novel
false positive per run from out-of-repo environment text -- an ordinary
English noun at an exact tie or a capitalized-dominant ratio, a shape no
existing condition reaches (payload-region confinement can't fire because the
text occurs in both `system[]` and `messages[]`; the positional heuristic
can't fire because the occurrences are mid-sentence; the case-inconsistency
family's own three-valued rule vetoes or abstains on exactly this ratio by
construction). Per-token seeding doesn't terminate against arbitrary beta
environments. This decision: a candidate whose casefolded form is a common
English word, with at least one prose-lowercase occurrence in the payload,
clears -- but only for a **single-token** Title-Case run, and never when
lowercase evidence is entirely absent (the universal distinctive-name
signal). Multi-word runs are untouched; the #358 three-valued rule governs
them unchanged.

Every test below uses synthetic vocabulary -- ordinary dictionary words
chosen because they are common English words, never a value any live #74 run
actually measured (ADR-0023's own self-poisoning-guard precedent).

Leak-audit clauses for this slice:
- A: reproven directly below -- a registered Term equal to a common-English
  word is still blindfolded even though the suppression condition would
  otherwise clear it (protection wins over suppression; `known_surfaces` is
  checked first in `select_candidate_spans`).
- E: N/A -- this condition adds no state; it is a pure function of the
  already-threaded per-request `CaseInconsistencySuppression` evidence plus a
  static, module-level wordlist loaded once at import (mirrors
  `_SENTENCE_STOPWORDS`) -- never per-request state, never state on the L3
  detector singleton (#261 invariant).
- B/C/D/F/G: N/A -- no restore, mapping-store, or resolution-gate code
  touched this slice; this is L3 candidate-span selection only.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.engine import blindfold_payload, extract_case_inconsistency_evidence_messages
from blindfold.l3 import (
    CandidateSpan,
    CaseInconsistencyEvidence,
    CaseInconsistencySuppression,
    L3Adjudication,
    L3Detector,
    SUPPRESSION_CONDITION_CASE_INCONSISTENCY,
    _COMMON_ENGLISH_WORDS,
    select_candidate_spans,
)
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


def test_common_english_wordlist_contains_an_ordinary_dictionary_word():
    # "repository" is comfortably common English vocabulary (and the exact
    # class -- ordinary workplace/software nouns -- the ADR names), never a
    # value any live run measured.
    assert "repository" in _COMMON_ENGLISH_WORDS


def test_common_english_wordlist_excludes_an_invented_codename():
    # An invented, non-dictionary stand-in (this codebase's own convention
    # for a distinctive project codename, e.g. test_case_inconsistency_
    # dictionary_word_fixture.py's "Larkmoor"/"Wisteria") must not appear --
    # otherwise the wordlist would be indistinguishable from "everything".
    assert "larkmoor" not in _COMMON_ENGLISH_WORDS


def test_capitalized_dominant_single_token_dictionary_word_clears():
    # AC 1: the run-13 residual's own shape -- a single-token Title-Case
    # candidate whose casefolded form is in the shipped wordlist, with >=1
    # prose-lowercase occurrence, at a capitalized-dominant ratio the
    # existing three-valued rule alone vetoes (ADR-0023 "Update (issue
    # #358)" Decision 2 -- this is exactly the shape that update *rejected*
    # filtering; this issue ships it). "Repository" is common English
    # vocabulary and appears in the shipped wordlist.
    text = (
        "Repository access requires approval. Repository access changed last "
        "week. Meanwhile the repository holds every build artifact."
    )
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"repository": 1}, capitalized_counts={"repository": 2}
    )
    assert evidence.verdict("Repository").name == "VETOES"
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert not any(c.text == "Repository" for c in candidates)


def test_single_token_dictionary_word_with_zero_lowercase_occurrences_still_mints():
    # AC 2: absence of lowercase evidence stays the universal distinctive-name
    # signal, whatever the wordlist says. "Meadow" is common English
    # vocabulary (in the shipped wordlist) but never appears lowercase here --
    # used only as a capitalized proper-noun-shaped referent.
    text = "Please brief Meadow before the review. Meadow will lead the call."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={}, capitalized_counts={"meadow": 2}
    )
    assert evidence.verdict("Meadow").name == "VETOES"
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert any(c.text == "Meadow" for c in candidates)


def test_exact_tie_single_token_dictionary_word_clears():
    # The run-13 residual's own shape (ADR-0023, "Update (run-14 gate
    # decisions)"): a single-token dictionary word at an exact nonzero tie
    # ABSTAINS under the count-based rule alone (issue #358) -- not vetoed,
    # so it used to mint. Dictionary-informed clearing reaches it: any
    # nonzero lowercase evidence plus wordlist membership clears, tie
    # included.
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"archive": 1}, capitalized_counts={"archive": 1}
    )
    assert evidence.verdict("Archive").name == "ABSTAINS"
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        "Archive access is limited today.",
        known_entities=[],
        case_inconsistency=suppression,
    )

    assert not any(c.text == "Archive" for c in candidates)


def test_multiword_run_is_unaffected_by_dictionary_informed_clearing():
    # AC 3: multi-word candidates are byte-for-byte unaffected -- a two-token
    # run where each constituent is individually a common dictionary word at
    # a capitalized-dominant/tied ratio must still mint, because the
    # dictionary path never runs for a run longer than one token (the #358
    # three-valued rule alone governs multi-word runs).
    text = "Please contact Harbor Meadow about the review. harbor operations and meadow surveys both continued today."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"harbor": 1, "meadow": 1},
        capitalized_counts={"harbor": 1, "meadow": 1},
    )
    assert evidence.verdict("Harbor").name == "ABSTAINS"
    assert evidence.verdict("Meadow").name == "ABSTAINS"
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert {c.text for c in candidates} >= {"Harbor", "Meadow"}


def test_bare_first_name_residual_is_documented_behavior_not_a_bug():
    # AC 4: the ADR's own accepted residual, pinned as documented behavior
    # rather than left implicit -- a real person referred to only by a bare
    # dictionary-word first name is suppressed from L3 novelty discovery once
    # that word also occurs prose-lowercase in the payload. "Grace" is
    # ordinary English vocabulary (in the shipped wordlist) and a common
    # given name -- exactly the collision ADR-0023 names as the accepted
    # cost of this decision, not a defect to fix here.
    text = "Please loop in Grace before the launch. We appreciate her grace under pressure this week."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"grace": 1}, capitalized_counts={"grace": 1}
    )
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression
    )

    assert not any(c.text == "Grace" for c in candidates)


def test_registered_term_equal_to_a_dictionary_word_is_still_blindfolded():
    # AC 6: protection always wins over suppression -- a registered Term
    # whose surface is a common-English word is still blindfolded by L2 even
    # though dictionary-informed clearing would otherwise suppress it from L3
    # candidacy (`known_surfaces` is checked before any suppression condition
    # in `select_candidate_spans`).
    mapping = SurrogateMapping.from_pairs([("Grace", "Northern Vault")])
    inbox = ReviewInbox()

    class _ConfirmAdjudicator:
        def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
            return L3Adjudication(is_entity=True)

    detector = L3Detector(_ConfirmAdjudicator())
    payload = {
        "model": "m",
        "system": "Please loop in Grace before the launch.",
        "messages": [
            {
                "role": "user",
                "content": "We appreciate her grace under pressure this week.",
            }
        ],
    }
    evidence = extract_case_inconsistency_evidence_messages(payload)
    suppression = CaseInconsistencySuppression(evidence=evidence)

    blinded, _session = blindfold_payload(
        payload, mapping, detector, inbox, case_inconsistency=suppression
    )

    assert "Grace" not in blinded["system"]
    surrogate = mapping.surrogate_for("Grace")
    assert surrogate is not None
    assert surrogate in blinded["system"]


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
async def test_messages_endpoint_wires_dictionary_informed_clearing_by_default():
    # AC 7: dictionary-informed clearing needs no new app-boundary wiring of
    # its own -- it is folded into the existing case-inconsistency evidence
    # bundle (`CaseInconsistencySuppression`), which /v1/messages already
    # constructs and threads through for every real exchange (issue #345).
    # This is the same per-request purity: no detector state, a pure
    # function of this request's payload plus the static wordlist (#261).
    from blindfold.app import (
        app,
        get_l3_detector,
        get_mapping,
        get_review_inbox,
        get_upstream_client,
    )

    adjudicator_calls: list[str] = []

    class _RecordingAdjudicator:
        def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
            adjudicator_calls.append(candidate.text)
            return L3Adjudication(is_entity=False)

    app.dependency_overrides[get_upstream_client] = _make_stub_upstream
    app.dependency_overrides[get_mapping] = lambda: SurrogateMapping.from_pairs([])
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_RecordingAdjudicator())
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "system": "Please contact Harbor about the archive today.",
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Harbor operations continued; the archive was "
                                "reviewed by harbor staff, and we archive "
                                "records weekly. Please brief Quentin tomorrow."
                            ),
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert "Harbor" not in adjudicator_calls
    assert "Quentin" in adjudicator_calls


def test_suppression_trace_run_detail_distinguishes_dictionary_membership():
    # AC 5: the #350 SuppressionTrace must let a future run audit distinguish
    # dictionary-informed clearing from count-based clearing. A survivor's
    # own run-mate can be dictionary-informed even when the survivor itself
    # isn't -- e.g. a multi-word run where a generic dictionary-word
    # constituent (with pervasive lowercase evidence) sits beside a
    # distinctive one (zero lowercase evidence, an unconditional veto), so
    # the whole run survives (mints) via neither clearing path. Recording
    # each member's own wordlist membership -- alongside the counts already
    # recorded -- lets a reviewer tell "this run-mate would have cleared via
    # the dictionary path on its own" apart from "this run-mate's numbers
    # clear/veto/abstain under the count-based rule", the same diagnostic
    # granularity #350 already gives the count-based rule.
    text = "Please contact Archive Larkmoor about the migration. archive access changed twice this week."
    evidence = CaseInconsistencyEvidence(
        lowercase_counts={"archive": 2}, capitalized_counts={"archive": 1}
    )
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        text, known_entities=[], case_inconsistency=suppression, trace_suppression=True
    )

    larkmoor = next(c for c in candidates if c.text == "Larkmoor")
    condition = next(
        c
        for c in larkmoor.suppression_trace.conditions
        if c.name == SUPPRESSION_CONDITION_CASE_INCONSISTENCY
    )
    tokens_by_text = {t.token: t for t in condition.detail.tokens}
    assert tokens_by_text["Archive"].in_common_word_list is True
    assert tokens_by_text["Larkmoor"].in_common_word_list is False
