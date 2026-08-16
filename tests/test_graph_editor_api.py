"""Graph editor API extensions for the SPA (Management-API seam / issue #30).

The SPA operates in surrogate-space: it only has entity IDs and surrogate labels
from the graph endpoint, never canonical (real) names. The merge endpoint from
issue #26 accepts canonical names, but the SPA cannot provide them without first
calling the re-identify endpoint (which requires the re-identifier role).

Since structural edits (merge) require only the curator role (ADR-0016/ADR-0028) —
not re-identifier — the SPA must be able to call merge using entity IDs. This file
tests the ID-based merge path and the entity-details endpoint.

Leak-audit clause analysis:
  A/B/C/D/E — N/A: these endpoints do not touch the proxy request path.
  F (access control) — covered: merge-by-ID requires curator role; entity details
    endpoint returns only surrogate-space data (kind, active_surrogate, variations
    count) and does not require re-identifier.
  G (mapping secrecy) — covered: entity details endpoint returns no canonical
    (real) names; the real name never flows to the SPA without re-identifier.

Management-API exemplar for the shared ``wired_app`` fixture (issue #318): every
test in this file needs exactly the standard stub graph (RBAC, mapping, entity
graph, audit log) ``wired_app`` provides, populated per test via
``wired_app.rbac.grant(...)`` / ``wired_app.entity_graph.add_entity(...)`` /
``wired_app.mapping.seed(...)`` instead of constructing fresh instances and
wiring them into ``app.dependency_overrides`` by hand -- and the autouse
snapshot/restore fixture retires the ``try/finally: app.dependency_overrides.clear()``
pattern this file used to repeat once per test.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


# ---------------------------------------------------------------------------
# 1. Merge endpoint accepts entity IDs as winner/loser specifiers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_endpoint_accepts_entity_ids_as_winner_loser(wired_app):
    wired_app.rbac.grant("curator", "acme", "curator")
    winner = wired_app.entity_graph.add_entity("person", "acme", "Alice Real", surrogate="Alice Sur")
    loser = wired_app.entity_graph.add_entity("person", "acme", "Bob Real", surrogate="Bob Sur")
    wired_app.mapping.seed("Alice Real", "Alice Sur")
    wired_app.mapping.seed("Bob Real", "Bob Sur")

    async with _make_client() as client:
        resp = await client.post(
            "/v1/management/entities/merge",
            json={
                "workspace": "acme",
                "winner": {"entity_id": winner.entity_id},
                "loser": {"entity_id": loser.entity_id},
            },
            headers={"x-blindfold-identity": "curator"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["winner"]["active_surrogate"] == "Alice Sur"
    assert "Bob Sur" in body["winner"]["retired_surrogates"]


# ---------------------------------------------------------------------------
# 2. Merge-by-entity-ID: cross-kind rejected with 422
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_entity_id_cross_kind_rejected(wired_app):
    wired_app.rbac.grant("curator", "acme", "curator")
    person = wired_app.entity_graph.add_entity("person", "acme", "Alice Real", surrogate="Alice Sur")
    term = wired_app.entity_graph.add_entity("term", "acme", "Project X", surrogate="Project Y")
    wired_app.mapping.seed("Alice Real", "Alice Sur")
    wired_app.mapping.seed("Project X", "Project Y")

    async with _make_client() as client:
        resp = await client.post(
            "/v1/management/entities/merge",
            json={
                "workspace": "acme",
                "winner": {"entity_id": person.entity_id},
                "loser": {"entity_id": term.entity_id},
            },
            headers={"x-blindfold-identity": "curator"},
        )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 3. Merge-by-entity-ID: unknown entity_id returns 404
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_entity_id_unknown_entity_returns_404(wired_app):
    wired_app.rbac.grant("curator", "acme", "curator")
    winner = wired_app.entity_graph.add_entity("person", "acme", "Alice Real", surrogate="Alice Sur")

    async with _make_client() as client:
        resp = await client.post(
            "/v1/management/entities/merge",
            json={
                "workspace": "acme",
                "winner": {"entity_id": winner.entity_id},
                "loser": {"entity_id": "nonexistent-id"},
            },
            headers={"x-blindfold-identity": "curator"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. Merge-by-entity-ID requires curator role (ADR-0016/ADR-0028)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_entity_id_denied_without_curator_role(wired_app):
    wired_app.rbac.grant("curator", "acme", "viewer")
    winner = wired_app.entity_graph.add_entity("person", "acme", "Alice Real", surrogate="Alice Sur")
    loser = wired_app.entity_graph.add_entity("person", "acme", "Bob Real", surrogate="Bob Sur")

    async with _make_client() as client:
        resp = await client.post(
            "/v1/management/entities/merge",
            json={
                "workspace": "acme",
                "winner": {"entity_id": winner.entity_id},
                "loser": {"entity_id": loser.entity_id},
            },
            headers={"x-blindfold-identity": "curator"},
        )

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_merge_by_entity_id_admin_without_curator_is_denied(wired_app):
    wired_app.rbac.grant("curator", "acme", "admin")  # admin, not curator -- roles are flat (ADR-0028)
    winner = wired_app.entity_graph.add_entity("person", "acme", "Alice Real", surrogate="Alice Sur")
    loser = wired_app.entity_graph.add_entity("person", "acme", "Bob Real", surrogate="Bob Sur")

    async with _make_client() as client:
        resp = await client.post(
            "/v1/management/entities/merge",
            json={
                "workspace": "acme",
                "winner": {"entity_id": winner.entity_id},
                "loser": {"entity_id": loser.entity_id},
            },
            headers={"x-blindfold-identity": "curator"},
        )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 5. Merge-by-entity-ID response omits canonical_name (surrogate-space only)
# ---------------------------------------------------------------------------
#
# ADR-0015: the re-identifier role gates real-name disclosure. The merge
# endpoint requires curator only (ADR-0016/ADR-0028), never re-identifier. Real-
# value fields (canonical_name, variations) are withheld unconditionally from
# every merge response -- not only the entity_id path -- per the issue #314
# amendment to ADR-0015/ADR-0017.


@pytest.mark.anyio
async def test_merge_by_entity_id_response_omits_real_names(wired_app):
    wired_app.rbac.grant("curator", "acme", "curator")
    winner = wired_app.entity_graph.add_entity("person", "acme", "Alice Real", surrogate="Alice Sur")
    loser = wired_app.entity_graph.add_entity("person", "acme", "Bob Real", surrogate="Bob Sur")
    wired_app.mapping.seed("Alice Real", "Alice Sur")
    wired_app.mapping.seed("Bob Real", "Bob Sur")

    async with _make_client() as client:
        resp = await client.post(
            "/v1/management/entities/merge",
            json={
                "workspace": "acme",
                "winner": {"entity_id": winner.entity_id},
                "loser": {"entity_id": loser.entity_id},
            },
            headers={"x-blindfold-identity": "curator"},
        )

    assert resp.status_code == 200
    body = resp.json()
    # canonical_name and variations (real names) must NOT appear in the entity_id-path
    # response — an admin without re-identifier must not discover real names.
    assert "canonical_name" not in body["winner"], (
        "entity_id-path merge response must not expose canonical_name (ADR-0015)"
    )
    assert "variations" not in body["winner"], (
        "entity_id-path merge response must not expose variations (ADR-0015)"
    )
    # Surrogate-space fields must still be present
    assert body["winner"]["active_surrogate"] == "Alice Sur"
    assert "Bob Sur" in body["winner"]["retired_surrogates"]


# ---------------------------------------------------------------------------
# 6. Edit-surrogate response omits canonical_name (surrogate-space only)
# ---------------------------------------------------------------------------
#
# The PATCH /entities/{entity_id}/surrogate endpoint is always called with
# entity_id (path param). Returning canonical_name in the response would
# reveal real names to an admin without re-identifier, violating ADR-0015.


@pytest.mark.anyio
async def test_edit_surrogate_response_omits_real_names(wired_app):
    wired_app.rbac.grant("curator", "acme", "admin")
    entity = wired_app.entity_graph.add_entity("person", "acme", "Alice Real", surrogate="Alice Sur")
    wired_app.mapping.seed("Alice Real", "Alice Sur")

    async with _make_client() as client:
        resp = await client.patch(
            f"/v1/management/entities/{entity.entity_id}/surrogate",
            json={"workspace": "acme", "new_surrogate": "Alice-New"},
            headers={"x-blindfold-identity": "curator"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "canonical_name" not in body, (
        "edit_surrogate response must not expose canonical_name (ADR-0015)"
    )
    assert body["active_surrogate"] == "Alice-New"


# ---------------------------------------------------------------------------
# 7. Edit-surrogate dependent entries omit canonical_name (surrogate-space only)
# ---------------------------------------------------------------------------
#
# The inconsistent_dependents warning lists coherent-world dependents of the
# edited entity. Each dependent is a real entity; echoing its canonical_name (or
# the raw real name anywhere in the entry) would reveal real names to an admin
# without re-identifier, violating ADR-0015 — the same leak as the top-level
# response, one level deeper. Guards the strip on the dependents list itself.


@pytest.mark.anyio
async def test_edit_surrogate_dependents_omit_real_names(wired_app):
    wired_app.rbac.grant("curator", "acme", "admin")
    # Org (target of the edit) and a person dependent whose relationship targets it.
    org = wired_app.entity_graph.add_entity("term", "acme", "Acme Corp Real", surrogate="Org Sur")
    person = wired_app.entity_graph.add_entity("person", "acme", "Alice Real", surrogate="Alice Sur")
    wired_app.entity_graph.add_relationship(
        workspace="acme",
        source_id=person.entity_id,
        source_kind="person",
        relation="employer",
        target_id=org.entity_id,
        target_kind="term",
    )
    wired_app.mapping.seed("Acme Corp Real", "Org Sur")
    wired_app.mapping.seed("Alice Real", "Alice Sur")

    async with _make_client() as client:
        resp = await client.patch(
            f"/v1/management/entities/{org.entity_id}/surrogate",
            json={"workspace": "acme", "new_surrogate": "Org-New"},
            headers={"x-blindfold-identity": "curator"},
        )

    assert resp.status_code == 200
    body = resp.json()
    dependents = body["inconsistent_dependents"]
    assert len(dependents) == 1, "the person dependent must be reported"
    dep = dependents[0]
    assert "canonical_name" not in dep, (
        "dependent entry must not expose canonical_name (ADR-0015)"
    )
    # No real name may appear anywhere in the entry — belt-and-suspenders against
    # a real value smuggled into any dependent field.
    assert "Alice Real" not in repr(dep), (
        "dependent entry must not leak the real name in any field (ADR-0015)"
    )
    assert dep["active_surrogate"] == "Alice Sur"
