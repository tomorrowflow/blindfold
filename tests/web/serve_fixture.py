"""Fixture launcher: a real, seeded `blindfold.app` served on a fixed loopback port.

Used by the committed `@playwright/test` browser suite in this directory (issue #50,
UX-7) to drive `/ui/org-graph`, `/ui/entity-list` and the shell's `/ui/inbox` against a
real running server — never a stubbed page. Seeds the exact same in-memory store shapes
and wiring the pytest SPA fixtures use (`tests/test_org_graph_spa.py`,
`tests/test_entity_list_spa.py`, `tests/test_browser_leak_audit.py`): one workspace
("acme") with a person entity whose real name is hidden behind a surrogate, an org term,
an authorized re-identifier ("alice") and an identity with no role on the workspace
("bob"), plus two provisional review-inbox candidates awaiting triage (issue #99).

Run directly: `uv run python tests/web/serve_fixture.py`. Listens on 127.0.0.1:8951
(fixed so `playwright.config.ts` can point `webServer.url` at it) until killed.

Home/Status view (issue #96): two env vars parameterize a second instance for the
Degraded browser-verify specs, launched by `playwright.config.ts`'s second
`webServer` entry rather than a second script --
- `BLINDFOLD_FIXTURE_PORT` -- bind port, default 8951.
- `BLINDFOLD_FIXTURE_STATE` -- "protected" (default) forces all four `/v1/status`
  dependencies healthy, so this port's state is deterministic for every OTHER spec
  file here too (none of them assert on `/v1/status`). "degraded" leaves the real
  unconfigured-L3 default in place (no `BLINDFOLD_OLLAMA_MODEL` in this process's
  env) instead of stubbing a fake outage, so the Degraded render is exercised
  against an honest fail-closed condition, not a synthetic one.
"""

from __future__ import annotations

import base64
import json
import os

import httpx
import uvicorn

from blindfold.app import (
    app,
    get_allowlist,
    get_audit_log,
    get_entity_graph,
    get_l3_health_probe,
    get_rbac,
    get_reidentify_store,
    get_relationship_store,
    get_review_inbox,
    get_store_health_probe,
    get_transit_client,
    get_transit_health_probe,
    get_upstream_health,
)
from blindfold.entity_graph import EntityGraph
from blindfold.policy import AuditLog, AuditRecord
from blindfold.rbac import RbacRegistry
from blindfold.reidentify import InMemoryReIdentificationStore
from blindfold.relationships import RelationshipStore
from blindfold.review import Allowlist, ReviewInbox
from blindfold.status import DependencyHealth
from blindfold.transit import TransitClient

HOST = "127.0.0.1"
PORT = int(os.environ.get("BLINDFOLD_FIXTURE_PORT", "8951"))
FIXTURE_STATE = os.environ.get("BLINDFOLD_FIXTURE_STATE", "protected")
FORCE_DEPENDENCIES_HEALTHY = FIXTURE_STATE != "degraded"
# Setup shell spec (issue #107): a third instance with a genuinely empty store —
# no workspace, no entity, no RBAC grant — so the forced-redirect-to-/setup gate
# and the create-first-workspace/creator-becomes-admin flow exercise real state,
# not a stub.
IS_EMPTY = FIXTURE_STATE == "empty"

WORKSPACE = "acme"
REAL_PERSON = "Martin Bach"
PERSON_SURROGATE = "Clara Hoffmann"
REAL_ORG = "Initech GmbH"
ORG_SURROGATE = "Pinnacle Corp"
CIPHERTEXT = "vault:v1:enc:martin-bach"

# A second, same-kind person sharing REAL_PERSON's real name — the design brief's own
# "planted duplicate" (entity-list-view-design-brief.md §4): drives the entity-list
# shell's same-kind merge-candidate picker (issue #97) and a real-name search that
# must highlight BOTH surrogate rows (ADR-0018's multi-match delta), not just one.
PERSON2_SURROGATE = "Devin Novak"
CIPHERTEXT_PERSON2 = "vault:v1:enc:martin-bach-2"

# A second term entity — the entity list's edge re-target picker needs a second
# same-kind (term) candidate to point an `employer` chip at (issue #97).
REAL_ORG2 = "Initech GmbH Holding"
ORG2_SURROGATE = "Meridian Group"
CIPHERTEXT_ORG2 = "vault:v1:enc:initech-holding"

# Second workspace for multi-workspace switcher tests (issue #95):
# carol holds a role on "beta"; alice has no role on "beta"; bob has no role on either.
WORKSPACE_BETA = "beta"

# Third person and third term — graph-editor-shell spec (#98) needs entities that
# entity-list-shell spec (#97) never mutates (no merge/rename/delete on these).
# Run order: alphabetical → entity-list-shell runs before graph-editor-shell.
# PERSON3: a fresh same-kind candidate for graph merge/edge-draw tests.
# ORG3: a term with PERSON3 as dependent (employer edge) so rename surfaces a warn.
PERSON3_SURROGATE = "Jordan Weiss"
REAL_PERSON3 = "Synthia Bloom"  # unrelated real name — no planted-duplicate needed
CIPHERTEXT_PERSON3 = "vault:v1:enc:synthia-bloom"
ORG3_SURROGATE = "Glacier Tech"
REAL_ORG3 = "Glacier Technology Inc"
CIPHERTEXT_ORG3 = "vault:v1:enc:glacier-tech"

# Two provisional candidates awaiting review (review-inbox shell migration, issue #99).
REVIEW_ITEM_REAL_ONE = "Klaus Bergmann"
REVIEW_ITEM_CONTEXT_ONE = "Please brief Klaus Bergmann on the merger tomorrow."
REVIEW_ITEM_REAL_TWO = "Nordwind Systems"
REVIEW_ITEM_CONTEXT_TWO = "Nordwind Systems signed the new contract yesterday."


def _stub_transit() -> TransitClient:
    plaintext_by_ciphertext = {
        CIPHERTEXT: REAL_PERSON,
        CIPHERTEXT_PERSON2: REAL_PERSON,
        CIPHERTEXT_ORG2: REAL_ORG2,
        CIPHERTEXT_PERSON3: REAL_PERSON3,
        CIPHERTEXT_ORG3: REAL_ORG3,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        real = plaintext_by_ciphertext.get(body.get("ciphertext"))
        if real is not None:
            plaintext = base64.b64encode(real.encode()).decode()
            return httpx.Response(200, json={"data": {"plaintext": plaintext}})
        return httpx.Response(400, json={"errors": ["no such ciphertext"]})

    return TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _build_empty_app():
    """A genuinely empty store: no workspace, no entity, no RBAC grant.

    Each override closes over ONE instance built up-front — a fresh instance
    per lambda call would silently discard every mutation between requests
    (the exact regression issue #104's own fix, commit c2d34d1, guards against
    for get_entity_graph()'s own unset-DSN fallback).
    """
    graph = EntityGraph()
    relationship_store = RelationshipStore()
    rbac = RbacRegistry()
    audit_log = AuditLog()
    reidentify_store = InMemoryReIdentificationStore({})
    review_inbox = ReviewInbox()
    allowlist = Allowlist()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: relationship_store
    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store
    app.dependency_overrides[get_transit_client] = _stub_transit
    app.dependency_overrides[get_review_inbox] = lambda: review_inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist

    if FORCE_DEPENDENCIES_HEALTHY:
        _all_healthy = lambda: _StaticHealthProbe(DependencyHealth(healthy=True))
        app.dependency_overrides[get_upstream_health] = _all_healthy
        app.dependency_overrides[get_l3_health_probe] = _all_healthy
        app.dependency_overrides[get_transit_health_probe] = _all_healthy
        app.dependency_overrides[get_store_health_probe] = _all_healthy

    return app


def build_app():
    if IS_EMPTY:
        return _build_empty_app()

    graph = EntityGraph()
    person = graph.add_entity("person", WORKSPACE, REAL_PERSON, surrogate=PERSON_SURROGATE)
    org = graph.add_entity("term", WORKSPACE, REAL_ORG, surrogate=ORG_SURROGATE)
    person2 = graph.add_entity("person", WORKSPACE, REAL_PERSON, surrogate=PERSON2_SURROGATE)
    org2 = graph.add_entity("term", WORKSPACE, REAL_ORG2, surrogate=ORG2_SURROGATE)
    # person3 and org3: reserved for graph-editor-shell spec (issue #98).
    # entity-list-shell spec (issue #97) must not touch these.
    person3 = graph.add_entity("person", WORKSPACE, REAL_PERSON3, surrogate=PERSON3_SURROGATE)
    org3 = graph.add_entity("term", WORKSPACE, REAL_ORG3, surrogate=ORG3_SURROGATE)

    relationship_store = RelationshipStore()
    relationship_store.create(
        WORKSPACE, "person", person.entity_id, "employer", "term", org.entity_id
    )
    relationship_store.create(
        WORKSPACE, "person", person2.entity_id, "employer", "term", org.entity_id
    )
    # person3 → org3 (employer): used by graph-editor-shell rename-dependent-warning test.
    relationship_store.create(
        WORKSPACE, "person", person3.entity_id, "employer", "term", org3.entity_id
    )
    # EntityGraph keeps its own internal relationship set (merge's edge re-homing,
    # edit_surrogate's coherent-world "inconsistent_dependents" warning) separate from
    # the RelationshipStore instance the /relationships CRUD endpoint (edge chips) uses.
    # Seed both so renaming the org's surrogate legitimately surfaces the dependent
    # soft-warn the entity-list shell's rename UI (issue #97) exercises end to end.
    graph.add_relationship(WORKSPACE, person.entity_id, "person", "employer", org.entity_id, "term")
    graph.add_relationship(WORKSPACE, person2.entity_id, "person", "employer", org.entity_id, "term")
    # person3 → org3 in EntityGraph (rename-dependent-warning for graph-editor-shell).
    graph.add_relationship(WORKSPACE, person3.entity_id, "person", "employer", org3.entity_id, "term")

    rbac = RbacRegistry()
    rbac.grant("alice", WORKSPACE, "re-identifier")
    # "viewer" lets the test suite query GET /v1/management/audit directly (as an
    # authorized auditor would) to assert on audit records, independent of the
    # browser page under test.
    rbac.grant("alice", WORKSPACE, "viewer")
    rbac.grant("alice", WORKSPACE, "curator")
    # Structural entity-list edits (rename, merge) are gated on `admin` by the shipped
    # backend (app.py::edit_entity_surrogate / merge_entities_by_id) — a pre-existing
    # RBAC-vocabulary gap from the settled curator/re-identifier split (ADR-0028) that
    # this migration slice does not re-wire (see commit notes). Granted here so the
    # shell's structural-curation specs can exercise the real endpoints end to end.
    rbac.grant("alice", WORKSPACE, "admin")
    # carol holds a role only on the second workspace — switcher must not show "acme"
    # to carol, and must not show "beta" to alice (multi-workspace fixture, issue #95).
    rbac.grant("carol", WORKSPACE_BETA, "viewer")
    # dave holds ONLY curator on "acme" — no re-identifier, no admin, no viewer. Drives
    # the entity-list shell's "locked without re-identifier while structural curation
    # stays available" acceptance criterion (issue #97): a curator can reach the
    # workspace (unlike bob, who holds no role anywhere) but Reveal/real-name search
    # must show the locked state.
    rbac.grant("dave", WORKSPACE, "curator")

    # Seeded real-space crossings/refusals for the full-page audit log view
    # (issue #102) — one of each kind (reveal/lookup/block), plus a second actor
    # (dave, a denied reveal attempt — SEC-8 audits denials too) and one event
    # dated well outside the default "Last 7 days" window to exercise the
    # time-range filter's exclusion.
    audit_log = AuditLog()
    audit_log.append(
        AuditRecord(workspace=WORKSPACE, event="re-identified", reason="reveal", identity="alice")
    )
    audit_log.append(
        AuditRecord(
            workspace=WORKSPACE,
            event="entity-list-searched",
            reason="hit_count=1",
            identity="alice",
        )
    )
    audit_log.append(
        AuditRecord(
            workspace=WORKSPACE,
            event="re-identify-denied",
            reason="role_required=re-identifier",
            identity="dave",
        )
    )
    audit_log.append(
        AuditRecord(
            workspace=WORKSPACE,
            event="blocked-leak",
            reason="leak_gate: a mapped entity matched the outbound payload",
        )
    )
    audit_log.append(
        AuditRecord(
            workspace=WORKSPACE,
            event="entity-list-searched",
            reason="hit_count=0",
            identity="alice",
            ts="2020-01-01T00:00:00+00:00",
        )
    )
    reidentify_store = InMemoryReIdentificationStore(
        {
            (PERSON_SURROGATE, WORKSPACE): CIPHERTEXT,
            (PERSON2_SURROGATE, WORKSPACE): CIPHERTEXT_PERSON2,
            (ORG2_SURROGATE, WORKSPACE): CIPHERTEXT_ORG2,
            (PERSON3_SURROGATE, WORKSPACE): CIPHERTEXT_PERSON3,
            (ORG3_SURROGATE, WORKSPACE): CIPHERTEXT_ORG3,
        }
    )
    transit = _stub_transit()

    review_inbox = ReviewInbox()
    review_inbox.upsert(REVIEW_ITEM_REAL_ONE, context=REVIEW_ITEM_CONTEXT_ONE)
    review_inbox.upsert(REVIEW_ITEM_REAL_TWO, context=REVIEW_ITEM_CONTEXT_TWO)
    allowlist = Allowlist()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: relationship_store
    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store
    app.dependency_overrides[get_transit_client] = lambda: transit
    app.dependency_overrides[get_review_inbox] = lambda: review_inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist

    if FORCE_DEPENDENCIES_HEALTHY:
        _all_healthy = lambda: _StaticHealthProbe(DependencyHealth(healthy=True))
        app.dependency_overrides[get_upstream_health] = _all_healthy
        app.dependency_overrides[get_l3_health_probe] = _all_healthy
        app.dependency_overrides[get_transit_health_probe] = _all_healthy
        app.dependency_overrides[get_store_health_probe] = _all_healthy

    return app


class _StaticHealthProbe:
    def __init__(self, health: DependencyHealth) -> None:
        self._health = health

    def check(self) -> DependencyHealth:
        return self._health


if __name__ == "__main__":
    build_app()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
