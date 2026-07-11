"""Fixture launcher: a real, seeded `blindfold.app` served on a fixed loopback port.

Used by the committed `@playwright/test` browser suite in this directory (issue #50,
UX-7) to drive `/ui/org-graph` and `/ui/entity-list` against a real running server —
never a stubbed page. Seeds the exact same in-memory store shapes and wiring the
pytest SPA fixtures use (`tests/test_org_graph_spa.py`, `tests/test_entity_list_spa.py`,
`tests/test_browser_leak_audit.py`): one workspace ("acme") with a person entity whose
real name is hidden behind a surrogate, an org term, an authorized re-identifier
("alice") and an identity with no role on the workspace ("bob").

Run directly: `uv run python tests/web/serve_fixture.py`. Listens on 127.0.0.1:8951
(fixed so `playwright.config.ts` can point `webServer.url` at it) until killed.
"""

from __future__ import annotations

import base64
import json

import httpx
import uvicorn

from blindfold.app import (
    app,
    get_audit_log,
    get_entity_graph,
    get_rbac,
    get_reidentify_store,
    get_relationship_store,
    get_transit_client,
)
from blindfold.entity_graph import EntityGraph
from blindfold.policy import AuditLog
from blindfold.rbac import RbacRegistry
from blindfold.reidentify import InMemoryReIdentificationStore
from blindfold.relationships import RelationshipStore
from blindfold.transit import TransitClient

HOST = "127.0.0.1"
PORT = 8951

WORKSPACE = "acme"
REAL_PERSON = "Martin Bach"
PERSON_SURROGATE = "Clara Hoffmann"
REAL_ORG = "Initech GmbH"
ORG_SURROGATE = "Pinnacle Corp"
CIPHERTEXT = "vault:v1:enc:martin-bach"

# Second workspace for multi-workspace switcher tests (issue #95):
# carol holds a role on "beta"; alice has no role on "beta"; bob has no role on either.
WORKSPACE_BETA = "beta"


def _stub_transit() -> TransitClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("ciphertext") == CIPHERTEXT:
            plaintext = base64.b64encode(REAL_PERSON.encode()).decode()
            return httpx.Response(200, json={"data": {"plaintext": plaintext}})
        return httpx.Response(400, json={"errors": ["no such ciphertext"]})

    return TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def build_app():
    graph = EntityGraph()
    person = graph.add_entity("person", WORKSPACE, REAL_PERSON, surrogate=PERSON_SURROGATE)
    org = graph.add_entity("term", WORKSPACE, REAL_ORG, surrogate=ORG_SURROGATE)

    relationship_store = RelationshipStore()
    relationship_store.create(
        WORKSPACE, "person", person.entity_id, "employer", "term", org.entity_id
    )

    rbac = RbacRegistry()
    rbac.grant("alice", WORKSPACE, "re-identifier")
    # "viewer" lets the test suite query GET /v1/management/audit directly (as an
    # authorized auditor would) to assert on audit records, independent of the
    # browser page under test.
    rbac.grant("alice", WORKSPACE, "viewer")
    rbac.grant("alice", WORKSPACE, "curator")
    # carol holds a role only on the second workspace — switcher must not show "acme"
    # to carol, and must not show "beta" to alice (multi-workspace fixture, issue #95).
    rbac.grant("carol", WORKSPACE_BETA, "viewer")

    audit_log = AuditLog()
    reidentify_store = InMemoryReIdentificationStore({(PERSON_SURROGATE, WORKSPACE): CIPHERTEXT})
    transit = _stub_transit()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: relationship_store
    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store
    app.dependency_overrides[get_transit_client] = lambda: transit

    return app


if __name__ == "__main__":
    build_app()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
