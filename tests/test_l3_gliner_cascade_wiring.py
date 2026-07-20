"""Config wiring + request-path integration for the GLiNER cascade (ADR-0033 §2,
issue #139).

Issue #138 built ``GlinerCascadeAdjudicator``/``GlinerClassifier`` behind the
``L3Adjudicator`` seam, exercised in isolation (test_l3_gliner_cascade.py). This
slice is the config → ``_build_l3_adjudicator`` → ``L3Detector`` → mint-pass wiring
that lets ``BLINDFOLD_L3_PROVIDER=gliner`` actually reach a real request -- so this
file's job is proving the cascade is reachable end-to-end through the proxy, not
re-testing the cascade's own is_entity logic.

Leak-audit clause analysis:
- A: the stub upstream receives only the surrogate for a GLiNER-positive candidate --
  the real candidate text never crosses egress. Issue #157 adds a batch-path variant
  of the same assertion: two GLiNER-negatives collapsed into one
  ``inner.adjudicate_batch`` call still leave zero real candidate text in the
  egressed payload.
- F: GLiNER-negative routes to the (stubbed) inner adjudicator, which remains the
  sole arbiter of is_entity=False -- unchanged fail-closed behavior, only reached via
  a different L3Adjudicator concrete class than the plain-Ollama/oMLX path. The
  batch path's own short/malformed-response fail-closed recovery is exercised at
  the adjudicator-seam level (test_l3_gliner_cascade.py), not re-proven here.
- B/C/D/E/G: N/A -- unchanged from the existing L3-detector-substitution tests this
  file mirrors (test_l3_single_mint_pass_adjudication.py); this slice only changes
  which concrete L3Adjudicator app.py wires in, not restore/verify-pass/mapping-store
  behavior.

Seam stubs: a recording GLiNER classifier and a recording inner adjudicator (same
shape as test_l3_gliner_cascade.py's) stand in for the real ONNX model and the real
LLM adjudicator -- no real GLiNER model load, no real Ollama/oMLX network call.
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
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.l3_gliner import GlinerCascadeAdjudicator
from blindfold.review import ReviewInbox
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _RecordingClassifier:
    """Stub for GLiNER -- records every classify() call, returns a scripted verdict."""

    def __init__(self, positives: frozenset[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self._positives = positives

    def classify(self, candidate: CandidateSpan) -> bool:
        self.calls.append(candidate.text)
        return candidate.text in self._positives


class _LabelAwareRecordingClassifier:
    """Stub for GLiNER's richer type-carrying seam (issue #167): implements
    ``classify_type`` (returns the tagged label, or ``None``), not just the
    bool-only ``classify`` the other stub in this file uses.
    """

    def __init__(self, tagged_hits: dict[str, str]) -> None:
        self.calls: list[str] = []
        self._tagged_hits = tagged_hits

    def classify(self, candidate: CandidateSpan) -> bool:
        self.calls.append(candidate.text)
        return candidate.text in self._tagged_hits

    def classify_type(self, candidate: CandidateSpan) -> str | None:
        self.calls.append(candidate.text)
        return self._tagged_hits.get(candidate.text)


class _RecordingInnerAdjudicator:
    """Stub for the inner L3Adjudicator (Ollama/oMLX) -- records every call."""

    def __init__(self, confirm: frozenset[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        return L3Adjudication(is_entity=candidate.text in self._confirm)


class _RecordingBatchInnerAdjudicator:
    """Stub for a batch-capable inner adjudicator (Ollama/oMLX, issue #157) --
    records every ``adjudicate_batch()`` call, no single-candidate ``adjudicate()``
    fallback, so the batch path is genuinely exercised end-to-end through the
    real request path rather than silently falling back.
    """

    def __init__(self, confirm: frozenset[str] = frozenset()) -> None:
        self.batch_calls: list[tuple[str, ...]] = []
        self._confirm = confirm

    def adjudicate_batch(
        self, candidates: list[CandidateSpan]
    ) -> list[L3Adjudication]:
        self.batch_calls.append(tuple(c.text for c in candidates))
        return [
            L3Adjudication(is_entity=c.text in self._confirm) for c in candidates
        ]


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


def _make_stub_upstream(scripted_response: dict, recorded: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_gliner_positive_candidate_becomes_entity_without_calling_inner_adjudicator():
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    classifier = _RecordingClassifier(positives=frozenset({"Klaus"}))
    inner = _RecordingInnerAdjudicator(confirm=frozenset())  # would refuse if asked
    detector = L3Detector(GlinerCascadeAdjudicator(classifier=classifier, inner=inner))

    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please brief Klaus tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    # Clause A: only the surrogate egressed -- the real candidate never crossed egress.
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    assert "Klaus" not in egressed
    item = inbox.list()[0]
    assert item.provisional_surrogate in egressed

    # GLiNER positive -> confirmed entity with zero inner-adjudicator calls.
    assert classifier.calls.count("Klaus") == 1
    assert inner.calls == []


@pytest.mark.anyio
async def test_gliner_negative_candidate_still_reaches_the_inner_adjudicator():
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    classifier = _RecordingClassifier(positives=frozenset())  # GLiNER misses it
    inner = _RecordingInnerAdjudicator(confirm=frozenset({"Klaus"}))
    detector = L3Detector(GlinerCascadeAdjudicator(classifier=classifier, inner=inner))

    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please brief Klaus tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    # GLiNER negative -> escalates to the inner adjudicator, which remains the sole
    # arbiter -- fail-closed recall preserved (ADR-0033 §2).
    assert classifier.calls.count("Klaus") == 1
    assert inner.calls.count("Klaus") == 1
    egressed = recorded[0].content.decode("utf-8")
    assert "Klaus" not in egressed
    item = inbox.list()[0]
    assert item.provisional_surrogate in egressed


@pytest.mark.anyio
async def test_gliner_negatives_batch_through_one_inner_call_stay_leak_clean():
    # Issue #157: the cascade's adjudicate_batch collapses multiple GLiNER-negatives
    # into one inner.adjudicate_batch call. Proving this end-to-end through the real
    # request path (not just the isolated adjudicator-seam tests in
    # test_l3_gliner_cascade.py) satisfies leak-audit clause A for the new batch
    # path specifically: only surrogates for both negatives cross egress, and the
    # client's response (via the review inbox's provisional surrogate) never
    # carries the real candidate text -- one inner round trip, not two.
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    classifier = _RecordingClassifier(positives=frozenset())  # GLiNER misses both
    inner = _RecordingBatchInnerAdjudicator(confirm=frozenset({"Klaus", "Yasmin"}))
    detector = L3Detector(GlinerCascadeAdjudicator(classifier=classifier, inner=inner))

    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {
                            "role": "user",
                            "content": "Please brief Klaus and Yasmin tomorrow.",
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    # Both GLiNER-negatives collapsed into ONE inner.adjudicate_batch call.
    assert len(inner.batch_calls) == 1
    assert sorted(inner.batch_calls[0]) == ["Klaus", "Yasmin"]
    egressed = recorded[0].content.decode("utf-8")
    assert "Klaus" not in egressed
    assert "Yasmin" not in egressed
    surrogates = {item.provisional_surrogate for item in inbox.list()}
    assert len(surrogates) == 2
    assert all(surrogate in egressed for surrogate in surrogates)


@pytest.mark.anyio
async def test_gliner_organization_candidate_mints_an_org_shaped_surrogate_end_to_end():
    # Issue #167 live evidence, driven through the real /v1/messages request path:
    # GLiNER classifying "Nordwind Logistik" as organization must mint an
    # org-shaped surrogate, not a person name from the default pool -- proven at
    # the full request-path level, not just the isolated adjudicator-seam tests
    # (test_l3_gliner_cascade.py, test_review_inbox.py).
    #
    # Clause A: the stub upstream sees only the org-shaped surrogate -- zero
    # real-entity tokens cross egress.
    from blindfold.review import _PROVISIONAL_POOL

    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    classifier = _LabelAwareRecordingClassifier(
        tagged_hits={"Nordwind": "organization", "Logistik": "organization"}
    )
    inner = _RecordingInnerAdjudicator(confirm=frozenset())  # would refuse if asked
    detector = L3Detector(GlinerCascadeAdjudicator(classifier=classifier, inner=inner))

    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {
                            "role": "user",
                            "content": "...von Nordwind Logistik heute.",
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    egressed = recorded[0].content.decode("utf-8")
    assert "Nordwind" not in egressed
    assert "Logistik" not in egressed

    item = inbox.list()[0]
    assert item.real == "Nordwind Logistik"
    assert item.provisional_surrogate not in _PROVISIONAL_POOL
    assert item.provisional_surrogate in egressed
    assert inner.calls == []
