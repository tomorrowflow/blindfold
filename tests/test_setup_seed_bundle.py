"""POST /v1/management/workspaces/{slug}/seed — Seed bundle import + one-click Sample
data (issue #108, Setup slice 5/5).

Per ADR-0029 (cited by the issue body; the file does not exist in this repo yet --
flagged as a gap, same class of gap prior Setup slices flagged for ADR-0030): a Seed
bundle is dictionary-only (persons, terms, org units, variations, relationships;
role_assignments are org-role strings like "CEO", not RBAC roles). It carries no
mapping, no surrogates, and no RBAC grants -- on import the local install mints its
OWN surrogates. The vendored Sample data and an operator-supplied bundle are the same
mechanism (the shared ``VendoredSeedRepository.seed_entity_graph`` path), two sources:
an empty request body falls back to the vendored bundle (Sample data / one-click);
a ``{"bundle": {...}}`` body is an operator-supplied company bundle (Import).

Gated by the ``admin`` role on the workspace (same convention as
``merge_entities``/workspace-roles endpoints) -- by the time Setup calls this, the
creator already holds ``admin`` on the just-created workspace (issue #107).

Leak-audit clause analysis: A-E/G N/A -- this is a management-API entity-graph
mutation, never the request path. F (fail-closed/access control) is the operative
clause: covered by the 403-without-admin test and by the privilege-escalation guard
tests below (an imported bundle can never grant a Role or supply its own surrogate).
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_entity_graph,
    get_rbac,
    get_reidentify_store,
    get_relationship_store,
    get_transit_client,
)
from blindfold.entity_graph import EntityGraph
from blindfold.rbac import RbacRegistry
from blindfold.reidentify import InMemoryReIdentificationStore
from blindfold.relationships import RelationshipStore
from blindfold.store import vendored_seed_repository
from blindfold.transit import TransitClient


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


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


def _override(
    *,
    entity_graph: EntityGraph,
    rbac: RbacRegistry,
    relationship_store: RelationshipStore | None = None,
    reidentify_store: InMemoryReIdentificationStore | None = None,
    transit: TransitClient | None = None,
) -> None:
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_relationship_store] = lambda: (
        relationship_store if relationship_store is not None else RelationshipStore()
    )
    app.dependency_overrides[get_reidentify_store] = lambda: (
        reidentify_store if reidentify_store is not None else InMemoryReIdentificationStore()
    )
    app.dependency_overrides[get_transit_client] = lambda: transit


# ---------------------------------------------------------------------------
# 1. One-click Sample data: empty body falls back to the vendored bundle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_seed_with_no_bundle_loads_the_vendored_sample_data():
    graph = EntityGraph()
    graph.create_workspace("default", "Default Workspace")
    rbac = RbacRegistry()
    rbac.grant("alice", "default", "admin")
    _override(entity_graph=graph, rbac=rbac)
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/default/seed",
                json={},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    names = {e.canonical_name for e in graph.list_entities("default")}
    assert "Martin Bach" in names


# ---------------------------------------------------------------------------
# 2. Gated by the admin role
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_seed_returns_403_without_admin_role():
    graph = EntityGraph()
    graph.create_workspace("default", "Default Workspace")
    rbac = RbacRegistry()
    rbac.grant("bob", "default", "viewer")
    _override(entity_graph=graph, rbac=rbac)
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/default/seed",
                json={},
                headers={"x-blindfold-identity": "bob"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    assert graph.list_entities("default") == []


# ---------------------------------------------------------------------------
# 3. Import: an operator-supplied bundle populates the workspace and mints its
#    OWN surrogate locally, ignoring any surrogate the bundle itself carries
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_import_populates_the_workspace_and_mints_its_own_surrogate():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    _override(entity_graph=graph, rbac=rbac)

    bundle = {
        "workspace": {"slug": "acme", "name": "Acme Corp"},
        "persons": [
            {
                "canonical_name": "Jane Doe",
                "variations": ["Jane"],
                # An attacker-/vendor-supplied surrogate: must never be used --
                # this install mints its own (ADR-0029 dictionary-only contract).
                "surrogate": "attacker-picked-surrogate",
            }
        ],
        "terms": [],
    }

    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/seed",
                json={"bundle": bundle},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    entities = graph.list_entities("acme")
    assert len(entities) == 1
    jane = entities[0]
    assert jane.canonical_name == "Jane Doe"
    assert jane.variations == ["Jane"]
    assert jane.active_surrogate != "attacker-picked-surrogate"
    assert jane.active_surrogate != ""


# ---------------------------------------------------------------------------
# 4. Privilege-escalation guard: a bundle can carry no RBAC grants -- an
#    RBAC-shaped field in the bundle is ignored, never applied
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_import_never_grants_a_role_even_if_the_bundle_carries_rbac_shaped_fields():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    _override(entity_graph=graph, rbac=rbac)

    bundle = {
        "workspace": {"slug": "acme", "name": "Acme Corp"},
        "persons": [{"canonical_name": "Jane Doe", "variations": []}],
        "terms": [],
        # Neither key is part of the ADR-0029 dictionary-only contract; a bundle
        # must never be able to mint itself an RBAC grant through either.
        "rbac_grants": [{"identity": "mallory", "workspace": "acme", "role": "admin"}],
        "role_assignments": [
            {"person": "Jane Doe", "org_unit": "Management", "role": "admin"}
        ],
    }

    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/seed",
                json={"bundle": bundle},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    # "role": "admin" in role_assignments is org-graph structure (a role-assignment
    # label like "CEO"), never an RBAC grant -- rbac must be untouched.
    assert rbac.list_identity("mallory") == []
    assert not rbac.has_role("mallory", "acme", "admin")
    # alice's own pre-existing admin grant is the only assignment left standing.
    assert [a.role for a in rbac.list_identity("alice")] == ["admin"]


# ---------------------------------------------------------------------------
# 5. After import, re-identify resolves (relies on #105's persisted stores) --
#    the seed endpoint also seeds the re-identify store when Transit is configured
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_import_seeds_the_reidentify_store_so_reidentify_resolves_afterward():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    rbac.grant("alice", "acme", "re-identifier")
    reidentify_store = InMemoryReIdentificationStore()
    transit = _recording_transit()
    _override(
        entity_graph=graph,
        rbac=rbac,
        reidentify_store=reidentify_store,
        transit=transit,
    )

    bundle = {
        "workspace": {"slug": "acme", "name": "Acme Corp"},
        "persons": [{"canonical_name": "Jane Doe", "variations": []}],
        "terms": [],
    }

    try:
        async with _make_client() as client:
            seed_resp = await client.post(
                "/v1/management/workspaces/acme/seed",
                json={"bundle": bundle},
                headers={"x-blindfold-identity": "alice"},
            )
            assert seed_resp.status_code == 200

            entities = graph.list_entities("acme")
            surrogate = entities[0].active_surrogate

            reidentify_resp = await client.get(
                f"/v1/management/surrogate/{surrogate}/real",
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "acme",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert reidentify_resp.status_code == 200
    assert reidentify_resp.json()["real"] == "Jane Doe"


# ---------------------------------------------------------------------------
# 6. Without Transit configured, seeding still succeeds (entity-graph population
#    is not itself a network operation) -- it just skips reidentify-store seeding,
#    mirroring bootstrap_from_vendored_seed's transit-gated behavior
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_seed_succeeds_without_transit_configured_but_skips_reidentify_seeding():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    reidentify_store = InMemoryReIdentificationStore()
    _override(
        entity_graph=graph,
        rbac=rbac,
        reidentify_store=reidentify_store,
        transit=None,
    )

    bundle = {
        "workspace": {"slug": "acme", "name": "Acme Corp"},
        "persons": [{"canonical_name": "Jane Doe", "variations": []}],
        "terms": [],
    }

    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/seed",
                json={"bundle": bundle},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    entities = graph.list_entities("acme")
    assert len(entities) == 1
    surrogate = entities[0].active_surrogate
    ciphertext = await reidentify_store.surrogate_to_ciphertext(surrogate, "acme")
    assert ciphertext is None
