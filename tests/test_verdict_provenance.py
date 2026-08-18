"""Verdict provenance: which adjudicator produced an L3 verdict (issue #348).

``_entity_kind_for`` (app.py) collapses ``entity_type`` into ``kind``, so an
untyped verdict (the inner LLM adjudicators never detect a type, l3.py:228)
is indistinguishable from a confident ``"person"`` verdict on the review-inbox
response. That conflation blocked #346's own measurement question because the
two candidate causes -- a wrong GLiNER label, and ``None`` defaulting to
``"person"`` -- produce byte-identical output.

This suite covers the fix in three layers:
- ``L3Adjudication.adjudicator`` (l3.py) records which concrete adjudicator
  produced a verdict -- ``"gliner"`` (GLiNER outer, confirmed without an inner
  call), ``"inner_llm"`` (Ollama/oMLX), or ``"cascade_coalescing"`` (engine.py
  coalesced two adjacent tokens confirmed by two *different* adjudicators into
  one referent, issue #162/#170's own coalescing, so no single adjudicator's
  label is honest for the merged span).
- ``ReviewItem``/``ReviewInbox.upsert`` carry it alongside ``entity_type``.
- the review-inbox API response exposes both ``entity_type`` (verbatim) and
  ``adjudicator``, without changing ``kind``'s existing values.

Read-only observability: no test in this file exercises minting, typing,
suppression or surrogate-pool-selection *decisions* -- only what gets
recorded about a decision already made. Leak-audit: N/A for the seam-level
tests (no request path); the API-level tests reuse the existing
``viewer``-gated endpoint and assert only additional metadata fields, no new
route or auth bypass -- covered explicitly at the bottom of this file.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_rbac, get_review_inbox
from blindfold.engine import blindfold_payload
from blindfold.l3 import (
    ADJUDICATOR_CASCADE_COALESCING,
    ADJUDICATOR_GLINER,
    ADJUDICATOR_INNER_LLM,
    CandidateSpan,
    L3Adjudication,
    L3Detector,
)
from blindfold.l3_gliner import GlinerCascadeAdjudicator
from blindfold.rbac import RbacRegistry
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


def test_l3_adjudication_defaults_adjudicator_to_none():
    # Existing callers (many test doubles across the suite) construct
    # L3Adjudication(is_entity=...) with no adjudicator kwarg at all -- the new
    # field must not force every one of them to be touched.
    decision = L3Adjudication(is_entity=True)

    assert decision.adjudicator is None


def test_l3_adjudication_stores_an_explicit_adjudicator():
    decision = L3Adjudication(is_entity=True, adjudicator=ADJUDICATOR_GLINER)

    assert decision.adjudicator == ADJUDICATOR_GLINER


# OllamaAdjudicator/OpenAICompatibleAdjudicator stamping ADJUDICATOR_INNER_LLM is
# covered directly in test_ollama_adjudicator.py / test_openai_compat_adjudicator.py
# (their existing exact-equality assertions on the returned L3Adjudication).


class _BoolOnlyClassifier:
    """The original ADR-0033 bool-only ``classify`` seam -- no type, no span."""

    def __init__(self, positives: frozenset[str]) -> None:
        self._positives = positives

    def classify(self, candidate: CandidateSpan) -> bool:
        return candidate.text in self._positives


class _RecordingInner:
    def __init__(self, decision: L3Adjudication) -> None:
        self._decision = decision

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return self._decision


def test_gliner_cascade_stamps_gliner_provenance_when_it_confirms_directly():
    classifier = _BoolOnlyClassifier(positives=frozenset({"Klaus"}))
    inner = _RecordingInner(L3Adjudication(is_entity=False))
    cascade = GlinerCascadeAdjudicator(classifier=classifier, inner=inner)
    candidate = CandidateSpan(
        text="Klaus", start=0, end=5, context="Klaus called.", context_offset=0
    )

    decision = cascade.adjudicate(candidate)

    assert decision.is_entity is True
    assert decision.adjudicator == ADJUDICATOR_GLINER


def test_gliner_cascade_passes_through_the_inner_adjudicators_own_provenance_on_a_negative():
    classifier = _BoolOnlyClassifier(positives=frozenset())
    inner_decision = L3Adjudication(is_entity=True, adjudicator=ADJUDICATOR_INNER_LLM)
    inner = _RecordingInner(inner_decision)
    cascade = GlinerCascadeAdjudicator(classifier=classifier, inner=inner)
    candidate = CandidateSpan(
        text="Klaus", start=0, end=5, context="Klaus called.", context_offset=0
    )

    decision = cascade.adjudicate(candidate)

    # A GLiNER negative delegates outright -- the cascade must never overwrite
    # the inner adjudicator's own provenance with its own.
    assert decision is inner_decision
    assert decision.adjudicator == ADJUDICATOR_INNER_LLM


class _MixedInner:
    """Confirms exactly the whitelisted candidate texts as ``ADJUDICATOR_INNER_LLM``."""

    def __init__(self, confirm: frozenset[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(
            is_entity=candidate.text in self._confirm, adjudicator=ADJUDICATOR_INNER_LLM
        )


def test_coalesced_referent_reports_a_single_adjudicator_when_both_tokens_agree():
    # "Sarah Bergmann" -- issue #162's own live repro -- both tokens confirmed
    # by the same GLiNER cascade: the merged referent's provenance is that one
    # adjudicator, not an ambiguous coalescing label.
    classifier = _BoolOnlyClassifier(positives=frozenset({"Sarah", "Bergmann"}))
    inner = _MixedInner(confirm=frozenset())  # would refuse if ever asked
    detector = L3Detector(GlinerCascadeAdjudicator(classifier=classifier, inner=inner))
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Hi, ich bin Sarah Bergmann."}],
    }

    blindfold_payload(payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Sarah Bergmann"
    assert item.adjudicator == ADJUDICATOR_GLINER


def test_coalesced_referent_reports_cascade_coalescing_when_tokens_disagree():
    # "Sarah" is a GLiNER positive (confirmed outright, no inner call); the
    # adjacent "Bergmann" is a GLiNER negative that only the inner LLM confirms.
    # Issue #162's coalescing merges them into one referent -- neither
    # adjudicator's label alone is honest for the merged span.
    classifier = _BoolOnlyClassifier(positives=frozenset({"Sarah"}))
    inner = _MixedInner(confirm=frozenset({"Bergmann"}))
    detector = L3Detector(GlinerCascadeAdjudicator(classifier=classifier, inner=inner))
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Hi, ich bin Sarah Bergmann."}],
    }

    blindfold_payload(payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Sarah Bergmann"
    assert item.adjudicator == ADJUDICATOR_CASCADE_COALESCING


@pytest.mark.parametrize(
    "entity_type, expected_kind",
    [
        (None, "term"),
        ("person", "person"),
        ("organization", "term"),
        ("phone", "term"),
        ("something-unrecognized", "term"),
    ],
)
def test_entity_kind_for_is_unchanged_for_every_entity_type(entity_type, expected_kind):
    # Issue #346: an untyped verdict (entity_type=None -- the inner-LLM
    # adjudicators' own documented shape, l3.py's ADJUDICATOR_INNER_LLM path)
    # is no longer rendered as a confident "person" claim. Every non-person
    # type, typed or absent, is the less-committal "term" bucket;
    # "person" alone still maps to "person".
    from blindfold.app import _entity_kind_for

    assert _entity_kind_for(entity_type) == expected_kind


@pytest.mark.anyio
async def test_review_inbox_response_exposes_entity_type_and_adjudicator_alongside_kind():
    # Acceptance criterion: an inner-LLM-adjudicated verdict (entity_type=None,
    # per l3.py:228's own documented design) must surface distinguishably from
    # a confident "person" verdict -- both currently collapse to kind="person".
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    inbox = ReviewInbox()
    item = inbox.upsert(
        "Klaus",
        context="Please brief Klaus tomorrow.",
        adjudicator=ADJUDICATOR_INNER_LLM,
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
    listed = resp.json()["items"]
    assert listed == [
        {
            "id": item.id,
            "real": "Klaus",
            "provisional_surrogate": item.provisional_surrogate,
            "context": "Please brief Klaus tomorrow.",
            "context_offset": item.context_offset,
            "entity_type": None,
            "adjudicator": ADJUDICATOR_INNER_LLM,
            "kind": "term",
        }
    ]
