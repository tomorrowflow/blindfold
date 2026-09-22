"""Payload inspection's retention hookup (ADR-0059 §2-§4, issue #399): the
armed-check + store push in `_exchange`, Unprotected-mode's existing
pipeline-skip covering "nothing retained while Unprotected mode is active"
for free, and the viewer-gated read endpoint.

Leak-audit: N/A for the retention mechanism itself -- every leaf retained
here is already in blindfolded form only (proven in
tests/test_rewritten_leaf_spans.py at the engine level; no real value is
ever constructed by this module). This file's own concern is clause F
(fail-closed / access control): the read endpoint 403s without `viewer`,
and retention must never happen for an exchange Unprotected mode bypassed.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_l3_detector,
    get_mapping,
    get_payload_inspection,
    get_rbac,
    get_review_inbox,
    get_rewritten_leaf_store,
    get_unprotected_mode,
    get_upstream_client,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.payload_inspection import PayloadInspection
from blindfold.rbac import RbacRegistry
from blindfold.review import ReviewInbox
from blindfold.rewritten_leaves import RewrittenLeafStore
from blindfold.surrogates import SurrogateMapping
from blindfold.unprotected_mode import UnprotectedMode


class _DismissAll:
    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=False)


def _scripted_upstream(text: str = "No results found.") -> UpstreamClient:
    from blindfold.upstream import UpstreamClient as _UpstreamClient

    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return _UpstreamClient(base_url="http://upstream.test", client=client)


async def _post_messages(payload: dict, overrides: dict) -> httpx.Response:
    app.dependency_overrides.update(overrides)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            return await client.post("/v1/messages", json=payload)
    finally:
        app.dependency_overrides.clear()


def test_the_store_retains_only_the_last_5_exchanges_per_workspace():
    # ADR-0059 §4: "last 5 exchanges, in memory only" -- oldest evicted.
    store = RewrittenLeafStore()

    for i in range(7):
        store.retain(workspace="ws-a", leaves=[], blocked=(i == 0))

    exchanges = store.for_workspace("ws-a")
    assert len(exchanges) == 5
    # The two oldest (including the one blocked exchange, i == 0) evicted --
    # bounded FIFO, not a filter.
    assert all(not exchange.blocked for exchange in exchanges)


@pytest.mark.anyio
async def test_a_successful_exchange_retains_its_rewritten_leaf_when_armed():
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    inspection = PayloadInspection()
    inspection.arm()
    store = RewrittenLeafStore()
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Please help Anna Schmidt today."}],
    }

    resp = await _post_messages(
        payload,
        {
            get_upstream_client: lambda: _scripted_upstream(),
            get_mapping: lambda: mapping,
            get_review_inbox: lambda: ReviewInbox(),
            get_l3_detector: lambda: L3Detector(_DismissAll()),
            get_payload_inspection: lambda: inspection,
            get_rewritten_leaf_store: lambda: store,
        },
    )

    assert resp.status_code == 200
    exchanges = store.for_workspace("default")
    assert len(exchanges) == 1
    exchange = exchanges[0]
    assert exchange.blocked is False
    assert len(exchange.leaves) == 1
    assert "Berta Vogel" in exchange.leaves[0].text
    assert "Anna Schmidt" not in exchange.leaves[0].text


@pytest.mark.anyio
async def test_nothing_is_retained_when_not_armed():
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    inspection = PayloadInspection()  # never armed
    store = RewrittenLeafStore()
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Please help Anna Schmidt today."}],
    }

    resp = await _post_messages(
        payload,
        {
            get_upstream_client: lambda: _scripted_upstream(),
            get_mapping: lambda: mapping,
            get_review_inbox: lambda: ReviewInbox(),
            get_l3_detector: lambda: L3Detector(_DismissAll()),
            get_payload_inspection: lambda: inspection,
            get_rewritten_leaf_store: lambda: store,
        },
    )

    assert resp.status_code == 200
    assert store.for_workspace("default") == []


@pytest.mark.anyio
async def test_nothing_is_retained_while_unprotected_mode_is_active():
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    inspection = PayloadInspection()
    inspection.arm()
    store = RewrittenLeafStore()
    unprotected = UnprotectedMode()
    unprotected.enable_capability()
    unprotected.enable(bound="next-request")
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Please help Anna Schmidt today."}],
    }

    resp = await _post_messages(
        payload,
        {
            get_upstream_client: lambda: _scripted_upstream(),
            get_mapping: lambda: mapping,
            get_review_inbox: lambda: ReviewInbox(),
            get_l3_detector: lambda: L3Detector(_DismissAll()),
            get_payload_inspection: lambda: inspection,
            get_rewritten_leaf_store: lambda: store,
            get_unprotected_mode: lambda: unprotected,
        },
    )

    assert resp.status_code == 200
    # The pipeline was skipped entirely -- "Anna Schmidt" reached the stub
    # upstream verbatim (Unprotected mode's own, unrelated point), and no
    # leaf record was ever built to retain, entity-free-by-construction
    # having never held in the first place for this exchange.
    assert store.for_workspace("default") == []


@pytest.mark.anyio
async def test_a_leak_gate_block_is_retained_and_marked_never_sent():
    # Reuses the #405/#406 self-poisoning fixture from
    # test_provisional_pairs_in_tool_descriptions.py -- a live, deterministic
    # leak_gate 503 on today's code (tracked as #406, not fixed here): the
    # blindfolded payload is fully constructed (so its leaves exist to
    # retain) and then discarded by the pre-egress gate.
    mapping = SurrogateMapping.from_pairs([("Bar", "Foo Baz")])
    inbox = ReviewInbox()
    inbox.upsert("Baz", context="...Baz signed off...", entity_type="organization")
    inspection = PayloadInspection()
    inspection.arm()
    store = RewrittenLeafStore()
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "checking in now."}],
        "tools": [
            {
                "name": "lookup",
                "description": "Bar reports to finance.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    }

    resp = await _post_messages(
        payload,
        {
            get_upstream_client: lambda: _scripted_upstream(),
            get_mapping: lambda: mapping,
            get_review_inbox: lambda: inbox,
            get_l3_detector: lambda: L3Detector(_DismissAll()),
            get_payload_inspection: lambda: inspection,
            get_rewritten_leaf_store: lambda: store,
        },
    )

    assert resp.status_code == 503
    exchanges = store.for_workspace("default")
    assert len(exchanges) == 1
    exchange = exchanges[0]
    assert exchange.blocked is True
    assert len(exchange.leaves) == 1
    assert exchange.leaves[0].text == "Foo Baz reports to finance."


@pytest.mark.anyio
async def test_read_endpoint_requires_viewer_role():
    rbac = RbacRegistry()  # alice has no roles on ws-a
    store = RewrittenLeafStore()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_rewritten_leaf_store] = lambda: store
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
        ) as client:
            resp = await client.get(
                "/v1/management/payload-inspection/leaves?workspace=ws-a",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_read_endpoint_returns_this_workspaces_retained_exchanges_only():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    store = RewrittenLeafStore()
    from blindfold.rewritten_leaves import RewrittenLeaf, RewrittenSpan

    store.retain(
        workspace="ws-a",
        leaves=[
            RewrittenLeaf(
                leaf_id="leaf-0",
                label="user: text block",
                text="hello Berta Vogel",
                spans=(RewrittenSpan(6, 17, "Berta Vogel", "l2"),),
            )
        ],
        blocked=False,
    )
    store.retain(workspace="ws-b", leaves=[], blocked=False)

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_rewritten_leaf_store] = lambda: store
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
        ) as client:
            resp = await client.get(
                "/v1/management/payload-inspection/leaves?workspace=ws-a",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["exchanges"]) == 1
    assert body["exchanges"][0]["leaves"][0]["text"] == "hello Berta Vogel"
    assert body["exchanges"][0]["leaves"][0]["spans"][0]["surrogate"] == "Berta Vogel"
