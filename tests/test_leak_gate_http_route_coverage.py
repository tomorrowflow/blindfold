"""Leak-gate route coverage for ``/v1/chat/completions`` and
``/v1/messages/count_tokens`` (issue #316).

The pre-egress leak gate's HTTP-level blocked-leak contract -- 503, the
``blindfold_blocked``/``blocked-leak`` body shape (ADR-0027), exactly one audit
record, and a scrubbed reason with no plaintext real value anywhere in
body/audit/log -- was previously pinned only for ``/v1/messages``
(``tests/test_proxy_fail_closed.py``). ``/v1/chat/completions`` had only a clean
round-trip test (``tests/test_proxy_round_trip_openai.py``); ``/v1/messages/
count_tokens`` had no leak-gate test at all, even though count_tokens has no
restore side and so the leak gate is its *sole* privacy gate. A leak-gate call
accidentally dropped from either handler stayed green before this file existed.

Mirrors ``test_proxy_fail_closed.py``'s own ``_LeakyMapping`` double (a mapping
whose ``real_values()`` knows about an entity ``entities()`` does NOT expose as a
detection surface, simulating a blindfold-engine miss) and stub-upstream wiring,
one route-level test per endpoint.

Leak-audit clauses asserted here:
- A: the stub upstream recorded zero requests -- the leak gate is a *prevention*
  gate before egress (SEC-5), not post-hoc detection.
- F: fail-closed (ADR-0009) -- a detected leak blocks by default; the structured
  block + audit record replaces a bare 500.
- The scrubbed-reason invariant (SEC-3, issue #40): the real value never appears
  in the 503 body, the audit record, or the process log.

N/A this slice: B/C/D/E/G -- no restore, mapping-mint, or store-touching change;
this is additive HTTP-route test coverage for the leak gate wiring already shipped
in #47/#48/#91, not a new mechanism. count_tokens' own B/C are N/A structurally
(no surrogate text ever returns from that route -- see
tests/test_proxy_count_tokens.py's own module docstring for that route's
standing N/A rationale).
"""

from __future__ import annotations

import logging

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_mapping,
    get_openai_upstream_client,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.policy import WorkspacePolicies
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _LeakyMapping(SurrogateMapping):
    """Test double: ``real_values()`` knows about an entity that ``entities()``
    does NOT expose as a detection surface -- simulates an engine miss, mirroring
    ``test_proxy_fail_closed.py``'s own double for the ``/v1/messages`` route.
    """

    def __init__(self, leaked_real: str) -> None:
        super().__init__()
        self._leaked_real = leaked_real

    def real_values(self) -> list[str]:
        return [self._leaked_real]


def _make_stub_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_chat_completions_leak_gate_violation_blocks_with_audit_and_scrubs_the_real_value(
    caplog,
):
    # AC1: /v1/chat/completions -- a hop carrying a known real value the blinder
    # is made to miss yields the fail-closed 503, the actionable body shape
    # (ADR-0027), one audit event, and a scrubbed reason.
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    policies = WorkspacePolicies()
    # SEC-7 (#48): isolate the leak-gate block from the fail-closed-by-default L3
    # scan, the same way test_proxy_fail_closed.py's leak-gate tests do.
    policies.opt_in_deterministic_only("gamma")
    app.dependency_overrides[get_openai_upstream_client] = lambda: _make_stub_upstream(
        recorded
    )
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    app.dependency_overrides[get_mapping] = lambda: _LeakyMapping(leaked_real="Quentin")
    try:
        transport = httpx.ASGITransport(app=app)
        with caplog.at_level(logging.WARNING, logger="blindfold.engine"):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://proxy.test"
            ) as client:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "user", "content": "Brief Quentin now."}
                        ],
                    },
                    headers={"x-blindfold-workspace": "gamma"},
                )
    finally:
        app.dependency_overrides.clear()

    # Not a bare 500 -- the structured fail-closed block, same shape as /v1/messages.
    assert resp.status_code == 503
    error = resp.json()["error"]
    assert error["type"] == "blindfold_blocked"
    assert error["event"] == "blocked-leak"
    assert error["workspace"] == "gamma"

    # Clause A: block happens BEFORE egress -- the leak gate is a prevention gate.
    assert recorded == []

    # Exactly one audit event for this exchange.
    matching_records = [
        r for r in audit_log.records if r.workspace == "gamma" and r.event == "blocked-leak"
    ]
    assert len(matching_records) == 1
    audit_record = matching_records[0]

    body_reason = error["reason"]
    log_messages = [record.getMessage() for record in caplog.records]

    # SEC-3: the real value never appears in the body, the audit record, or the log.
    assert "Quentin" not in body_reason
    assert "Quentin" not in error["message"]
    assert "Quentin" not in audit_record.reason
    assert not any("Quentin" in m for m in log_messages), log_messages

    # Diagnosable via the identical scrubbed reference everywhere.
    assert "hash:" in body_reason
    assert body_reason == audit_record.reason
    assert any(body_reason in m for m in log_messages), log_messages


@pytest.mark.anyio
async def test_count_tokens_leak_gate_violation_blocks_with_audit_and_scrubs_the_real_value(
    caplog,
):
    # AC2: /v1/messages/count_tokens -- same contract, minus restore-side
    # assertions (count_tokens has no restore side -- the leak gate is its SOLE
    # privacy gate, per the issue's own framing).
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only("gamma")
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    app.dependency_overrides[get_mapping] = lambda: _LeakyMapping(leaked_real="Quentin")
    try:
        transport = httpx.ASGITransport(app=app)
        with caplog.at_level(logging.WARNING, logger="blindfold.engine"):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://proxy.test"
            ) as client:
                resp = await client.post(
                    "/v1/messages/count_tokens",
                    json={
                        "model": "m",
                        "messages": [
                            {"role": "user", "content": "Brief Quentin now."}
                        ],
                    },
                    headers={"x-blindfold-workspace": "gamma"},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    error = resp.json()["error"]
    assert error["type"] == "blindfold_blocked"
    assert error["event"] == "blocked-leak"
    assert error["workspace"] == "gamma"

    # Clause A: block happens BEFORE egress -- no count call reached the upstream.
    assert recorded == []

    matching_records = [
        r for r in audit_log.records if r.workspace == "gamma" and r.event == "blocked-leak"
    ]
    assert len(matching_records) == 1
    audit_record = matching_records[0]

    body_reason = error["reason"]
    log_messages = [record.getMessage() for record in caplog.records]

    assert "Quentin" not in body_reason
    assert "Quentin" not in error["message"]
    assert "Quentin" not in audit_record.reason
    assert not any("Quentin" in m for m in log_messages), log_messages

    assert "hash:" in body_reason
    assert body_reason == audit_record.reason
    assert any(body_reason in m for m in log_messages), log_messages
