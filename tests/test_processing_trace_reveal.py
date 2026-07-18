"""Processing trace — audited Reveal + Review-inbox deep-link (ADR-0035, issue #154).

Completes the per-hop surrogate chips (#153) with a reveal *lifecycle*, computed
live at serve time (never captured/frozen into the ring buffer — the buffer still
stores plain surrogate-token strings, per test_processing_trace.py):

- **confirmed** — the surrogate is already a re-identifiable known entity (seeded
  or L1/L2-minted) -> the SPA renders the existing audited Reveal control.
- **pending** — the surrogate is still a provisional candidate awaiting triage in
  the review inbox -> the SPA renders a "Pending review" deep-link, carrying the
  review item's own id (never the inbox's real plaintext).
- **rejected** — neither store recognizes the token (triaged away) -> no
  affordance at all.

Leak-audit clause analysis: A-E/G N/A — this endpoint never touches the request
path (mint/restore/leak_gate/resolution_gate untouched) and never returns a real
value; it only classifies already-scrubbed surrogate *tokens* against two stores
(`ReviewInbox`, `SurrogateMapping`) that never carry a real value across this
boundary either. F (fail-closed) is unchanged from #151/#153 (same `viewer` gate).
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_mapping, get_processing_trace, get_rbac, get_review_inbox
from blindfold.processing_trace import ProcessingTraceBuffer
from blindfold.rbac import RbacRegistry
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


def _hop(surrogates: list[str]) -> dict:
    return {
        "hop_index": 0,
        "hop_kind": "user",
        "l1_counts": {},
        "l1_duration_ms": 0.1,
        "l2_count": 0,
        "l2_duration_ms": 0.1,
        "l3_confirmed": 0,
        "l3_dismissed": 0,
        "l3_suppressed": 0,
        "l3_provider": None,
        "l3_duration_ms": None,
        "surrogates": surrogates,
    }


@pytest.mark.anyio
async def test_confirmed_surrogate_is_classified_reveal_eligible():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    trace = ProcessingTraceBuffer()
    trace.record(
        workspace="ws-a", endpoint="messages", streamed=False,
        outcome="passed", detected=1, duration_ms=5.0,
        hops=[_hop(["Klaus Retter"])],
    )
    mapping = SurrogateMapping()
    mapping.seed("Klaus Weber", "Klaus Retter")
    inbox = ReviewInbox()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_processing_trace] = lambda: trace
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/processing-trace",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    surrogates = resp.json()["records"][0]["hops"][0]["surrogates"]
    assert surrogates == [
        {"token": "Klaus Retter", "lifecycle": "confirmed", "review_item_id": None}
    ]


@pytest.mark.anyio
async def test_polling_the_endpoint_twice_against_the_same_buffer_stays_consistent():
    # The Processing trace view polls this endpoint every ~2s (issue #151) against
    # the same long-lived ProcessingTraceBuffer. Classifying a hop's surrogates
    # must never mutate the buffer's own stored record -- otherwise the second
    # poll re-classifies already-classified {"token", "lifecycle", ...} dicts as
    # if they were raw surrogate-token strings.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    trace = ProcessingTraceBuffer()
    trace.record(
        workspace="ws-a", endpoint="messages", streamed=False,
        outcome="passed", detected=1, duration_ms=5.0,
        hops=[_hop(["Klaus Retter"])],
    )
    mapping = SurrogateMapping()
    mapping.seed("Klaus Weber", "Klaus Retter")
    inbox = ReviewInbox()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_processing_trace] = lambda: trace
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    try:
        async with _make_client() as client:
            first = await client.get(
                "/v1/management/processing-trace",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
            second = await client.get(
                "/v1/management/processing-trace",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    expected = [{"token": "Klaus Retter", "lifecycle": "confirmed", "review_item_id": None}]
    assert first.json()["records"][0]["hops"][0]["surrogates"] == expected
    assert second.json()["records"][0]["hops"][0]["surrogates"] == expected


@pytest.mark.anyio
async def test_pending_novel_surrogate_is_classified_with_its_review_item_id():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    trace = ProcessingTraceBuffer()
    trace.record(
        workspace="ws-a", endpoint="messages", streamed=False,
        outcome="passed", detected=1, duration_ms=5.0,
        hops=[_hop(["Alex Brenner"])],
    )
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    item = inbox.upsert("Priya", context="Ping Priya about the deploy.")
    assert item.provisional_surrogate == "Alex Brenner"

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_processing_trace] = lambda: trace
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/processing-trace",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    surrogates = resp.json()["records"][0]["hops"][0]["surrogates"]
    assert surrogates == [
        {"token": "Alex Brenner", "lifecycle": "pending", "review_item_id": item.id}
    ]


@pytest.mark.anyio
async def test_surrogate_recognized_by_neither_store_is_classified_rejected():
    # A surrogate that was provisional at capture time but has since been triaged
    # away (rejected -> removed from the inbox, never seeded into the mapping) is
    # classified with no reveal/deep-link affordance at all.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    trace = ProcessingTraceBuffer()
    trace.record(
        workspace="ws-a", endpoint="messages", streamed=False,
        outcome="passed", detected=1, duration_ms=5.0,
        hops=[_hop(["Berta Falke"])],
    )
    mapping = SurrogateMapping()
    inbox = ReviewInbox()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_processing_trace] = lambda: trace
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/processing-trace",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    surrogates = resp.json()["records"][0]["hops"][0]["surrogates"]
    assert surrogates == [
        {"token": "Berta Falke", "lifecycle": "rejected", "review_item_id": None}
    ]
