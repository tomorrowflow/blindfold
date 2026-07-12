"""App-wiring: the real (un-overridden) entity-graph + RBAC singletons behave correctly.

Issue #104 (Setup slice 1/5): entity-graph seeding is no longer automatic at startup.
The entity graph is now served by the Postgres-backed store (``PostgresEntityGraphStore``
via ``get_entity_graph()``).  A fresh database starts empty; the vendored seed is opt-in
Sample data for a future slice (#108).

The RBAC-bootstrap-admin and re-identify-store-seeding cases are env/network-gated at
import (``BLINDFOLD_BOOTSTRAP_ADMIN`` / a configured Transit client), neither of which
is set in the test process. Those two are simulated here by running the exact same
seam functions app.py's startup wiring calls -- ``bootstrap_admin`` and
``VendoredSeedRepository.seed_reidentify_store`` -- directly against the app's real
singletons, so the assertions exercise the identical code path startup would run.

Leak-audit clause analysis: A/B/C/D/E -- N/A, no proxy request path (Management-API
reads/writes in surrogate-space, ADR-0017). F (access control) -- covered: the
bootstrapped identity succeeds through the unchanged ``_require_role`` gate and any
other identity is still refused. G (mapping secrecy) -- covered: Reveal resolves only
via Transit-produced ciphertext in the re-identify store, never a plaintext shortcut.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from blindfold.app import app, get_entity_graph, get_rbac, get_reidentify_store, get_transit_client
from blindfold.bootstrap import bootstrap_admin
from blindfold.policy import DEFAULT_WORKSPACE
from blindfold.rbac import RbacRegistry
from blindfold.store import vendored_seed_repository
from blindfold.transit import TransitClient


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


@pytest.mark.anyio
@pytest.mark.skipif(not _docker_available(), reason="Docker unavailable")
async def test_fresh_database_entity_list_renders_empty():
    """Issue #104: a fresh database (no vendored-seed auto-load) returns an empty
    entity list.  The real get_entity_graph() connects to the Postgres-backed store;
    a blank workspace renders [] with no automatic seeding.

    Requires Docker (PostgresContainer) — same guard as test_entity_graph_postgres.py.
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        dsn = pg.get_connection_url()

    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    fresh_store = PostgresEntityGraphStore(dsn)
    app.dependency_overrides[get_entity_graph] = lambda: fresh_store
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/default/entities")
    finally:
        app.dependency_overrides.pop(get_entity_graph, None)

    assert resp.status_code == 200
    # Fresh database: no auto-seeding, so the entity list is empty.
    assert resp.json()["entities"] == []


@pytest.mark.anyio
@pytest.mark.skipif(not _docker_available(), reason="Docker unavailable")
async def test_fresh_database_org_graph_renders_empty():
    """Issue #104: a fresh database renders empty nodes and edges (no auto-seed).

    Requires Docker (PostgresContainer).
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        dsn = pg.get_connection_url()

    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    fresh_store = PostgresEntityGraphStore(dsn)
    app.dependency_overrides[get_entity_graph] = lambda: fresh_store
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/default/graph")
    finally:
        app.dependency_overrides.pop(get_entity_graph, None)

    assert resp.status_code == 200
    body = resp.json()
    # Fresh database: no nodes, no edges.
    assert body["nodes"] == []
    assert body["edges"] == []


@pytest.mark.anyio
async def test_bootstrap_admin_on_the_real_rbac_singleton_authorizes_the_identity_only():
    """Simulates BLINDFOLD_BOOTSTRAP_ADMIN having been set at process start: running
    bootstrap_admin against the app's real (un-overridden) RBAC singleton must let that
    identity through an RBAC-gated management endpoint, and still 403 everyone else --
    the same _require_role gate, no bypass branch.
    """
    bootstrap_admin(get_rbac(), "operator", DEFAULT_WORKSPACE)
    try:
        async with _make_client() as client:
            authorized = await client.get(
                f"/v1/management/workspaces/{DEFAULT_WORKSPACE}/roles",
                headers={"x-blindfold-identity": "operator"},
            )
            unauthorized = await client.get(
                f"/v1/management/workspaces/{DEFAULT_WORKSPACE}/roles",
                headers={"x-blindfold-identity": "someone-else"},
            )
    finally:
        for role in ("viewer", "re-identifier", "admin"):
            get_rbac().revoke("operator", DEFAULT_WORKSPACE, role)

    assert authorized.status_code == 200
    assert unauthorized.status_code == 403


class _EchoTransit(TransitClient):
    """encrypt() wraps plaintext in a fake ciphertext tag; the stub network handler
    reverses it on decrypt -- a network-boundary stub, not a plaintext shortcut."""

    def encrypt(self, plaintext: str) -> str:
        return f"vault:v1:{plaintext}"


def _stub_transit() -> TransitClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        plaintext = body["ciphertext"].removeprefix("vault:v1:")
        encoded = base64.b64encode(plaintext.encode()).decode()
        return httpx.Response(200, json={"data": {"plaintext": encoded}})

    return _EchoTransit(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.anyio
async def test_seeding_the_real_reidentify_store_lets_reveal_resolve_without_postgres():
    """Simulates BLINDFOLD_OPENBAO_TOKEN having been set at process start: seeding the
    app's real (un-overridden) re-identify store via the same
    VendoredSeedRepository.seed_reidentify_store startup calls, then hitting Reveal
    (Transit stubbed at the network boundary, RBAC granted explicitly) resolves a
    seeded surrogate -- no Postgres/ETL involved.
    """
    transit = _stub_transit()
    vendored_seed_repository().seed_reidentify_store(
        get_reidentify_store(), transit, workspace=DEFAULT_WORKSPACE
    )

    rbac = RbacRegistry()
    rbac.grant("operator", DEFAULT_WORKSPACE, "re-identifier")

    pairs = dict(vendored_seed_repository().seeded_pairs())
    surrogate = pairs["Martin Bach"]

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_transit_client] = lambda: transit
    try:
        async with _make_client() as client:
            resp = await client.get(
                f"/v1/management/surrogate/{surrogate}/real",
                headers={
                    "x-blindfold-identity": "operator",
                    "x-blindfold-workspace": DEFAULT_WORKSPACE,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["real"] == "Martin Bach"


def test_get_entity_graph_returns_same_instance_across_calls_when_database_url_unset(
    monkeypatch,
):
    """Issue #104 regression: with BLINDFOLD_DATABASE_URL unset, get_entity_graph()
    must return the same in-memory singleton across calls so mutations are visible
    across requests within one process (mirrors the pre-slice _entity_graph singleton).

    A fresh EntityGraph() per call would silently discard every mutation between
    consecutive HTTP requests — not a documented acceptable gap, but a silent data loss.
    """
    monkeypatch.delenv("BLINDFOLD_DATABASE_URL", raising=False)

    from blindfold.app import get_entity_graph

    g1 = get_entity_graph()
    g1.add_entity("person", "default", "Alice Example")

    g2 = get_entity_graph()

    # Same object — no fresh construction on the second call.
    assert g1 is g2
    # Mutation from g1 is visible through g2 (process-lifetime stability).
    assert g2.list_entities("default") != []


def test_get_rbac_returns_same_instance_across_calls_when_database_url_unset(
    monkeypatch,
):
    """Issue #105: get_rbac() mirrors get_entity_graph()'s singleton contract (#104) --
    with BLINDFOLD_DATABASE_URL unset, it must return the same in-memory fallback
    across calls so a grant issued in one request is visible to the next within one
    process (a fresh RbacRegistry() per call would silently discard every grant).
    """
    monkeypatch.delenv("BLINDFOLD_DATABASE_URL", raising=False)

    from blindfold.app import get_rbac

    r1 = get_rbac()
    r1.grant("wiring-singleton-check", "default", "viewer")
    try:
        r2 = get_rbac()

        # Same object — no fresh construction on the second call.
        assert r1 is r2
        # Grant from r1 is visible through r2 (process-lifetime stability).
        assert r2.has_role("wiring-singleton-check", "default", "viewer") is True
    finally:
        r1.revoke("wiring-singleton-check", "default", "viewer")


@pytest.mark.anyio
async def test_get_reidentify_store_returns_same_instance_across_calls_when_database_url_unset(
    monkeypatch,
):
    """Issue #105: get_reidentify_store() mirrors get_entity_graph()'s/get_rbac()'s
    singleton contract -- with BLINDFOLD_DATABASE_URL unset, it must return the same
    in-memory fallback across calls so a seeded mapping entry is visible to the next
    request within one process.
    """
    monkeypatch.delenv("BLINDFOLD_DATABASE_URL", raising=False)

    from blindfold.app import get_reidentify_store

    s1 = get_reidentify_store()
    s1.seed("wiring-singleton-surrogate", "default", "vault:v1:wiring-blob")

    s2 = get_reidentify_store()

    # Same object — no fresh construction on the second call.
    assert s1 is s2
    # Seeded entry from s1 is visible through s2 (process-lifetime stability).
    resolved = await s2.surrogate_to_ciphertext("wiring-singleton-surrogate", "default")
    assert resolved == "vault:v1:wiring-blob"
