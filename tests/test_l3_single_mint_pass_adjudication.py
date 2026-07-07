"""ADR-0022 decision #1: L3 adjudicates exactly once, in the blindfold mint pass.

Before this slice, wiring L3 at *both* the mint pass and the pre-egress gate would
double-adjudicate every token AND re-adjudicate the provisional surrogate the mint pass
just minted for a confirmed candidate (a fresh, previously-unseen capitalized token from
L3's point of view). This suite proves the fix structurally: the pre-egress gate no
longer calls L3 at all — it reverts to the leak gate over known entities.

Leak-audit clauses asserted here:
- A: the stub upstream saw only the surrogate world (the provisional surrogate is what
  egressed; the real candidate never crossed egress).
- B: the client received the real value back (closed-world restore).
- F: with L3 forced unavailable, a novel candidate still fail-closes -- from the mint
  pass now, not the (removed) pre-egress scan.

N/A this slice: C/E/G — not the concern of this suite (covered by adjacent suites).
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _RecordingStubAdjudicator:
    """Stub for Ollama: confirms only the whitelisted candidate; records every call."""

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm
        self.calls: list[str] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        return L3Adjudication(is_entity=candidate.text in self._confirm)


class _UnavailableAdjudicator:
    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise ConnectionError("ollama unreachable")


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
async def test_confirmed_provisional_surrogate_is_never_handed_back_to_l3():
    # The provisional surrogate the mint pass mints for "Klaus" is itself a fresh,
    # previously-unseen capitalized token (e.g. "Alex Brenner", ADR-0010's pool). If
    # anything re-ran L3 over the already-blindfolded text (the old pre-egress scan),
    # it would hand that surrogate straight back to the adjudicator. Assert it never
    # does: the adjudicator's calls contain the real candidate exactly once and never
    # any surrogate-shaped token.
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    adjudicator = _RecordingStubAdjudicator(confirm={"Klaus"})
    detector = L3Detector(adjudicator)

    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
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

    # Clause A: only the provisional surrogate egressed.
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    assert "Klaus" not in egressed
    item = inbox.list()[0]
    assert item.provisional_surrogate in egressed

    # The single-mint-pass invariant: L3 adjudicated "Klaus" exactly once, and the
    # surrogate minted for it was never itself handed back to the adjudicator.
    assert adjudicator.calls.count("Klaus") == 1
    for token in item.provisional_surrogate.split():
        assert token not in adjudicator.calls


@pytest.mark.anyio
async def test_l3_unavailable_fails_closed_from_the_mint_pass_not_a_pre_egress_rescan():
    # ADR-0022: the fail-closed 503 now originates in the mint pass. With L3 forced
    # unavailable and a novel candidate in the payload, the request must still block,
    # with zero egress (clause A) -- proving the block path no longer depends on a
    # pre-egress L3 re-scan that this slice removes.
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        {"content": [{"type": "text", "text": "ok"}]}, recorded
    )
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
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
                        {"role": "user", "content": "Please brief Quentin tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    body = resp.json()["error"]
    assert body["code"] == "blindfold_fail_closed"
    assert body["sub_reason"] == "l3_unavailable"
    assert recorded == []
