"""Committed Playwright leak-audit suite for the management SPA (issue #50, UX-7).

Today's `test_*_spa.py` files only assert marker substrings exist in the served HTML
string (`or`-chains that pass almost anything); they can't catch a broken handler, a
bad fetch URL, or a JS exception. This suite drives `/ui/entity-list` and
`/ui/org-graph` in a real Chromium browser (Playwright) against a seeded, running
`blindfold.app` instance, and asserts the SPA-side privacy properties the
`browser-verify` skill defines — the browser-side counterpart to the proxy
request-path leak audit.

Leak-audit clause analysis (`.claude/skills/leak-audit/SKILL.md`):
- A/B/C/D/E — N/A: the management SPA is not the proxy request path; there is no
  blindfold/restore/streaming/tool-call hop here.
- F (access control) — covered: authorized-only re-identification (reveal succeeds
  for a role-holder, is denied without the role) on both `/ui/org-graph` and
  `/ui/entity-list`.
- G (mapping secrecy) — covered at the browser boundary: no real entity value
  appears in the DOM or in any network request/response except through an
  authorized, audited reveal; a third-party origin (the Cytoscape/Vue CDN loads)
  never carries a real value.

Additionally (the `browser-verify` properties):
- Audit-on-reveal: every reveal attempt (allowed or denied) produces an audit
  record (SEC-8's "a probing caller always leaves a trail").
- Browser egress hygiene: no real value crosses to a non-first-party origin.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlparse

import httpx
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

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

WORKSPACE = "acme"
REAL_PERSON = "Martin Bach"
PERSON_SURROGATE = "Clara Hoffmann"
REAL_ORG = "Initech GmbH"
ORG_SURROGATE = "Pinnacle Corp"
CIPHERTEXT = "vault:v1:enc:martin-bach"


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


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def blindfold_server():
    """A real, seeded `blindfold.app` served by uvicorn on a loopback port.

    Seeds the same in-memory stores the pytest SPA fixtures use
    (`app.dependency_overrides`) so this suite reuses the exact wiring/data shape
    of `tests/test_org_graph_spa.py` / `tests/test_entity_list_spa.py`, rather than
    inventing a separate one: one workspace ("acme") with a person entity whose real
    name is hidden behind a surrogate, an authorized re-identifier ("alice") and an
    identity with no role on the workspace ("bob").
    """
    graph = EntityGraph()
    person = graph.add_entity("person", WORKSPACE, REAL_PERSON, surrogate=PERSON_SURROGATE)
    org = graph.add_entity("term", WORKSPACE, REAL_ORG, surrogate=ORG_SURROGATE)

    relationship_store = RelationshipStore()
    relationship_store.create(
        WORKSPACE, "person", person.entity_id, "employer", "term", org.entity_id
    )

    rbac = RbacRegistry()
    rbac.grant("alice", WORKSPACE, "re-identifier")

    audit_log = AuditLog()
    reidentify_store = InMemoryReIdentificationStore({(PERSON_SURROGATE, WORKSPACE): CIPHERTEXT})
    transit = _stub_transit()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: relationship_store
    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store
    app.dependency_overrides[get_transit_client] = lambda: transit

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base_url}/ui/org-graph", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        raise RuntimeError("blindfold server did not come up in time")

    try:
        yield SimpleNamespace(base_url=base_url, audit_log=audit_log)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture
def alice_page(browser):
    """Browser context for "alice", who holds the re-identifier role on acme."""
    context = browser.new_context(extra_http_headers={"x-blindfold-identity": "alice"})
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture
def bob_page(browser):
    """Browser context for "bob", who holds no role on acme (unauthorized)."""
    context = browser.new_context(extra_http_headers={"x-blindfold-identity": "bob"})
    page = context.new_page()
    yield page
    context.close()


def _click_graph_node(page, label: str) -> None:
    """Click a Cytoscape node by label.

    The graph renders to a <canvas> with no per-node DOM elements, so we read the
    node's renderedPosition() via the test-only `window.__blindfoldGraph` hook
    (spa.py) and issue a real mouse click at that screen coordinate.
    """
    page.wait_for_function(
        "() => window.__blindfoldGraph && window.__blindfoldGraph.nodes().length > 0"
    )
    point = page.evaluate(
        """(label) => {
            const cy = window.__blindfoldGraph;
            const node = cy.nodes().filter(n => n.data('label') === label)[0];
            const rp = node.renderedPosition();
            const rect = document.getElementById('cy').getBoundingClientRect();
            return { x: rect.left + rp.x, y: rect.top + rp.y };
        }""",
        label,
    )
    page.mouse.click(point["x"], point["y"])


# ---------------------------------------------------------------------------
# 1. Org-graph: authorized-only re-identification + audit-on-reveal
# ---------------------------------------------------------------------------


def test_org_graph_reveal_authorized_shows_real_value_and_is_audited(
    blindfold_server, alice_page
):
    page = alice_page
    page.goto(f"{blindfold_server.base_url}/ui/org-graph?workspace={WORKSPACE}")

    _click_graph_node(page, PERSON_SURROGATE)
    page.wait_for_selector("#reveal-badge-btn", state="visible")
    page.click("#reveal-badge-btn")
    page.wait_for_selector("#reveal-audit-backdrop.open")
    page.click("#reveal-confirm-yes")

    page.wait_for_function(
        f"() => document.getElementById('reveal-badge-btn').textContent === {REAL_PERSON!r}"
    )
    assert page.locator("#reveal-badge-btn").inner_text() == REAL_PERSON

    reveals = [
        r
        for r in blindfold_server.audit_log.records
        if r.event == "re-identified" and r.identity == "alice"
    ]
    assert reveals, "authorized reveal must be audited as re-identified"
    assert reveals[-1].reason == f"surrogate={PERSON_SURROGATE}"
    # The real name is never in the audit record — only the surrogate (CONTEXT invariant).
    assert REAL_PERSON not in reveals[-1].reason


def test_org_graph_reveal_denied_without_role_never_shows_real_value_and_is_audited_as_denied(
    blindfold_server, bob_page
):
    page = bob_page
    page.goto(f"{blindfold_server.base_url}/ui/org-graph?workspace={WORKSPACE}")

    _click_graph_node(page, PERSON_SURROGATE)
    page.wait_for_selector("#reveal-badge-btn", state="visible")
    page.click("#reveal-badge-btn")
    page.wait_for_selector("#reveal-audit-backdrop.open")
    page.click("#reveal-confirm-yes")

    page.wait_for_selector("#reveal-badge-locked", state="visible")
    assert page.locator("#reveal-badge-locked").inner_text() == "locked"
    assert REAL_PERSON not in page.content()

    denials = [
        r
        for r in blindfold_server.audit_log.records
        if r.event == "re-identify-denied" and r.identity == "bob"
    ]
    assert denials, "a denied reveal attempt must be audited too (SEC-8)"
    assert denials[-1].reason == f"surrogate={PERSON_SURROGATE}"


# ---------------------------------------------------------------------------
# 2. Entity-list: authorized-only re-identification + audit-on-reveal
# ---------------------------------------------------------------------------


def test_entity_list_reveal_authorized_shows_real_value_and_is_audited(
    blindfold_server, alice_page
):
    page = alice_page
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(f"{blindfold_server.base_url}/ui/entity-list?workspace={WORKSPACE}")

    row = page.locator("tr", has_text=PERSON_SURROGATE)
    row.locator("button.reveal-badge").click()

    page.wait_for_selector(f"tr:has-text('{PERSON_SURROGATE}') .reveal-value")
    assert row.locator(".reveal-value").inner_text() == REAL_PERSON

    reveals = [
        r
        for r in blindfold_server.audit_log.records
        if r.event == "re-identified" and r.identity == "alice"
    ]
    assert reveals, "authorized reveal must be audited as re-identified"


def test_entity_list_reveal_locked_without_role_real_value_never_appears(
    blindfold_server, bob_page
):
    page = bob_page
    requests: list[str] = []
    page.on("request", lambda req: requests.append(req.url))
    page.goto(f"{blindfold_server.base_url}/ui/entity-list?workspace={WORKSPACE}")

    row = page.locator("tr", has_text=PERSON_SURROGATE)
    reveal_btn = row.locator("button.reveal-badge")
    reveal_btn.wait_for(state="visible")
    assert reveal_btn.is_disabled()
    assert reveal_btn.inner_text() == "locked"
    assert REAL_PERSON not in page.content()
    # No role -> the client never even attempts the reveal call (surrogate-space
    # only), so no re-identify request should have been sent for this surrogate.
    assert not any("/v1/management/surrogate/" in url for url in requests)


# ---------------------------------------------------------------------------
# 3. Browser egress hygiene: no real value to a third-party origin
# ---------------------------------------------------------------------------


def test_browser_egress_hygiene_no_real_value_to_third_party_origin(
    blindfold_server, alice_page
):
    page = alice_page
    requests = []
    page.on("request", lambda req: requests.append(req))

    page.goto(f"{blindfold_server.base_url}/ui/org-graph?workspace={WORKSPACE}")
    _click_graph_node(page, PERSON_SURROGATE)
    page.wait_for_selector("#reveal-badge-btn", state="visible")
    page.click("#reveal-badge-btn")
    page.wait_for_selector("#reveal-audit-backdrop.open")
    page.click("#reveal-confirm-yes")
    page.wait_for_function(
        f"() => document.getElementById('reveal-badge-btn').textContent === {REAL_PERSON!r}"
    )

    first_party = urlparse(blindfold_server.base_url).netloc
    third_party = [r for r in requests if urlparse(r.url).netloc != first_party]
    # Sanity: the page does load a third-party CDN script (Cytoscape) — otherwise
    # this assertion would be vacuous.
    assert third_party, "expected at least one third-party (CDN) request to check"

    for req in third_party:
        haystack = req.url + json.dumps(dict(req.headers)) + (req.post_data or "")
        for real_value in (REAL_PERSON, REAL_ORG):
            assert real_value not in haystack, (
                f"real entity value {real_value!r} leaked to third-party origin {req.url}"
            )
