"""Confirm writes a re-identification entry so Reveal resolves (issue #172).

After #171, confirming a review-inbox candidate seeds ``SurrogateMapping``
(detection) and the workspace ``EntityGraph`` (surrogate-space read surfaces),
but never the ``ReIdentificationStore`` the reveal endpoint
(``GET /v1/management/surrogate/{surrogate}/real``) reads -- so a confirmed
referent could never be re-identified (``re-identify-failed / outcome=not-found``).

Transit is stubbed at the network boundary (httpx.MockTransport), per leak-audit
convention -- never a real OpenBao instance.

Leak-audit clause analysis for this slice:
- A/B/C/D N/A -- confirm is a management-API action, not request-path egress/
  restore; nothing here changes proxy behavior.
- E N/A -- stable/idempotent mint is unaffected; idempotent *re-identify-entry*
  upsert is asserted explicitly below.
- F N/A -- fail-closed (L3Unavailable) untouched.
- G covered -- only Transit ciphertext is ever written to the ReIdentificationStore
  (asserted via the recording Transit stub); with Transit unconfigured, confirm
  still succeeds and writes nothing to the store (no plaintext fallback).
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_entity_graph,
    get_mapping,
    get_reidentify_store,
    get_review_inbox,
    get_transit_client,
)
from blindfold.entity_graph import EntityGraph
from blindfold.reidentify import InMemoryReIdentificationStore, ReIdentificationStore
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.transit import TransitClient


class _RecordingReIdentificationStore:
    """A minimal double implementing only the ``ReIdentificationStore`` Protocol's
    declared methods -- proves the write side (``seed``) is part of the honest
    seam contract, not just an incidental method on the concrete implementations.
    """

    def __init__(self) -> None:
        self.seeded: list[tuple[str, str, str]] = []

    def seed(self, surrogate: str, workspace: str, ciphertext: str) -> None:
        self.seeded.append((surrogate, workspace, ciphertext))

    async def surrogate_to_ciphertext(self, surrogate: str, workspace: str) -> str | None:
        for s, w, c in reversed(self.seeded):
            if s == surrogate and w == workspace:
                return c
        return None


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _recording_transit() -> TransitClient:
    """A TransitClient whose encrypt/decrypt round-trip via a fake in-memory vault."""
    vault: dict[str, str] = {}
    counter = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/encrypt/blindfold-mapping"):
            counter[0] += 1
            ciphertext = f"vault:v1:{counter[0]}"
            vault[ciphertext] = body["plaintext"]
            return httpx.Response(200, json={"data": {"ciphertext": ciphertext}})
        if request.url.path.endswith("/decrypt/blindfold-mapping"):
            plaintext = vault[body["ciphertext"]]
            return httpx.Response(200, json={"data": {"plaintext": plaintext}})
        return httpx.Response(400, json={"errors": ["unhandled"]})

    return TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


@pytest.mark.anyio
async def test_confirm_lets_an_authorized_re_identifier_reveal_the_confirmed_referent():
    from blindfold.app import get_audit_log, get_rbac
    from blindfold.policy import AuditLog
    from blindfold.rbac import RbacRegistry

    inbox = ReviewInbox()
    item = inbox.upsert(
        "Astrid Voss", context="Brief Astrid Voss tomorrow.", workspace="acme"
    )
    entity_graph = EntityGraph()
    mapping = SurrogateMapping.from_pairs([])
    reidentify_store = InMemoryReIdentificationStore()
    transit = _recording_transit()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "re-identifier")
    audit_log = AuditLog()

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            confirm_resp = await client.post(
                f"/v1/management/review-inbox/{item.id}/confirm"
            )
            reveal_resp = await client.get(
                f"/v1/management/surrogate/{item.provisional_surrogate}/real",
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "acme",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert confirm_resp.status_code == 200
    assert reveal_resp.status_code == 200
    assert reveal_resp.json()["real"] == "Astrid Voss"
    assert audit_log.records[-1].event == "re-identified"


@pytest.mark.anyio
async def test_confirm_writes_a_workspace_scoped_entry_not_visible_from_another_workspace():
    from blindfold.app import get_audit_log, get_rbac
    from blindfold.policy import AuditLog
    from blindfold.rbac import RbacRegistry

    inbox = ReviewInbox()
    item = inbox.upsert(
        "Astrid Voss", context="Brief Astrid Voss tomorrow.", workspace="acme"
    )
    entity_graph = EntityGraph()
    mapping = SurrogateMapping.from_pairs([])
    reidentify_store = InMemoryReIdentificationStore()
    transit = _recording_transit()
    rbac = RbacRegistry()
    # bob only holds re-identifier on a DIFFERENT workspace than the item's own.
    rbac.grant("bob", "other-workspace", "re-identifier")
    audit_log = AuditLog()

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            await client.post(f"/v1/management/review-inbox/{item.id}/confirm")
            reveal_resp = await client.get(
                f"/v1/management/surrogate/{item.provisional_surrogate}/real",
                headers={
                    "x-blindfold-identity": "bob",
                    "x-blindfold-workspace": "other-workspace",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert reveal_resp.status_code == 404


@pytest.mark.anyio
async def test_confirm_succeeds_and_writes_nothing_when_transit_is_unconfigured():
    inbox = ReviewInbox()
    item = inbox.upsert(
        "Astrid Voss", context="Brief Astrid Voss tomorrow.", workspace="acme"
    )
    entity_graph = EntityGraph()
    mapping = SurrogateMapping.from_pairs([])
    reidentify_store = InMemoryReIdentificationStore()

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store
    app.dependency_overrides[get_transit_client] = lambda: None
    try:
        async with _make_client() as client:
            confirm_resp = await client.post(
                f"/v1/management/review-inbox/{item.id}/confirm"
            )
    finally:
        app.dependency_overrides.clear()

    assert confirm_resp.status_code == 200
    # Entity/mapping side effects still land (issue #171 behavior preserved)...
    assert entity_graph.list_entities("acme")[0].canonical_name == "Astrid Voss"
    # ...but no re-identify entry is written when Transit is unavailable -- no
    # plaintext fallback (only ciphertext may ever reach the store).
    ciphertext = await reidentify_store.surrogate_to_ciphertext(
        item.provisional_surrogate, "acme"
    )
    assert ciphertext is None


@pytest.mark.anyio
async def test_reconfirming_the_same_real_value_does_not_error_and_reveal_still_resolves():
    from blindfold.app import get_audit_log, get_rbac
    from blindfold.policy import AuditLog
    from blindfold.rbac import RbacRegistry

    entity_graph = EntityGraph()
    mapping = SurrogateMapping.from_pairs([])
    reidentify_store = InMemoryReIdentificationStore()
    transit = _recording_transit()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "re-identifier")
    audit_log = AuditLog()

    # First confirm.
    inbox = ReviewInbox()
    item = inbox.upsert(
        "Astrid Voss", context="Brief Astrid Voss tomorrow.", workspace="acme"
    )
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            first_resp = await client.post(
                f"/v1/management/review-inbox/{item.id}/confirm"
            )

            # Re-confirm the same real value via a fresh inbox item (same provisional
            # surrogate, since ReviewInbox.upsert reuses it by `real`).
            inbox2 = ReviewInbox()
            item2 = inbox2.upsert(
                "Astrid Voss", context="Brief Astrid Voss again.", workspace="acme"
            )
            assert item2.provisional_surrogate == item.provisional_surrogate
            app.dependency_overrides[get_review_inbox] = lambda: inbox2
            second_resp = await client.post(
                f"/v1/management/review-inbox/{item2.id}/confirm"
            )

            reveal_resp = await client.get(
                f"/v1/management/surrogate/{item.provisional_surrogate}/real",
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "acme",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert first_resp.status_code == 200
    assert second_resp.status_code == 200
    assert reveal_resp.status_code == 200
    assert reveal_resp.json()["real"] == "Astrid Voss"


def test_recording_double_satisfies_the_reidentification_store_protocol():
    # ReIdentificationStore is runtime_checkable (isinstance-testable) -- a double
    # implementing only the Protocol's declared methods (surrogate_to_ciphertext
    # + seed, issue #172) satisfies it, proving the write side is a first-class
    # part of the seam contract rather than an implementation-only extra.
    assert isinstance(_RecordingReIdentificationStore(), ReIdentificationStore)


@pytest.mark.anyio
async def test_confirm_writes_only_ciphertext_to_a_recording_double_store():
    inbox = ReviewInbox()
    item = inbox.upsert(
        "Astrid Voss", context="Brief Astrid Voss tomorrow.", workspace="acme"
    )
    entity_graph = EntityGraph()
    mapping = SurrogateMapping.from_pairs([])
    reidentify_store = _RecordingReIdentificationStore()
    transit = _recording_transit()

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store
    app.dependency_overrides[get_transit_client] = lambda: transit
    try:
        async with _make_client() as client:
            resp = await client.post(
                f"/v1/management/review-inbox/{item.id}/confirm"
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(reidentify_store.seeded) == 1
    surrogate, workspace, ciphertext = reidentify_store.seeded[0]
    assert surrogate == item.provisional_surrogate
    assert workspace == "acme"
    # Only Transit ciphertext ever reaches the store -- never the real value.
    assert ciphertext != "Astrid Voss"
    assert transit.decrypt(ciphertext) == "Astrid Voss"
