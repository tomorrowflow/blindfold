"""App-wiring: the process-wide singletons are actually seeded at import (issue #43 /
UX-1), not merely the underlying seeding functions in isolation.

Deliberately does NOT override get_entity_graph/get_relationship_store -- the point is
to prove a fresh process's org-graph and entity-list render the vendored seed out of
the box, mirroring how the existing proxy round-trip tests exercise the real ``_mapping``
singleton (also built from the vendored seed at import) without overriding get_mapping.

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

from blindfold.app import app, get_rbac, get_reidentify_store, get_transit_client
from blindfold.bootstrap import bootstrap_admin
from blindfold.policy import DEFAULT_WORKSPACE
from blindfold.rbac import RbacRegistry
from blindfold.store import vendored_seed_repository
from blindfold.transit import TransitClient


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


@pytest.mark.anyio
async def test_default_workspace_entity_list_renders_the_vendored_seed_out_of_the_box():
    async with _make_client() as client:
        resp = await client.get("/v1/management/workspaces/default/entities")

    assert resp.status_code == 200
    entities = resp.json()["entities"]
    # Not empty: a fresh install must not show a blank workspace (finding UX-1).
    assert len(entities) >= 5 + 3  # 5 seeded persons + 3 seeded terms
    surrogates = {e["active_surrogate"] for e in entities}
    pairs = dict(vendored_seed_repository().seeded_pairs())
    assert pairs["Martin Bach"] in surrogates
    assert pairs["Enervia"] in surrogates


@pytest.mark.anyio
async def test_default_workspace_org_graph_renders_seeded_nodes_and_the_seeded_edge():
    async with _make_client() as client:
        resp = await client.get("/v1/management/workspaces/default/graph")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["nodes"]) >= 5 + 3
    # The vendored seed's one entity_relationship (Enervia subsidiary_of Voltwerk).
    assert any(edge["relation"] == "subsidiary_of" for edge in body["edges"])


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
