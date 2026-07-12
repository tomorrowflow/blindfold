"""Upstream client seam (issue #101): construction-time failures, not just request-time.

#86 mapped *request-time* upstream transport/HTTP errors to the structured
``blindfold_upstream_error`` envelope. That mapping only covers failures raised while
sending a request through an already-built ``UpstreamClient``. But
``get_upstream_client``/``get_openai_upstream_client`` build a fresh ``UpstreamClient``
(and its inner ``httpx.AsyncClient``) eagerly, during FastAPI **dependency resolution**
-- before a route handler's own try/except ever runs. A construction failure there
(observed live: a missing CA bundle after a venv rebuild, raising ``FileNotFoundError``
from httpx's SSL context setup) previously escaped as a raw ASGI 500 traceback instead
of the same structured envelope every other upstream failure now returns.

This file covers two seams:
- ``UpstreamClient.__init__`` itself maps a construction failure to ``UpstreamError``
  (mirrors ``_map_httpx_error``'s job, one level earlier).
- The FastAPI app maps an ``UpstreamError`` raised during dependency resolution (not
  just inside a route body) to the same JSON response -- the fix for the actual
  hard-500 this issue reports.

N/A this slice (leak-audit clauses): no blindfold/restore pass runs on this failure
path -- the failure happens before any payload is blinded or sent -- so A/B/C/D/E/G do
not apply. F: N/A -- this is an availability/contract bug (client construction), not an
L3 fail-closed privacy path; the mapped error shape is deliberately distinct from
``blindfold_fail_closed``, same as #86.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold import upstream as upstream_module
from blindfold.app import (
    app,
    get_audit_log,
    get_openai_upstream_client,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.upstream import UpstreamClient, UpstreamError


def _deterministic_only_policies() -> WorkspacePolicies:
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def test_upstream_client_construction_failure_maps_to_structured_upstream_error(
    monkeypatch,
):
    # Reproduces the live incident: httpx.AsyncClient(...) raises while building its
    # SSL context (a missing CA bundle) during UpstreamClient.__init__ -- before any
    # request is ever sent.
    def _raise(*args, **kwargs):
        raise FileNotFoundError(
            "[Errno 2] No such file or directory: '/venv/ca-bundle.crt'"
        )

    monkeypatch.setattr(upstream_module.httpx, "AsyncClient", _raise)

    with pytest.raises(UpstreamError) as excinfo:
        UpstreamClient(base_url="http://upstream.test")

    assert excinfo.value.status_code in (502, 503)
    assert excinfo.value.sub_reason == "upstream_client_init_failed"
    # Scrubbed: the mapped message never echoes the raw filesystem detail.
    assert "ca-bundle" not in str(excinfo.value)


def _raising_upstream_client_factory() -> UpstreamClient:
    # Simulates a construction-time failure the way UpstreamClient.__init__ now
    # raises one -- an UpstreamError surfacing straight out of a Depends() callable,
    # i.e. during dependency resolution rather than inside the route body.
    raise UpstreamError(
        status_code=502,
        sub_reason="upstream_client_init_failed",
        message="failed to construct upstream client",
    )


@pytest.mark.anyio
async def test_messages_endpoint_maps_a_dependency_resolution_failure_not_a_raw_500():
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = _raising_upstream_client_factory
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["type"] == "blindfold_upstream_error"
    assert body["error"]["code"] != "blindfold_fail_closed"
    assert body["error"]["sub_reason"] == "upstream_client_init_failed"
    assert any(r.event == "upstream-error" for r in audit_log.records)


@pytest.mark.anyio
async def test_chat_completions_endpoint_maps_the_same_dependency_resolution_failure():
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_openai_upstream_client] = _raising_upstream_client_factory
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["type"] == "blindfold_upstream_error"
    assert body["error"]["sub_reason"] == "upstream_client_init_failed"
    assert any(r.event == "upstream-error" for r in audit_log.records)
