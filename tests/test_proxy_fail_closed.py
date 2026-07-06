"""HTTP proxy seam: fail-closed policy + per-workspace degrade opt-in (ADR-0009).

Drives the request path with L3 (Ollama) forced unavailable via the stubbed-Ollama
boundary, and asserts the proxy blocks by default — nothing novel egresses unscanned
(leak-audit clause F). The per-workspace degrade opt-in lets the request through in
deterministic-only mode and is captured as an audit record. Replaces the interim
HTTP 500 from the verify_pass guard added in #2 with a structured block + audit.

SEC-7 (issue #48): the *shipped default* (no adjudicator override at all) is asserted
to fail closed too — previously the default (`_NullAdjudicator`, now
`_UnconfiguredAdjudicator`) silently classified every novel candidate as "not an
entity" instead of signalling L3 unavailability, so a novel unresolved candidate
egressed unscanned (fail-*open* by default). The l3-unavailable 503 also carries the
ADR-0009 contract: a stable `blindfold_fail_closed`/`l3_unavailable` code, a scrubbed
(hashed-id) candidate reference — never the plaintext — and a remedy naming all three
on-ramps (curate in the review inbox, deterministic-only opt-in, or configure L3).

Leak-audit clauses asserted here:
- A: blocked path -> the stub upstream recorded zero requests (no egress at all).
- F: L3 unavailable (by explicit override AND by the true production default) ->
  block by default; deterministic-only opt-in produces an audited pass; leak-gate /
  resolution-gate violations (the two halves of the former verify pass, split by
  ADR-0020) route to the same structured block + audit, scrubbed identically.

N/A this slice: B/C/D (no successful round trip with novel-entity restore in the
blocked path), E (no PII / coherent-world surrogates), G (no store-touching changes).
"""

from __future__ import annotations

import logging

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
async def test_default_production_wiring_blocks_a_novel_candidate_with_no_overrides():
    # SEC-7 (issue #48): with NO dependency_overrides at all -- the actual shipped
    # default, not a test double forced into outage -- a novel unresolved candidate
    # must still block. Before this fix, the default adjudicator (`_NullAdjudicator`)
    # silently classified every novel candidate as "not an entity" instead of
    # signalling that no real L3 is configured, so the payload egressed unscanned:
    # fail-*open* by default, contradicting ADR-0009.
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
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
    assert resp.json()["error"]["event"] == "blocked-l3-unavailable"
    # Block came BEFORE egress -- the novel candidate never reached the upstream.
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


@pytest.mark.anyio
async def test_l3_unavailable_503_carries_the_stable_code_and_three_on_ramp_remedy():
    # ADR-0009 / SEC-7 (issue #48): the fail-closed 503 for the l3-unavailable case
    # carries a stable machine code (`blindfold_fail_closed`) + sub-reason
    # (`l3_unavailable`) so a client SDK can route on it without string-matching
    # the human-readable message, and a remedy naming all three on-ramps: curate in
    # the review inbox, enable the deterministic-only degrade, or configure L3.
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
    error = resp.json()["error"]
    assert error["code"] == "blindfold_fail_closed"
    assert error["sub_reason"] == "l3_unavailable"
    remedy = error["remedy"]
    assert "review inbox" in remedy
    assert "deterministic-only" in remedy
    assert "L3" in remedy


@pytest.mark.anyio
async def test_l3_unavailable_scrubs_the_candidate_value_from_body_audit_and_log(
    caplog,
):
    # SEC-7 (issue #48): the l3-unavailable block names *which* candidate triggered
    # it -- but only via a scrubbed reference, mirroring #40's leak-gate scrub for
    # the blocked-leak path. "Quentin" is a novel candidate with no surrogate ever
    # minted for it, so the scrubbed reference falls back to a hashed id -- the
    # plaintext must not reach the 503 body, the audit record, or the log.
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_l3_adjudicator] = lambda: _UnavailableAdjudicator()
    try:
        transport = httpx.ASGITransport(app=app)
        with caplog.at_level(logging.WARNING):
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
                    headers={"x-blindfold-workspace": "zeta"},
                )
    finally:
        app.dependency_overrides.clear()

    body_message = resp.json()["error"]["message"]
    audit_record = next(
        r
        for r in audit_log.records
        if r.workspace == "zeta" and r.event == "blocked-l3-unavailable"
    )
    log_messages = [record.getMessage() for record in caplog.records]

    assert "Quentin" not in body_message
    assert "Quentin" not in audit_record.reason
    assert not any("Quentin" in m for m in log_messages), log_messages

    assert "hash:" in body_message
    assert body_message == audit_record.reason
    assert any(body_message in m for m in log_messages), log_messages


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
async def test_leak_gate_violation_returns_structured_block_with_audit_not_a_bare_500():
    # AC: "Replace the interim guard from #2 (verify_pass violation raising → HTTP 500)
    # with proper fail-closed block semantics + an audit record."
    # When the leak gate detects a real entity value about to egress (i.e. the
    # blindfold engine missed it), the proxy MUST emit the structured fail-closed
    # block — same shape as the L3-unavailable block — and write an audit record,
    # NOT a bare 500. (The pre-egress prevention property is covered separately by
    # test_pre_egress_leak_gate_blocks_before_anything_reaches_upstream.)
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    policies = WorkspacePolicies()
    # SEC-7 (#48): isolate the leak-gate block from the (now fail-closed-by-default)
    # L3 scan -- "Brief"/"Quentin" would otherwise trip blocked-l3-unavailable first.
    # This test's concern is the leak gate specifically, so skip L3 entirely.
    policies.opt_in_deterministic_only("gamma")
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_mapping] = lambda: _LeakyMapping(leaked_real="Quentin")
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


@pytest.mark.anyio
async def test_pre_egress_leak_gate_blocks_before_anything_reaches_upstream():
    # SEC-5 / issue #47: the leak gate is a *prevention* gate before egress, not a
    # post-hoc detection after the blinded payload already reached the provider.
    # Same blindfold-engine miss as above (_LeakyMapping), but this time the stub
    # upstream MUST record zero requests — the block happens before
    # upstream.send_messages is ever called (leak-audit clause A: no egress at all).
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only("gamma")
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_mapping] = lambda: _LeakyMapping(leaked_real="Quentin")
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
                    "messages": [{"role": "user", "content": "Brief Quentin now."}],
                },
                headers={"x-blindfold-workspace": "gamma"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    assert resp.json()["error"]["event"] == "blocked-leak"
    assert recorded == [], "leak gate must block before the payload reaches upstream"


@pytest.mark.anyio
async def test_leak_gate_violation_scrubs_the_real_value_from_body_audit_and_log(
    caplog,
):
    # Issue #40 (SEC-3): a leak_gate violation used to put the real value into the
    # 503 body, the audit record, AND the process log at WARNING — a privacy bug on
    # the error/observability surface itself. All three sinks must instead carry one
    # identical scrubbed reason string that names the entity by surrogate/hashed id.
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only("gamma")
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_mapping] = lambda: _LeakyMapping(leaked_real="Quentin")
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    try:
        transport = httpx.ASGITransport(app=app)
        with caplog.at_level(logging.WARNING, logger="blindfold.engine"):
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

    body_message = resp.json()["error"]["message"]
    audit_record = next(
        r
        for r in audit_log.records
        if r.workspace == "gamma" and r.event == "blocked-leak"
    )
    log_messages = [record.getMessage() for record in caplog.records]

    assert "Quentin" not in body_message
    assert "Quentin" not in audit_record.reason
    assert not any("Quentin" in m for m in log_messages), log_messages

    # Diagnosable via the scrubbed reference (no surrogate was ever minted for
    # "Quentin" here, so it falls back to a hashed id) — and identical everywhere.
    assert "hash:" in body_message
    assert body_message == audit_record.reason
    assert any(body_message in m for m in log_messages), log_messages
