"""Review inbox + learning loop seam (ADR-0010, ADR-0011): novel candidates are
auto-blindfolded with a **provisional surrogate** and land in an **async review inbox**;
the user later **confirms** (entity graph grows) or **rejects** (allowlist grows).

Drives the proxy seam end-to-end:
- ``POST /v1/messages`` with a novel capitalized token the L3 adjudicator confirms as
  an entity → stub upstream sees only the surrogate (leak-audit clause A), the
  request returns (protection is non-blocking — agents don't stall), and the
  candidate appears in the inbox.
- ``GET /v1/management/review-inbox`` lists provisional candidates.
- ``POST /v1/management/review-inbox/{id}/confirm`` grows the entity graph: the
  same real value is detected deterministically (no L3 call) on the next request.
- ``POST /v1/management/review-inbox/{id}/reject`` grows the allowlist: the same
  candidate is no longer blindfolded on the next request.

L3 (Ollama) is stubbed at the network boundary (the ``_StubAdjudicator``); the
upstream provider is stubbed at the egress boundary (``httpx.MockTransport``).

ADR-0022 (issue #57): L3 now adjudicates once, in this same mint pass -- there is no
longer a separate pre-egress L3 re-scan to route around, so these tests simply
override ``get_l3_detector`` with their own stub-backed detector and leave the
workspace's fail-closed policy at its default (non-deterministic-only).

Leak-audit clauses for this slice:
- A covered: stub upstream saw only the provisional surrogate.
- B covered: the client receives the real value back (closed-world restore).
- C N/A: closed-world restore covered by adjacent suite; not weakened here.
- D covered: the verify pass runs (clean round trip returns 200, no LeakError).
- E covered (stable): same novel candidate → same provisional surrogate across
  re-detection within one session; reuse via inbox.
- F N/A: L3 unavailable / fail-closed is covered by ``test_l3_detection``.
- G N/A: mapping secrecy / Transit deferred to #10.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_allowlist,
    get_l3_detector,
    get_mapping,
    get_rbac,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.rbac import RbacRegistry
from blindfold.review import Allowlist, ReviewInbox
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


class _StubAdjudicator:
    """Stub for Ollama: returns is_entity=True only for whitelisted candidate texts."""

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm
        self.calls: list[str] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        if candidate.text in self._confirm:
            return L3Adjudication(is_entity=True)
        return L3Adjudication(is_entity=False)


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
async def test_novel_candidate_is_auto_blindfolded_with_provisional_surrogate_and_lands_in_inbox():
    # Novel = NOT in the vendored seed. L3 adjudicator confirms as an entity, so
    # the engine mints a provisional surrogate and rewrites the prose before egress.
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Klaus"})
    detector = L3Detector(adjudicator)

    novel = "Klaus"
    # The provider only ever sees surrogates; its reply echoes the surrogate.
    # We discover the provisional surrogate via the inbox after the request.
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
    app.dependency_overrides[get_allowlist] = lambda: allowlist
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
                        {"role": "user", "content": f"Please brief {novel} tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # Non-blocking: the request returns (agents don't stall waiting for review).
    assert resp.status_code == 200

    # Clause A: the novel real value never crossed egress.
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    assert novel not in egressed

    # The inbox now holds the provisional candidate; the surrogate the inbox
    # carries is precisely what egressed upstream.
    items = inbox.list()
    assert len(items) == 1
    item = items[0]
    assert item.real == novel
    assert item.provisional_surrogate in egressed
    assert item.provisional_surrogate != novel
    # ADR-0035 decision 11 (issue #155): context_offset is derived from the
    # candidate span's own position in the mint pass, not a frontend indexOf.
    offset = item.context_offset
    assert item.context[offset : offset + len(novel)] == novel


@pytest.mark.anyio
async def test_context_offset_is_the_candidate_spans_own_position_not_a_text_search():
    # ADR-0035 decision 11 (issue #155): context_offset must come from the
    # confirmed candidate's own positional span (engine.py's mint pass), not a
    # naive text search over the context window -- a search is substring-based
    # and would mis-highlight when the real value occurs as a substring of an
    # unrelated, longer token earlier in the window ("Klausenburg" contains
    # "Klaus"). L3 confirms only the standalone "Klaus" token as an entity.
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Klaus"})
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
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    app.dependency_overrides[get_l3_detector] = lambda: detector
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "We visited Klausenburg last year. Please brief "
                                "Klaus tomorrow about the trip."
                            ),
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    item = inbox.list()[0]
    assert item.real == "Klaus"
    offset = item.context_offset
    assert item.context[offset : offset + len("Klaus")] == "Klaus"
    # A naive substring search over the context finds "Klaus" inside
    # "Klausenburg" first -- the wrong occurrence. The real offset must not be it.
    naive_offset = item.context.find("Klaus")
    assert naive_offset != offset
    assert item.context[naive_offset : naive_offset + len("Klausenburg")] == "Klausenburg"


@pytest.mark.anyio
async def test_review_inbox_api_lists_provisional_candidates():
    # ADR-0011: the management JSON API is the clean boundary the SPA consumes.
    # GET /v1/management/review-inbox returns the queued provisional candidates
    # the proxy auto-blindfolded — id, real value, provisional surrogate, the
    # small context window L3 saw (so the reviewer can decide without re-opening
    # the original transcript).
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Yasmin"})
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
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")

    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    app.dependency_overrides[get_l3_detector] = lambda: detector
    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please brief Yasmin tomorrow."}
                    ],
                },
            )
            listed = await client.get(
                "/v1/management/review-inbox",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert listed.status_code == 200
    body = listed.json()
    assert "items" in body
    items = body["items"]
    assert len(items) == 1
    entry = items[0]
    # The shape the SPA consumes: routable id + the data needed to triage.
    assert entry["real"] == "Yasmin"
    assert entry["id"]
    assert entry["provisional_surrogate"]
    assert entry["provisional_surrogate"] != "Yasmin"
    # The context carries the surrounding window L3 saw — proves it's the
    # candidate-span context (not the full payload), per ADR-0003.
    assert "Yasmin" in entry["context"]
    # ADR-0035 decision 11 (issue #155): context_offset lets the SPA highlight
    # the candidate span in place within context, at the correct occurrence.
    offset = entry["context_offset"]
    assert entry["context"][offset : offset + len("Yasmin")] == "Yasmin"


@pytest.mark.anyio
async def test_confirm_grows_entity_graph_so_l2_detects_deterministically_thereafter():
    # ADR-0010 (learning loop, bidirectional): confirming a candidate grows the
    # entity graph. The same real value on a subsequent request is detected by
    # the deterministic L2 dictionary — no L3 call needed (the system gets more
    # deterministic / less LLM-dependent over time).
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Astrid"})
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
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    app.dependency_overrides[get_l3_detector] = lambda: detector
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            # Turn 1: novel candidate → L3 fires once, inbox grows.
            await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please brief Astrid tomorrow."}
                    ],
                },
            )
            calls_after_turn_one = len(adjudicator.calls)

            # Confirm via the management API → entity graph grows.
            item_id = inbox.list()[0].id
            confirm_resp = await client.post(
                f"/v1/management/review-inbox/{item_id}/confirm"
            )
            assert confirm_resp.status_code == 200
            assert confirm_resp.json()["action"] == "confirmed"

            # Turn 2: same real value. L2 picks it up deterministically — no new
            # L3 call. The inbox is no longer holding the item.
            await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Brief Astrid again."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # Bidirectional learning: confirmation removes the candidate from L3's
    # workload. ("Astrid" is now in the entity-graph dictionary.)
    assert calls_after_turn_one == 1
    assert "Astrid" not in adjudicator.calls[calls_after_turn_one:]
    # The inbox emptied after confirm (no duplicate now in the entity graph).
    assert inbox.list() == []
    # Egress on turn 2 was still blindfolded — clause A holds across the
    # learning transition (the confirmed surrogate is the same one egressed
    # turn 1, so the provider sees a consistent surrogate world).
    assert len(recorded) == 2
    turn_two_egress = recorded[1].content.decode("utf-8")
    assert "Astrid" not in turn_two_egress
    confirmed_surrogate = mapping.surrogate_for("Astrid")
    assert confirmed_surrogate is not None
    assert confirmed_surrogate in turn_two_egress


@pytest.mark.anyio
async def test_reject_grows_allowlist_so_candidate_is_never_blindfolded_again():
    # ADR-0010 (bidirectional): rejecting a candidate grows the allowlist —
    # the token is marked NOT sensitive and is never blindfolded again. This is
    # the system's release valve for over-redaction (a quality bug, not a
    # privacy bug). L3 also stops adjudicating it (avoid wasting calls).
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Helga"})
    detector = L3Detector(adjudicator, allowlist=allowlist)

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
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    app.dependency_overrides[get_l3_detector] = lambda: detector
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            # Turn 1: novel candidate auto-blindfolded.
            await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please brief Helga tomorrow."}
                    ],
                },
            )

            # Reject via the management API → allowlist grows.
            item_id = inbox.list()[0].id
            reject_resp = await client.post(
                f"/v1/management/review-inbox/{item_id}/reject"
            )
            assert reject_resp.status_code == 200
            assert reject_resp.json()["action"] == "rejected"

            calls_after_reject = len(adjudicator.calls)

            # Turn 2: same token. Allowlist suppresses re-detection — the token
            # appears in plaintext upstream (deliberately — the user said so).
            await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Mention Helga again."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # Allowlist effects: the rejected token is recorded, the inbox is empty,
    # and L3 was not consulted again for the rejected candidate.
    assert allowlist.contains("Helga")
    assert inbox.list() == []
    assert "Helga" not in adjudicator.calls[calls_after_reject:]
    # Turn 2 egress carries the plain token — the system honored the reject.
    assert len(recorded) == 2
    turn_two_egress = recorded[1].content.decode("utf-8")
    assert "Helga" in turn_two_egress


@pytest.mark.anyio
async def test_provisional_surrogate_round_trips_so_client_sees_real_value_back():
    # Leak-audit clause B for the novel-candidate path: the provisional surrogate
    # the provider emits is restored back to the original real value before the
    # client sees the response. Closed-world: the restore key is the session,
    # which recorded the provisional surrogate when L3 confirmed the candidate.
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Iris"})
    detector = L3Detector(adjudicator, allowlist=allowlist)

    novel = "Iris"
    provisional = "Alex Brenner"  # first slot in _PROVISIONAL_POOL, ADR-0010
    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": f"Will brief {provisional}."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist
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
                        {"role": "user", "content": f"Please brief {novel}."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    body = resp.json()
    client_text = body["content"][0]["text"]
    # Clause B: the client sees the real name restored.
    assert novel in client_text
    assert provisional not in client_text
