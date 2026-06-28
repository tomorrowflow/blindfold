"""HTTP proxy seam: fail-closed policy + per-workspace degrade opt-in (ADR-0009).

Drives the request path with L3 (Ollama) forced unavailable via the stubbed-Ollama
boundary, and asserts the proxy blocks by default — nothing novel egresses unscanned
(leak-audit clause F). The per-workspace degrade opt-in lets the request through in
deterministic-only mode and is captured as an audit record. Replaces the interim
HTTP 500 from the verify_pass guard added in #2 with a structured block + audit.

Leak-audit clauses asserted here:
- A: blocked path -> the stub upstream recorded zero requests (no egress at all).
- F: L3 unavailable -> block by default; deterministic-only opt-in produces an
  audited pass; verify-pass violations route to the same structured block + audit.

N/A this slice: B/C/D (no successful round trip with novel-entity restore in the
blocked path), E (no PII / coherent-world surrogates), G (no store-touching changes).
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_l3_adjudicator,
    get_mapping,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.l3 import CandidateSpan, L3Adjudication
from blindfold.policy import WorkspacePolicies
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _UnavailableAdjudicator:
    """Stubbed-Ollama at its network boundary, forced into outage to exercise fail-closed."""

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise ConnectionError("ollama unreachable")


def _make_stub_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "ok"}]},
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_proxy_blocks_when_l3_unavailable_for_a_novel_candidate():
    # ADR-0009 / leak-audit clause F: with L3 forced unavailable AND a novel candidate
    # in the payload, the proxy MUST block — nothing novel may egress unscanned. The
    # block surfaces as a clear, structured response (not a bare 500), and the stub
    # upstream sees ZERO requests (clause A: no egress at all on the blocked path).
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_l3_adjudicator] = lambda: _UnavailableAdjudicator()
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
    body = resp.json()
    assert body["error"]["type"] == "blindfold_blocked"
    # Block came BEFORE egress.
    assert recorded == []


@pytest.mark.anyio
async def test_per_workspace_deterministic_only_opt_in_produces_an_audited_pass():
    # ADR-0009: the explicit, per-workspace opt-in degrades to deterministic-only
    # operation — L3 is SKIPPED for that workspace (so an Ollama outage no longer
    # blocks), L1+L2 still protect known entities, and the degraded pass MUST be
    # captured in the audit log (the "explicit, logged" half of the policy).
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only("alpha")
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    # L3 is forced unavailable to prove the opt-in actually bypasses the L3 call —
    # if the proxy still called L3 for an opted-in workspace, this would block.
    app.dependency_overrides[get_l3_adjudicator] = lambda: _UnavailableAdjudicator()
    app.dependency_overrides[get_workspace_policies] = lambda: policies
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
                headers={"x-blindfold-workspace": "alpha"},
            )
    finally:
        app.dependency_overrides.clear()

    # Degraded pass succeeded (200), reached upstream (egress happened), and was audited.
    assert resp.status_code == 200
    assert len(recorded) == 1
    events = [
        (r.workspace, r.event)
        for r in audit_log.records
        if r.event == "deterministic-only-pass"
    ]
    assert ("alpha", "deterministic-only-pass") in events


@pytest.mark.anyio
async def test_block_response_explains_why_and_how_to_opt_into_degraded_mode():
    # AC: "Blocked requests return clear feedback explaining why and how to opt
    # into degraded mode." The block body MUST identify (a) the block event so
    # clients can route on it, (b) a human-readable reason naming L3, and (c) the
    # remedy — the per-workspace deterministic-only opt-in. The audit record
    # carries the same workspace + event so operators can correlate later.
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_l3_adjudicator] = lambda: _UnavailableAdjudicator()
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
                headers={"x-blindfold-workspace": "beta"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    error = resp.json()["error"]
    assert error["type"] == "blindfold_blocked"
    assert error["event"] == "blocked-l3-unavailable"
    assert error["workspace"] == "beta"
    # "Why" — names L3 / the unavailable subsystem so the client knows what failed.
    assert "L3" in error["message"]
    # "How to opt in" — names the deterministic-only opt-in path explicitly.
    assert "deterministic-only" in error["remedy"]
    # Block was audited with the same workspace + event the client got told.
    assert any(
        r.workspace == "beta" and r.event == "blocked-l3-unavailable"
        for r in audit_log.records
    )


class _LeakyMapping(SurrogateMapping):
    """Test double: ``real_values()`` knows about an entity that ``entities()`` does
    NOT expose as a detection surface. Simulates an engine miss — the real value
    egresses unblindfolded, and the verify pass is the last line of defence.
    """

    def __init__(self, leaked_real: str) -> None:
        super().__init__()
        self._leaked_real = leaked_real

    def real_values(self) -> list[str]:
        return [self._leaked_real]


@pytest.mark.anyio
async def test_verify_pass_leak_returns_structured_block_with_audit_not_a_bare_500():
    # AC: "Replace the interim guard from #2 (verify_pass violation raising → HTTP 500)
    # with proper fail-closed block semantics + an audit record."
    # When the verify pass detects a real entity value about to egress (i.e. the
    # blindfold engine missed it), the proxy MUST emit the structured fail-closed
    # block — same shape as the L3-unavailable block — and write an audit record,
    # NOT a bare 500.
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_mapping] = lambda: _LeakyMapping(leaked_real="Quentin")
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "Brief Quentin now."}],
                },
                headers={"x-blindfold-workspace": "gamma"},
            )
    finally:
        app.dependency_overrides.clear()

    # NOT a 500. The structured block uses 503 (same as the L3-unavailable block).
    assert resp.status_code == 503
    error = resp.json()["error"]
    assert error["type"] == "blindfold_blocked"
    assert error["event"] == "blocked-leak"
    assert error["workspace"] == "gamma"
    assert any(
        r.workspace == "gamma" and r.event == "blocked-leak"
        for r in audit_log.records
    )
