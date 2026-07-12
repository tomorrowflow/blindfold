"""GET /v1/status (issue #92): ungated, scrubbed-by-construction status endpoint.

The single status contract consumed by both the management app's Home/Status view
and the future macOS menu bar item. Deliberately outside /v1/management/* (ADR-0011)
-- no workspace scoping, no role gate; the security boundary is the existing
loopback-only bind (ADR-0021).

Leak-audit clauses touched by this slice: F only (the fail-closed body/audit/log
funnel now also feeds `blocks.recent`, scrubbed-reason invariant applies there too).
A-E/G are N/A -- this endpoint reads already-scrubbed state (dependency health,
audit-derived block records, counts); it doesn't blindfold/restore/mint anything
itself. The leak-audit-style assertion (no entity plaintext in `blocks.recent`)
lives in its own test below.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_block_history,
    get_entity_graph,
    get_l3_detector,
    get_l3_health_probe,
    get_store_health_probe,
    get_transit_health_probe,
    get_upstream_client,
    get_upstream_health,
)
from blindfold.app import get_review_inbox
from blindfold.entity_graph import EntityGraph
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.status import BlockHistory, CachedHealthProbe, DependencyHealth, RecentFailureHealth
from blindfold.upstream import UpstreamClient, UpstreamError


@pytest.mark.anyio
async def test_status_endpoint_returns_the_settled_contract_shape():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.get("/v1/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] in ("protected", "degraded")
    assert set(body["dependencies"].keys()) == {"upstream", "l3", "transit", "store"}
    for dependency in body["dependencies"].values():
        assert "healthy" in dependency
    assert set(body["blocks"].keys()) == {"window_minutes", "count", "recent"}
    assert isinstance(body["blocks"]["recent"], list)
    assert set(body["review_inbox"].keys()) == {"pending"}
    assert set(body["config"].keys()) == {
        "upstream_base_url",
        "l3_model",
        "fail_closed_policy",
    }


@pytest.mark.anyio
async def test_status_reports_empty_store_true_when_no_workspace_exists():
    # Issue #106: the SPA's forced-redirect-to-Setup (slice 4) keys off this signal.
    app.dependency_overrides[get_entity_graph] = lambda: EntityGraph()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            resp = await client.get("/v1/status")
    finally:
        app.dependency_overrides.clear()

    assert resp.json()["empty_store"] is True


@pytest.mark.anyio
async def test_status_reports_empty_store_false_once_an_entity_exists():
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach")
    app.dependency_overrides[get_entity_graph] = lambda: graph
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            resp = await client.get("/v1/status")
    finally:
        app.dependency_overrides.clear()

    body = resp.json()
    assert body["empty_store"] is False
    # The empty-store signal is a boolean only -- the real canonical_name must never
    # surface on this ungated, loopback-only endpoint (issue #106 AC).
    assert "Martin Bach" not in str(body)


class _FakeProbe:
    def __init__(self, health: DependencyHealth) -> None:
        self._health = health

    def check(self) -> DependencyHealth:
        return self._health


@pytest.mark.anyio
@pytest.mark.parametrize(
    "dependency_name, override_getter",
    [
        ("upstream", get_upstream_health),
        ("l3", get_l3_health_probe),
        ("transit", get_transit_health_probe),
        ("store", get_store_health_probe),
    ],
)
async def test_state_flips_to_degraded_when_a_stubbed_dependency_is_down(
    dependency_name, override_getter
):
    # Stub every dependency healthy first so only the one under test can be
    # unhealthy -- otherwise the real default l3 probe (no BLINDFOLD_OLLAMA_MODEL
    # in the test env) would already report unhealthy regardless of this test.
    for getter in (get_upstream_health, get_l3_health_probe, get_transit_health_probe, get_store_health_probe):
        app.dependency_overrides[getter] = lambda: _FakeProbe(DependencyHealth(healthy=True))
    unhealthy = DependencyHealth(healthy=False, detail="stubbed outage")
    app.dependency_overrides[override_getter] = lambda: _FakeProbe(unhealthy)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            resp = await client.get("/v1/status")
    finally:
        app.dependency_overrides.clear()

    body = resp.json()
    assert body["state"] == "degraded"
    assert body["dependencies"][dependency_name] == {"healthy": False, "detail": "stubbed outage"}
    other_names = {"upstream", "l3", "transit", "store"} - {dependency_name}
    for other in other_names:
        assert body["dependencies"][other]["healthy"] is True


@pytest.mark.anyio
async def test_state_is_protected_when_every_dependency_is_healthy():
    for getter in (get_upstream_health, get_l3_health_probe, get_transit_health_probe, get_store_health_probe):
        app.dependency_overrides[getter] = lambda: _FakeProbe(DependencyHealth(healthy=True))
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            resp = await client.get("/v1/status")
    finally:
        app.dependency_overrides.clear()

    assert resp.json()["state"] == "protected"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "override_getter", [get_l3_health_probe, get_transit_health_probe, get_store_health_probe]
)
async def test_rapid_repeated_polls_do_not_multiply_probe_calls_to_a_stubbed_dependency(
    override_getter,
):
    calls = []

    def probe() -> DependencyHealth:
        calls.append(1)
        return DependencyHealth(healthy=True)

    # A generous TTL relative to the test's own wall-clock duration -- the point is
    # collapsing calls within the cache window, not timing precision. Built once,
    # outside the override lambda: the whole point is that the SAME cached instance
    # persists across requests (a fresh instance per request would reset the cache
    # every time and prove nothing).
    shared_probe = CachedHealthProbe(probe, ttl_seconds=30.0)
    app.dependency_overrides[override_getter] = lambda: shared_probe
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            for _ in range(5):
                resp = await client.get("/v1/status")
                assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()

    assert len(calls) == 1


class _UnavailableAdjudicator:
    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise ConnectionError("ollama unreachable")


def _make_stub_upstream() -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_blocks_recent_carries_the_scrubbed_reason_and_management_url_never_entity_plaintext():
    # Leak-audit-style assertion (issue #92 AC): after a fail-closed block, it appears
    # in blocks.recent with the scrubbed reason only -- the real candidate's plaintext
    # must never reach this observability surface, mirroring #91's 503-body contract.
    block_history = BlockHistory(window_minutes=15)
    app.dependency_overrides[get_block_history] = lambda: block_history
    app.dependency_overrides[get_upstream_client] = _make_stub_upstream
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            block_resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "Please brief Quentin."}],
                },
            )
            assert block_resp.status_code == 503

            status_resp = await client.get("/v1/status")
    finally:
        app.dependency_overrides.clear()

    body = status_resp.json()
    assert body["blocks"]["count"] == 1
    assert body["blocks"]["window_minutes"] == 15
    recent = body["blocks"]["recent"]
    assert len(recent) == 1
    record = recent[0]
    assert record["sub_reason"] == "l3_unavailable"
    assert record["management_url"] == block_resp.json()["error"]["management_url"]
    assert record["scrubbed_reason"] == block_resp.json()["error"]["reason"]
    assert "ts" in record
    # The real entity ("Quentin") must never appear anywhere in this payload.
    assert "Quentin" not in str(body)


class _FailingUpstream:
    async def send_messages(self, blinded, headers):
        raise UpstreamError(
            status_code=502, sub_reason="upstream_unreachable", message="failed to reach upstream"
        )


@pytest.mark.anyio
async def test_a_real_upstream_boundary_failure_passively_marks_upstream_unhealthy():
    # Issue #92: `/v1/status`'s upstream health is the passive RecentFailureHealth
    # signal fed by the existing `_upstream_error_response` funnel (#86) -- no
    # standalone active probe for the paid provider. Proves the real wiring, not
    # just the override seam already covered by the degraded-flip parametrized test.
    upstream_health = RecentFailureHealth(unhealthy_window_seconds=60.0)
    app.dependency_overrides[get_upstream_health] = lambda: upstream_health
    app.dependency_overrides[get_upstream_client] = lambda: _FailingUpstream()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            resp = await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert resp.json()["error"]["type"] == "blindfold_upstream_error"

            status_resp = await client.get("/v1/status")
    finally:
        app.dependency_overrides.clear()

    dependency = status_resp.json()["dependencies"]["upstream"]
    assert dependency == {"healthy": False, "detail": "upstream_unreachable"}


@pytest.mark.anyio
async def test_review_inbox_pending_reflects_the_real_inbox_count():
    inbox = ReviewInbox()
    inbox.upsert(real="Quentin", context="Please brief Quentin.")
    inbox.upsert(real="Distinct Other Name", context="Loop in Distinct Other Name.")
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            resp = await client.get("/v1/status")
    finally:
        app.dependency_overrides.clear()

    body = resp.json()
    assert body["review_inbox"] == {"pending": 2}
    # Scrubbed by construction: the pending count is the only thing exposed here,
    # never the candidates' own real values.
    assert "Quentin" not in str(body)
    assert "Distinct Other Name" not in str(body)


@pytest.mark.anyio
async def test_config_never_carries_the_openbao_token_or_any_secret(monkeypatch):
    secret_token = "s.super-secret-transit-token"
    monkeypatch.setenv("BLINDFOLD_OPENBAO_TOKEN", secret_token)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.get("/v1/status")

    body = resp.json()
    assert set(body["config"].keys()) == {
        "upstream_base_url",
        "l3_model",
        "fail_closed_policy",
    }
    assert secret_token not in str(body)


@pytest.mark.anyio
async def test_no_auth_or_identity_headers_are_required():
    # ADR-0011: deliberately outside /v1/management/* -- not workspace-scoped, not
    # role-gated. No x-api-key/authorization/x-blindfold-workspace/x-blindfold-identity
    # header is sent here at all; the endpoint must still answer 200.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.get("/v1/status")

    assert resp.status_code == 200
