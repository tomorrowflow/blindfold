"""Org-graph endpoint + SPA (Management-API seam / issue #29).

The graph renders in surrogate-space: nodes are labelled with their active
surrogates, not real entity names. Loading the graph emits no audit events
(no decrypt, no re-identify). Per-node reveal uses the existing
re-identify endpoint (already tested in test_reidentify_endpoint.py).

Leak-audit clause analysis:
- A/B/C/D/E — N/A: the graph endpoint and SPA page do not touch the proxy
  request path. No blindfold, no restore, no provider egress.
- F (access control) — covered: per-node reveal (the re-identify endpoint)
  requires the ``re-identifier`` role (ADR-0015). Loading the graph itself
  does not require any role; all data returned is surrogate-space.
- G (mapping secrecy) — covered by design: the graph endpoint reads only
  ``active_surrogate`` from EntityRecord (the surrogate, not the real name).
  No Transit decrypt is performed. Real names never reach the HTTP response.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_entity_graph,
    get_relationship_store,
)
from blindfold.entity_graph import EntityGraph
from blindfold.policy import AuditLog
from blindfold.relationships import RelationshipStore
from blindfold.spa import (
    ORG_GRAPH_ENDPOINT,
    REIDENTIFY_ENDPOINT,
)


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


# ---------------------------------------------------------------------------
# 1. Graph endpoint returns nodes in surrogate-space
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_endpoint_returns_nodes_labelled_with_surrogates():
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")
    graph.add_entity("term", "acme", "Project Condor", surrogate="Project Wren")

    store = RelationshipStore()
    audit_log = AuditLog()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/acme/graph")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    nodes = data["nodes"]
    labels = {n["label"] for n in nodes}
    # Nodes are labelled with surrogates, not real names.
    assert "Clara Hoffmann" in labels
    assert "Project Wren" in labels
    # Real names must NOT appear.
    assert "Martin Bach" not in labels
    assert "Project Condor" not in labels


# ---------------------------------------------------------------------------
# 2. Graph endpoint includes relationship edges
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_endpoint_includes_relationship_edges():
    graph = EntityGraph()
    person = graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")
    org = graph.add_entity("term", "acme", "Initech GmbH", surrogate="Pinnacle Corp")

    store = RelationshipStore()
    store.create("acme", "person", person.entity_id, "employer", "term", org.entity_id)

    audit_log = AuditLog()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/acme/graph")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    edges = data["edges"]
    assert len(edges) == 1
    edge = edges[0]
    assert edge["relation"] == "employer"
    assert edge["source"] == person.entity_id
    assert edge["target"] == org.entity_id


# ---------------------------------------------------------------------------
# 3. Loading the graph emits no audit events (no decrypt, no re-identify)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_endpoint_emits_no_audit_events():
    graph = EntityGraph()
    graph.add_entity("person", "ws-x", "Real Person", surrogate="Fake Person")

    store = RelationshipStore()
    audit_log = AuditLog()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            await client.get("/v1/management/workspaces/ws-x/graph")
    finally:
        app.dependency_overrides.clear()

    assert audit_log.records == [], "graph view must not emit any audit events"


# ---------------------------------------------------------------------------
# 4. Graph endpoint returns only nodes and edges for the requested workspace
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_endpoint_is_workspace_scoped():
    graph = EntityGraph()
    graph.add_entity("person", "ws-a", "Alice Real", surrogate="Alice Sur")
    graph.add_entity("person", "ws-b", "Bob Real", surrogate="Bob Sur")

    store = RelationshipStore()
    audit_log = AuditLog()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/ws-a/graph")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    nodes = resp.json()["nodes"]
    labels = {n["label"] for n in nodes}
    assert "Alice Sur" in labels
    assert "Bob Sur" not in labels


# ---------------------------------------------------------------------------
# 5. Org-graph SPA is served as HTML with a mount point
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_org_graph_spa_is_served_as_html_with_a_mount_point():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    body = resp.text
    assert "<!doctype html>" in body.lower()
    assert 'id="org-graph-app"' in body


# ---------------------------------------------------------------------------
# 6. Org-graph SPA references the graph endpoint and re-identify endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_org_graph_spa_references_management_endpoints():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    body = resp.text
    # SPA must reference the graph endpoint (workspace slug substituted in JS).
    assert ORG_GRAPH_ENDPOINT in body
    # SPA must reference the re-identify endpoint (for per-node reveal).
    assert REIDENTIFY_ENDPOINT in body
    # Project ubiquitous language — not "anonymize"/"mask"/"redact".
    assert "surrogate" in body.lower()
    for forbidden in ("anonymize", "anonymise", "mask", "redact", "de-anonymize"):
        assert forbidden not in body.lower(), f"{forbidden!r} is not project language"


# ---------------------------------------------------------------------------
# 7. Graph endpoint node shape includes id, kind, and label (surrogate)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_endpoint_node_has_required_fields():
    graph = EntityGraph()
    entity = graph.add_entity("person", "ws-z", "Hans Müller", surrogate="Peter Pan")

    store = RelationshipStore()
    audit_log = AuditLog()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/ws-z/graph")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    nodes = resp.json()["nodes"]
    assert len(nodes) == 1
    node = nodes[0]
    assert node["id"] == entity.entity_id
    assert node["kind"] == "person"
    assert node["label"] == "Peter Pan"
