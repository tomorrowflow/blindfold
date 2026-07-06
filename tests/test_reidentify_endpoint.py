"""Re-identification endpoint: GET /v1/management/surrogate/{surrogate}/real (ADR-0015 / issue #10).

Drives the management endpoint through the FastAPI test client.
Transit and the re-identification store are stubbed: Transit via an httpx.MockTransport
at the network boundary; the store via a pre-seeded in-memory mapping.

Leak-audit clause analysis:
- A/B/C/D/E — N/A: this slice does not add a new proxy request path; the existing
  proxy path is unchanged.
- F (access control) — covered: endpoint returns 403 when the calling identity lacks
  the ``re-identifier`` role on the requested workspace; workspace-scoped (surrogate
  from workspace A is NOT re-identifiable by a caller holding re-identifier only on B).
  503 when Transit is not configured (env vars absent) — covered by test 6.
- G (mapping secrecy) — covered by design: the endpoint decrypts via Transit (stubbed
  at network boundary here); the real value is never stored in the audit record (only
  the surrogate is recorded), honoring the CONTEXT invariant.

SEC-8 (issue #41): a denied (403) or failed (404 / 503 / decrypt exception) re-identify
attempt writes an audit event too — ``re-identify-denied`` / ``re-identify-failed`` — so
a probing caller always leaves a trail (ADR-0018's "audit even misses" principle). Every
such record still carries only the surrogate and outcome, never the plaintext real value.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from blindfold.app import app, get_audit_log, get_rbac, get_reidentify_store, get_transit_client
from blindfold.policy import AuditLog
from blindfold.rbac import RbacRegistry
from blindfold.transit import TransitClient


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _stub_transit(surrogate_to_plaintext: dict[str, str]) -> TransitClient:
    """Return a TransitClient whose decrypt() returns pre-canned plaintext.

    The mapping key is ciphertext (same as the surrogate key here for test simplicity),
    and the mock Transport serves the appropriate base64-encoded plaintext.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        ciphertext = body.get("ciphertext", "")
        if ciphertext in surrogate_to_plaintext:
            return httpx.Response(
                200,
                json={"data": {"plaintext": _b64(surrogate_to_plaintext[ciphertext])}},
            )
        return httpx.Response(400, json={"errors": ["no such ciphertext"]})

    return TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


# ---------------------------------------------------------------------------
# Helpers: stub store
# ---------------------------------------------------------------------------

from blindfold.reidentify import InMemoryReIdentificationStore


def _store_with(entries: dict[tuple[str, str], str]) -> InMemoryReIdentificationStore:
    """(surrogate, workspace) → ciphertext mapping for the test store."""
    return InMemoryReIdentificationStore(entries)


# ---------------------------------------------------------------------------
# 1. Authorized caller gets real value back
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reidentify_returns_real_value_for_authorized_re_identifier():
    surrogate = "Clara Hoffmann"
    ciphertext = "vault:v1:enc:martin-bach"
    plaintext = "Martin Bach"

    rbac = RbacRegistry()
    rbac.grant("alice", "default", "re-identifier")

    store = _store_with({(surrogate, "default"): ciphertext})
    transit = _stub_transit({ciphertext: plaintext})
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                f"/v1/management/surrogate/{surrogate}/real",
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "default",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["real"] == plaintext
    assert data["surrogate"] == surrogate


# ---------------------------------------------------------------------------
# 2. 403 when caller lacks re-identifier role
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reidentify_returns_403_without_re_identifier_role():
    surrogate = "Clara Hoffmann"
    rbac = RbacRegistry()
    # alice has viewer, not re-identifier
    rbac.grant("alice", "default", "viewer")

    store = _store_with({(surrogate, "default"): "vault:v1:enc:x"})
    transit = _stub_transit({})
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                f"/v1/management/surrogate/{surrogate}/real",
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "default",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    # SEC-8: a denied attempt is audited too, so a probing caller leaves a trail.
    assert len(audit_log.records) == 1
    record = audit_log.records[0]
    assert record.event == "re-identify-denied"
    assert record.workspace == "default"
    assert record.identity == "alice"
    assert surrogate in record.reason


# ---------------------------------------------------------------------------
# 3. 404 when surrogate is not in the requested workspace
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reidentify_returns_404_when_surrogate_not_in_requested_workspace():
    surrogate = "Clara Hoffmann"
    # surrogate exists in ws-a but caller asks for ws-b
    rbac = RbacRegistry()
    rbac.grant("bob", "ws-b", "re-identifier")

    store = _store_with({(surrogate, "ws-a"): "vault:v1:enc:x"})
    transit = _stub_transit({"vault:v1:enc:x": "real-value"})
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                f"/v1/management/surrogate/{surrogate}/real",
                headers={
                    "x-blindfold-identity": "bob",
                    "x-blindfold-workspace": "ws-b",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404
    # SEC-8: a failed lookup (unknown surrogate in this workspace) is audited too.
    assert len(audit_log.records) == 1
    record = audit_log.records[0]
    assert record.event == "re-identify-failed"
    assert record.workspace == "ws-b"
    assert record.identity == "bob"
    assert surrogate in record.reason


# ---------------------------------------------------------------------------
# 4. Re-identification is audited (surrogate in reason, never the real value)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reidentify_writes_audit_event_with_surrogate_not_real_value():
    surrogate = "Clara Hoffmann"
    ciphertext = "vault:v1:enc:martin"
    real_value = "Martin Bach"

    rbac = RbacRegistry()
    rbac.grant("alice", "default", "re-identifier")

    store = _store_with({(surrogate, "default"): ciphertext})
    transit = _stub_transit({ciphertext: real_value})
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            await client.get(
                f"/v1/management/surrogate/{surrogate}/real",
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "default",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert len(audit_log.records) == 1
    record = audit_log.records[0]
    assert record.event == "re-identified"
    assert record.workspace == "default"
    assert record.identity == "alice"
    # CONTEXT invariant: real value NEVER in audit record
    assert real_value not in record.reason
    assert surrogate in record.reason


# ---------------------------------------------------------------------------
# 5. Multi-workspace referent: re-identifiable from any of its workspaces
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reidentify_multi_workspace_referent_resolves_from_any_authorized_workspace():
    surrogate = "Clara Hoffmann"
    ciphertext = "vault:v1:enc:martin"
    real_value = "Martin Bach"

    # Same surrogate tagged to both ws-a and ws-b
    store = _store_with(
        {
            (surrogate, "ws-a"): ciphertext,
            (surrogate, "ws-b"): ciphertext,
        }
    )
    transit = _stub_transit({ciphertext: real_value})

    rbac = RbacRegistry()
    rbac.grant("carol", "ws-b", "re-identifier")
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                f"/v1/management/surrogate/{surrogate}/real",
                headers={
                    "x-blindfold-identity": "carol",
                    "x-blindfold-workspace": "ws-b",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["real"] == real_value


# ---------------------------------------------------------------------------
# 6. 503 when Transit client is not configured
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reidentify_returns_503_when_transit_not_configured():
    surrogate = "Clara Hoffmann"
    ciphertext = "vault:v1:enc:martin"

    rbac = RbacRegistry()
    rbac.grant("alice", "default", "re-identifier")

    store = _store_with({(surrogate, "default"): ciphertext})
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: None
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                f"/v1/management/surrogate/{surrogate}/real",
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "default",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    # SEC-8: a failed attempt (Transit unconfigured) is audited too.
    assert len(audit_log.records) == 1
    record = audit_log.records[0]
    assert record.event == "re-identify-failed"
    assert record.workspace == "default"
    assert record.identity == "alice"
    assert surrogate in record.reason


# ---------------------------------------------------------------------------
# 7. A decrypt exception is audited too (SEC-8) and never leaks the ciphertext error
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reidentify_writes_audit_event_when_decrypt_raises():
    surrogate = "Clara Hoffmann"
    ciphertext = "vault:v1:enc:martin"

    rbac = RbacRegistry()
    rbac.grant("alice", "default", "re-identifier")

    store = _store_with({(surrogate, "default"): ciphertext})
    # Empty mapping: the stub Transit responds 400, so transit.decrypt() raises.
    transit = _stub_transit({})
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.get(
                    f"/v1/management/surrogate/{surrogate}/real",
                    headers={
                        "x-blindfold-identity": "alice",
                        "x-blindfold-workspace": "default",
                    },
                )
    finally:
        app.dependency_overrides.clear()

    assert len(audit_log.records) == 1
    record = audit_log.records[0]
    assert record.event == "re-identify-failed"
    assert record.workspace == "default"
    assert record.identity == "alice"
    assert surrogate in record.reason


# ---------------------------------------------------------------------------
# 8. get_transit_client auto-initializes from settings when token is configured
# ---------------------------------------------------------------------------


def test_get_transit_client_returns_transit_client_when_token_configured(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_OPENBAO_ADDR", "http://openbao.test:8200")
    monkeypatch.setenv("BLINDFOLD_OPENBAO_TOKEN", "dev-root-token")
    client = get_transit_client()
    assert isinstance(client, TransitClient)


def test_get_transit_client_returns_none_when_token_not_configured(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_OPENBAO_TOKEN", raising=False)
    client = get_transit_client()
    assert client is None
