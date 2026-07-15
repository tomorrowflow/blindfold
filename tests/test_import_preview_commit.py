"""Bulk seeding, two-phase: preview (read-only, validated) + commit (issue #127).

Closes the gap #116 left open: #116's Settings -> Import dropzone only ever parsed
a CSV/JSON file client-side and posted straight to the existing one-phase
POST /v1/management/workspaces/{slug}/seed (issue #108). This slice adds the real
second phase -- a server-side, read-only POST .../seed/preview that validates each
row against the live entity graph (ADR-0018 blind-index equality for duplicates;
the controlled relation vocabulary + orientation for employer/subsidiary_of edges,
CONTEXT.md) -- and hardens commit itself to skip flagged rows rather than trust the
client to only resubmit valid ones.

Leak-audit clause analysis: A-D/G N/A -- this is a management-API entity-graph
mutation, never the request path. E (surrogate invariants) is covered indirectly:
duplicate rows are skipped rather than re-minted, so a re-imported referent never
gets a second, divergent surrogate (no silent double-mint). F (fail-closed/access
control) is the operative clause for the new preview endpoint, mirroring the
existing commit endpoint's 403-without-admin coverage in test_setup_seed_bundle.py.

Coherent-world surrogate generation (ADR-0005 -- employer edges aligning fake email
domains) is explicitly deferred past v1 and tracked under #25; the live mint path
(_mint.py) mints from flat name pools with no email/domain concept at all, and a
prior prototype implementing it was deleted for never being wired in (finding
ARCH-1/ARCH-6). Building that from scratch is out of this slice's scope -- flagged
here rather than fabricated, per the issue's own stop-and-report instruction.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_entity_graph, get_rbac, get_relationship_store
from blindfold.entity_graph import EntityGraph
from blindfold.rbac import RbacRegistry
from blindfold.relationships import RelationshipStore
from blindfold.store.repository import VendoredSeedRepository, relation_row_problems


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


def _override(
    *,
    entity_graph: EntityGraph,
    rbac: RbacRegistry,
    relationship_store: RelationshipStore | None = None,
) -> None:
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_relationship_store] = lambda: (
        relationship_store if relationship_store is not None else RelationshipStore()
    )


# ---------------------------------------------------------------------------
# 1. relation_row_problems -- pure per-row validation (unit level)
# ---------------------------------------------------------------------------


def test_relation_row_problems_empty_for_a_valid_employer_edge():
    assert relation_row_problems("employer", "person", "term") == []


def test_relation_row_problems_flags_unknown_relation():
    assert relation_row_problems("manages", "person", "term") == ["unknown_relation"]


def test_relation_row_problems_flags_orientation_violation():
    # employer is person -> term; a term -> person row is the wrong orientation.
    assert relation_row_problems("employer", "term", "person") == ["orientation_violation"]


# ---------------------------------------------------------------------------
# 2. VendoredSeedRepository.preview() -- read-only, per-row validated preview
# ---------------------------------------------------------------------------


def test_preview_returns_clean_rows_for_a_valid_bundle_and_persists_nothing():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")

    bundle = {
        "persons": [{"canonical_name": "Priya Sharma", "variations": ["Priya"]}],
        "terms": [{"canonical_name": "Zentek Solutions", "variations": []}],
        "entity_relationships": [
            {
                "source_kind": "person",
                "source": "Priya Sharma",
                "relation": "employer",
                "target_kind": "term",
                "target": "Zentek Solutions",
            }
        ],
    }
    repo = VendoredSeedRepository(bundle)

    preview = repo.preview(graph, workspace="acme")

    assert preview["row_count"] == 3
    assert all(row["problems"] == [] for row in preview["rows"])
    values = {row["value"] for row in preview["rows"]}
    assert "Priya Sharma" in values
    assert "Zentek Solutions" in values
    assert "Priya Sharma → Zentek Solutions" in values
    # Read-only: nothing persisted to the graph by preview.
    assert graph.list_entities("acme") == []


def test_preview_flags_a_blind_index_duplicate_of_an_existing_entity():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")
    graph.add_entity(
        kind="person", workspace="acme", canonical_name="Priya Sharma", surrogate="Devin Novak"
    )

    bundle = {
        "persons": [{"canonical_name": "Priya Sharma", "variations": []}],
        "terms": [],
        "entity_relationships": [],
    }
    preview = VendoredSeedRepository(bundle).preview(graph, workspace="acme")

    assert preview["rows"] == [
        {"kind": "person", "value": "Priya Sharma", "relation": "", "problems": ["duplicate"]}
    ]
    assert graph.list_entities("acme") == [
        e for e in graph.list_entities("acme") if e.canonical_name == "Priya Sharma"
    ]  # unchanged: still exactly the one pre-existing entity, preview added nothing
    assert len(graph.list_entities("acme")) == 1


def test_preview_flags_an_unknown_relation_and_an_orientation_violation_with_reasons():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")

    bundle = {
        "persons": [{"canonical_name": "Priya Sharma", "variations": []}],
        "terms": [{"canonical_name": "Zentek Solutions", "variations": []}],
        "entity_relationships": [
            {
                "source_kind": "person",
                "source": "Priya Sharma",
                "relation": "manages",
                "target_kind": "term",
                "target": "Zentek Solutions",
            },
            {
                "source_kind": "term",
                "source": "Zentek Solutions",
                "relation": "employer",
                "target_kind": "person",
                "target": "Priya Sharma",
            },
        ],
    }
    preview = VendoredSeedRepository(bundle).preview(graph, workspace="acme")

    relationship_rows = [row for row in preview["rows"] if row["relation"]]
    assert relationship_rows[0]["problems"] == ["unknown_relation"]
    assert relationship_rows[1]["problems"] == ["orientation_violation"]


# ---------------------------------------------------------------------------
# 3. POST /v1/management/workspaces/{slug}/seed/preview -- the endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_seed_preview_endpoint_returns_rows_and_persists_nothing():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    _override(entity_graph=graph, rbac=rbac)

    bundle = {
        "persons": [{"canonical_name": "Priya Sharma", "variations": []}],
        "terms": [],
        "entity_relationships": [],
    }
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/seed/preview",
                json={"bundle": bundle},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 1
    assert body["rows"][0]["value"] == "Priya Sharma"
    assert body["rows"][0]["problems"] == []
    assert graph.list_entities("acme") == []


@pytest.mark.anyio
async def test_seed_preview_returns_403_without_admin_role():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")
    rbac = RbacRegistry()
    rbac.grant("bob", "acme", "viewer")
    _override(entity_graph=graph, rbac=rbac)

    bundle = {"persons": [{"canonical_name": "Priya Sharma", "variations": []}], "terms": []}
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/seed/preview",
                json={"bundle": bundle},
                headers={"x-blindfold-identity": "bob"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_seed_preview_returns_422_without_a_bundle():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    _override(entity_graph=graph, rbac=rbac)

    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/seed/preview",
                json={},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 4. Commit hardening -- duplicates are skipped (no double-mint); invalid
#    relationship rows are skipped rather than crashing the whole commit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_skips_a_blind_index_duplicate_referent_no_double_mint():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")
    existing = graph.add_entity(
        kind="person", workspace="acme", canonical_name="Priya Sharma", surrogate="Devin Novak"
    )
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    _override(entity_graph=graph, rbac=rbac)

    bundle = {
        "persons": [{"canonical_name": "Priya Sharma", "variations": []}],
        "terms": [],
        "entity_relationships": [],
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
    assert entities[0].entity_id == existing.entity_id
    assert entities[0].active_surrogate == "Devin Novak"


@pytest.mark.anyio
async def test_commit_skips_invalid_relationship_rows_valid_rows_still_commit():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    relationship_store = RelationshipStore()
    _override(entity_graph=graph, rbac=rbac, relationship_store=relationship_store)

    bundle = {
        "persons": [{"canonical_name": "Priya Sharma", "variations": []}],
        "terms": [
            {"canonical_name": "Zentek Solutions", "variations": []},
            {"canonical_name": "Helvex AG", "variations": []},
        ],
        "entity_relationships": [
            {
                "source_kind": "person",
                "source": "Priya Sharma",
                "relation": "manages",  # unknown relation -- must be skipped
                "target_kind": "term",
                "target": "Zentek Solutions",
            },
            {
                "source_kind": "person",
                "source": "Priya Sharma",
                "relation": "employer",
                "target_kind": "term",
                "target": "Helvex AG",
            },
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
    edges = relationship_store.list_workspace("acme")
    assert len(edges) == 1
    assert edges[0].relation == "employer"
    assert edges[0].target_kind == "term"


@pytest.mark.anyio
async def test_commit_wires_a_relationship_to_a_pre_existing_duplicate_entity():
    graph = EntityGraph()
    graph.create_workspace("acme", "Acme Corp")
    existing = graph.add_entity(
        kind="person", workspace="acme", canonical_name="Priya Sharma", surrogate="Devin Novak"
    )
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    relationship_store = RelationshipStore()
    _override(entity_graph=graph, rbac=rbac, relationship_store=relationship_store)

    bundle = {
        "persons": [{"canonical_name": "Priya Sharma", "variations": []}],
        "terms": [{"canonical_name": "Zentek Solutions", "variations": []}],
        "entity_relationships": [
            {
                "source_kind": "person",
                "source": "Priya Sharma",
                "relation": "employer",
                "target_kind": "term",
                "target": "Zentek Solutions",
            }
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
    assert len(graph.list_entities("acme")) == 2  # Priya (reused) + the new term
    edges = relationship_store.list_workspace("acme")
    assert len(edges) == 1
    assert edges[0].source_id == existing.entity_id
