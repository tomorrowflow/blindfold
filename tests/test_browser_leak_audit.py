"""Committed Playwright leak-audit suite for the management SPA (issue #50, UX-7).

Today's `test_*_spa.py` files only assert marker substrings exist in the served HTML
string (`or`-chains that pass almost anything); they can't catch a broken handler, a
bad fetch URL, or a JS exception. This suite drives `/ui/entity-list` in a real
Chromium browser (Playwright) against a seeded, running `blindfold.app` instance,
and asserts the SPA-side privacy properties the `browser-verify` skill defines —
the browser-side counterpart to the proxy request-path leak audit.

NOTE: The `/ui/org-graph` tests (previously tests 1 and 3) are retired with the
legacy embedded page (issue #98). Their privacy properties are now covered by the
committed Playwright spec `tests/web/specs/graph-editor-shell.spec.ts`, which
drives the new React GraphEditor at `/ui/graph`. Test 3's "no CDN request for
Cytoscape" assertion is now covered by `shell-egress-hygiene.spec.ts` (which
already asserts `/ui/graph` makes zero non-loopback requests).

Leak-audit clause analysis (`.claude/skills/leak-audit/SKILL.md`):
- A/B/C/D/E — N/A: the management SPA is not the proxy request path; there is no
  blindfold/restore/streaming/tool-call hop here.
- F (access control) — covered: authorized-only re-identification (reveal succeeds
  for a role-holder, is denied without the role) on `/ui/entity-list`.
- G (mapping secrecy) — covered at the browser boundary: no real entity value
  appears in the DOM or in any network request/response except through an
  authorized, audited reveal.

Additionally (the `browser-verify` properties):
- Audit-on-reveal: every reveal attempt (allowed or denied) produces an audit
  record (SEC-8's "a probing caller always leaves a trail").
- Browser egress hygiene: no real value crosses to a non-first-party origin.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import threading
import time
from types import SimpleNamespace

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

def _chromium_available() -> bool:
    """Whether Playwright's Chromium browser binary is installed.

    Mirrors the Docker skip-guard on the testcontainer suites: the wheel adds the
    Playwright dependency but not the browser binaries (those need an out-of-band
    ``playwright install chromium``), so this suite is skip-guarded to degrade
    gracefully rather than error the whole ``uv run pytest`` when they are absent.
    When Chromium IS installed it never skips, so every leak-audit assertion runs.
    """
    try:
        with sync_playwright() as playwright:
            return os.path.exists(playwright.chromium.executable_path)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_available(),
    reason="Playwright Chromium not installed (run `playwright install chromium`)",
)

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
            httpx.get(f"{base_url}/ui/", timeout=0.5)
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


# NOTE: The helper _click_graph_node() and the org-graph reveal tests (old items
# 1 and 3) are removed here — the legacy /ui/org-graph page is retired by #98.
# Their privacy properties are now covered by:
#   tests/web/specs/graph-editor-shell.spec.ts  (reveal audit, locked state)
#   tests/web/specs/shell-egress-hygiene.spec.ts  (no CDN request for Cytoscape)

# ---------------------------------------------------------------------------
# 1. Entity-list: authorized-only re-identification + audit-on-reveal
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


# NOTE: The third-party egress hygiene test (old test 3) was specific to the legacy
# /ui/org-graph page's CDN-loaded Cytoscape. After issue #98's migration:
# - Cytoscape is vendored (no CDN request at all for /ui/graph) — asserted by
#   tests/web/specs/shell-egress-hygiene.spec.ts which already covers /ui/graph.
# - No test is needed here; the shell's committed Playwright spec covers it.
