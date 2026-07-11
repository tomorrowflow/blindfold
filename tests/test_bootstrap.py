"""Bootstrap: admin/re-identifier from BLINDFOLD_BOOTSTRAP_ADMIN, entity graph and
re-identify store from the vendored seed (issue #43 / UX-1).

Per the grill decision this is bootstrap-admin, not an RBAC-bypass mode: ``grant``
is the exact same seam ``POST /v1/management/workspaces/{slug}/roles`` uses; a
bootstrapped identity is granted through it, and ``_require_role`` is exercised
unchanged for both the bootstrapped identity (succeeds) and any other identity
(still 403s).

Leak-audit clause analysis: A/B/C/D/E -- N/A, no proxy request path. F (access
control) -- covered directly: bootstrap grants a role through the same RbacRegistry
a human admin's grant-role endpoint writes to; no bypass branch is introduced, and
an identity NOT bootstrapped is still refused.
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import app, get_audit_log, get_rbac
from blindfold.bootstrap import bootstrap_admin, bootstrap_from_vendored_seed
from blindfold.entity_graph import EntityGraph
from blindfold.policy import AuditLog
from blindfold.rbac import RbacRegistry
from blindfold.reidentify import InMemoryReIdentificationStore
from blindfold.relationships import RelationshipStore
from blindfold.store import vendored_seed_repository
from blindfold.transit import TransitClient


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


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


def test_bootstrap_admin_grants_the_canonical_four_role_set():
    # ADR-0028: bootstrap_admin grants every canonical role, curator included.
    rbac = RbacRegistry()

    bootstrap_admin(rbac, "operator", "default")

    assert rbac.has_role("operator", "default", "viewer")
    assert rbac.has_role("operator", "default", "curator")
    assert rbac.has_role("operator", "default", "re-identifier")
    assert rbac.has_role("operator", "default", "admin")


@pytest.mark.anyio
async def test_bootstrapped_admin_can_list_workspace_roles_others_still_403():
    rbac = RbacRegistry()
    bootstrap_admin(rbac, "operator", "default")
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            authorized = await client.get(
                "/v1/management/workspaces/default/roles",
                headers={"x-blindfold-identity": "operator"},
            )
            unauthorized = await client.get(
                "/v1/management/workspaces/default/roles",
                headers={"x-blindfold-identity": "someone-else"},
            )
    finally:
        app.dependency_overrides.clear()

    assert authorized.status_code == 200
    assert unauthorized.status_code == 403


def test_bootstrap_from_vendored_seed_seeds_entity_graph_without_requiring_transit():
    graph = EntityGraph()
    relationship_store = RelationshipStore()
    reidentify_store = InMemoryReIdentificationStore()
    rbac = RbacRegistry()

    bootstrap_from_vendored_seed(
        entity_graph=graph,
        relationship_store=relationship_store,
        reidentify_store=reidentify_store,
        rbac=rbac,
        transit=None,
        bootstrap_admin_identity="",
    )

    assert graph.get_by_canonical("default", "person", "Martin Bach") is not None
    assert relationship_store.list_workspace("default")
    # No Transit configured -> the re-identify store stays empty (Reveal is
    # unavailable regardless, so this is not a degradation).
    assert not rbac.list_workspace("default")


def test_bootstrap_from_vendored_seed_grants_no_roles_when_identity_is_empty():
    rbac = RbacRegistry()

    bootstrap_from_vendored_seed(
        entity_graph=EntityGraph(),
        relationship_store=RelationshipStore(),
        reidentify_store=InMemoryReIdentificationStore(),
        rbac=rbac,
        transit=None,
        bootstrap_admin_identity="",
    )

    # No BLINDFOLD_BOOTSTRAP_ADMIN set -> no roles granted for anyone (no bypass).
    assert rbac.list_workspace("default") == []


@pytest.mark.anyio
async def test_bootstrap_from_vendored_seed_seeds_reidentify_store_when_transit_configured():
    reidentify_store = InMemoryReIdentificationStore()
    transit = _recording_transit()

    bootstrap_from_vendored_seed(
        entity_graph=EntityGraph(),
        relationship_store=RelationshipStore(),
        reidentify_store=reidentify_store,
        rbac=RbacRegistry(),
        transit=transit,
        bootstrap_admin_identity="",
    )

    pairs = dict(vendored_seed_repository().seeded_pairs())
    surrogate = pairs["Martin Bach"]
    ciphertext = await reidentify_store.surrogate_to_ciphertext(surrogate, "default")
    assert ciphertext is not None
    assert transit.decrypt(ciphertext) == "Martin Bach"
