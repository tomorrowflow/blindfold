"""Re-identification endpoint: GET /v1/management/surrogate/{surrogate}/real (ADR-0015 / issue #10).

Drives the management endpoint through the FastAPI test client. The endpoint decrypts
through whichever **mapping cipher** is active (ADR-0045 §4, issue #430) -- Transit
(stubbed via an httpx.MockTransport at the network boundary) or the Local key cipher
(a real ``LocalKeyCipher`` over a throwaway Store key, never a stub -- it has no network
boundary to stub). The re-identification store is stubbed via a pre-seeded in-memory
mapping in both cases.

Leak-audit clause analysis:
- A/B/C/D/E — N/A: this slice does not add a new proxy request path; the existing
  proxy path is unchanged.
- F (access control) — covered: endpoint returns 403 when the calling identity lacks
  the ``re-identifier`` role on the requested workspace; workspace-scoped (surrogate
  from workspace A is NOT re-identifiable by a caller holding re-identifier only on B).
  503 when no mapping cipher is configured (env vars absent) — covered by the
  no-mapping-cipher test.
- G (mapping secrecy) — covered by design: the endpoint decrypts via the active mapping
  cipher (Transit stubbed at the network boundary, Local key cipher exercised for real);
  the real value is never stored in the audit record (only the surrogate is recorded),
  honoring the CONTEXT invariant.

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

from blindfold.app import (
    app,
    get_audit_log,
    get_mapping_cipher,
    get_rbac,
    get_reidentify_store,
    get_transit_client,
)
from blindfold.policy import AuditLog
from blindfold.rbac import RbacRegistry
from blindfold.transit import TransitClient, TransitError


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
    app.dependency_overrides[get_mapping_cipher] = lambda: transit
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
    app.dependency_overrides[get_mapping_cipher] = lambda: transit
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
    app.dependency_overrides[get_mapping_cipher] = lambda: transit
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
async def test_reidentify_returns_503_when_no_mapping_cipher_is_configured():
    """Neither Transit nor the Local key cipher is configured (issue #430): fails
    closed with a named, cipher-agnostic reason -- the message must not tell a
    Local-key-cipher user to configure OpenBao specifically, since that is not
    the only (or, for the menu bar default, even the relevant) remedy.
    """
    surrogate = "Clara Hoffmann"
    ciphertext = "vault:v1:enc:martin"

    rbac = RbacRegistry()
    rbac.grant("alice", "default", "re-identifier")

    store = _store_with({(surrogate, "default"): ciphertext})
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: None
    app.dependency_overrides[get_mapping_cipher] = lambda: None
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
    # The message names both remedies -- Local key cipher first -- rather than
    # steering every reader toward OpenBao regardless of which cipher they run.
    detail = resp.json()["detail"]
    assert "BLINDFOLD_STORE_KEY" in detail
    assert "Transit client not configured" not in detail

    # SEC-8: a failed attempt (no mapping cipher configured) is audited too.
    assert len(audit_log.records) == 1
    record = audit_log.records[0]
    assert record.event == "re-identify-failed"
    assert record.workspace == "default"
    assert record.identity == "alice"
    assert surrogate in record.reason
    assert "outcome=mapping-cipher-unconfigured" in record.reason


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
    # Empty mapping: the stub Transit responds 400, so transit.decrypt() raises the
    # named TransitError (issue #364 -- previously the unnamed httpx.HTTPStatusError
    # raise_for_status() produced).
    transit = _stub_transit({})
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_mapping_cipher] = lambda: transit
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            with pytest.raises(TransitError):
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
# 8. Bulk resolution via `also` -- the exchange-level Reveal switch's seam
# (ADR-0059 §5, issue #401). Same route, same handler, same audit-event
# vocabulary: `also` just lets one call resolve several surrogates so the
# switch can write exactly one audit event no matter how many surrogates the
# exchange carries, rather than one event per surrogate.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reidentify_bulk_resolves_every_surrogate_in_one_call():
    primary = "Clara Hoffmann"
    other = "Northwind Logistics"
    ciphertexts = {primary: "vault:v1:enc:martin-bach", other: "vault:v1:enc:acme-corp"}
    plaintexts = {"vault:v1:enc:martin-bach": "Martin Bach", "vault:v1:enc:acme-corp": "Acme Corp"}

    rbac = RbacRegistry()
    rbac.grant("alice", "default", "re-identifier")

    store = _store_with(
        {
            (primary, "default"): ciphertexts[primary],
            (other, "default"): ciphertexts[other],
        }
    )
    transit = _stub_transit(plaintexts)
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_mapping_cipher] = lambda: transit
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                f"/v1/management/surrogate/{primary}/real",
                params={"also": [other]},
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "default",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == {primary: "Martin Bach", other: "Acme Corp"}

    # Exactly one audit event for the whole batch (ADR-0059 §5) -- not one per
    # surrogate, which is what the per-chip single-surrogate call already does.
    assert len(audit_log.records) == 1
    record = audit_log.records[0]
    assert record.event == "re-identified"
    assert primary in record.reason
    assert other in record.reason
    # CONTEXT invariant: real values never in the audit record.
    assert "Martin Bach" not in record.reason
    assert "Acme Corp" not in record.reason


@pytest.mark.anyio
async def test_reidentify_bulk_denied_without_role_writes_exactly_one_denied_event():
    primary = "Clara Hoffmann"
    other = "Northwind Logistics"

    rbac = RbacRegistry()
    rbac.grant("alice", "default", "viewer")  # not re-identifier

    store = _store_with({(primary, "default"): "x", (other, "default"): "y"})
    transit = _stub_transit({})
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                f"/v1/management/surrogate/{primary}/real",
                params={"also": [other]},
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "default",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    assert len(audit_log.records) == 1
    assert audit_log.records[0].event == "re-identify-denied"


@pytest.mark.anyio
async def test_reidentify_bulk_fails_closed_when_one_surrogate_is_unresolvable():
    primary = "Clara Hoffmann"
    unknown = "Ghost Referent"

    rbac = RbacRegistry()
    rbac.grant("alice", "default", "re-identifier")

    store = _store_with({(primary, "default"): "vault:v1:enc:martin-bach"})
    transit = _stub_transit({"vault:v1:enc:martin-bach": "Martin Bach"})
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                f"/v1/management/surrogate/{primary}/real",
                params={"also": [unknown]},
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "default",
                },
            )
    finally:
        app.dependency_overrides.clear()

    # Fail-closed on the whole batch -- a partial reveal would show some real
    # values without the reader knowing the batch was incomplete.
    assert resp.status_code == 404
    assert len(audit_log.records) == 1
    assert audit_log.records[0].event == "re-identify-failed"


# ---------------------------------------------------------------------------
# 9. get_transit_client auto-initializes from settings when token is configured
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


# ---------------------------------------------------------------------------
# 10. Re-identify decrypts through whichever mapping cipher is active, not
# Transit specifically (ADR-0045 §4, issue #430) -- the Local key cipher is
# the menu bar app's default (Store key set, no OpenBao token).
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reidentify_resolves_under_the_local_key_cipher_with_no_transit():
    import base64
    import os

    from blindfold.mapping_cipher import LocalKeyCipher

    cipher = LocalKeyCipher(base64.b64encode(os.urandom(32)).decode())
    surrogate = "Clara Hoffmann"
    real_value = "Martin Bach"
    ciphertext = cipher.encrypt(real_value)

    rbac = RbacRegistry()
    rbac.grant("alice", "default", "re-identifier")

    store = _store_with({(surrogate, "default"): ciphertext})
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: None
    app.dependency_overrides[get_mapping_cipher] = lambda: cipher
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
    assert data["real"] == real_value
    assert data["surrogate"] == surrogate
    assert audit_log.records[-1].event == "re-identified"


@pytest.mark.anyio
async def test_reidentify_bulk_resolves_under_the_local_key_cipher_in_one_call():
    """The exchange-level bulk Reveal switch (``also=``) resolves under the Local
    key cipher too -- exactly one audit event, same as today under Transit
    (issue #430 AC2).
    """
    import base64
    import os

    from blindfold.mapping_cipher import LocalKeyCipher

    cipher = LocalKeyCipher(base64.b64encode(os.urandom(32)).decode())
    primary = "Clara Hoffmann"
    other = "Northwind Logistics"
    plaintexts = {primary: "Martin Bach", other: "Acme Corp"}
    ciphertexts = {name: cipher.encrypt(value) for name, value in plaintexts.items()}

    rbac = RbacRegistry()
    rbac.grant("alice", "default", "re-identifier")

    store = _store_with(
        {
            (primary, "default"): ciphertexts[primary],
            (other, "default"): ciphertexts[other],
        }
    )
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
    app.dependency_overrides[get_transit_client] = lambda: None
    app.dependency_overrides[get_mapping_cipher] = lambda: cipher
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                f"/v1/management/surrogate/{primary}/real",
                params={"also": [other]},
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "default",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["results"] == plaintexts

    # Exactly one audit event for the whole batch, same as under Transit.
    assert len(audit_log.records) == 1
    assert audit_log.records[0].event == "re-identified"


@pytest.mark.anyio
async def test_reidentify_resolves_under_the_local_key_cipher_through_production_wiring(
    monkeypatch,
):
    """The production seam, not a stub: BLINDFOLD_STORE_KEY is set and neither
    the cipher nor the Transit dependency is overridden, so the real
    ``get_mapping_cipher()`` resolves the Local key cipher exactly as a menu
    bar install would (issue #430 AC4) -- this is the class of bug that hid
    behind every other test's ``get_mapping_cipher``/``get_transit_client``
    override.
    """
    import base64
    import os

    from blindfold.mapping_cipher import LocalKeyCipher

    store_key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("BLINDFOLD_STORE_KEY", store_key)
    monkeypatch.delenv("BLINDFOLD_OPENBAO_TOKEN", raising=False)

    surrogate = "Clara Hoffmann"
    real_value = "Martin Bach"
    # Pre-seed the store with ciphertext produced by an independently-constructed
    # cipher over the SAME Store key -- proving decrypt works via the key alone,
    # not a shared cipher instance.
    ciphertext = LocalKeyCipher(store_key).encrypt(real_value)

    rbac = RbacRegistry()
    rbac.grant("alice", "default", "re-identifier")

    store = _store_with({(surrogate, "default"): ciphertext})
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_reidentify_store] = lambda: store
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
    assert resp.json()["real"] == real_value
    assert audit_log.records[-1].event == "re-identified"
