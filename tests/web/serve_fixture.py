"""Fixture launcher: a real, seeded `blindfold.app` served on a fixed loopback port.

Used by the committed `@playwright/test` browser suite in this directory (issue #50,
UX-7) to drive the shell's `/ui/graph`, `/ui/entities`, and `/ui/inbox` views against a
real running server — never a stubbed page (both `/ui/org-graph` and `/ui/entity-list`,
the legacy embedded pages this fixture originally targeted, are retired — issues #98,
#128). Seeds the exact same in-memory store shapes and wiring the pytest SPA fixtures
use (`tests/test_org_graph_spa.py`, `tests/test_entity_list_spa.py`): one workspace
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
  unconfigured-L3 default in place (no `BLINDFOLD_L3_MODEL` in this process's
  env) instead of stubbing a fake outage, so the Degraded render is exercised
  against an honest fail-closed condition, not a synthetic one.

Setup's unencrypted-persistence honesty banner (ADR-0045 §4/§10, issue #227) needs
a fixture instance with an actual *persistent* store, unlike every other instance
here -- `BLINDFOLD_FIXTURE_PERSISTENT_STORE=1` opts a process into that (a fresh
per-process scratch SQLite file, never the real host's app-data path). Pair with
`BLINDFOLD_OPENBAO_TOKEN` unset (the banner's "no mapping cipher" case) or set (the
banner's "Transit cipher configured" case). Setting the token makes
`blindfold.app`'s own module-level startup bootstrap construct a *real*
`TransitClient` before any `dependency_overrides` exist to intercept it (every
request-time Transit call this fixture makes goes through this file's own
`_stub_transit()` MockTransport instead) -- so a loopback-only stub OpenBao HTTP
server is started first and `BLINDFOLD_OPENBAO_ADDR` pointed at it, never a real
OpenBao daemon.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Keep every fixture instance ephemeral by default (ADR-0043 §1, issue #204): an
# *unset* BLINDFOLD_DATABASE_URL now resolves to a durable SQLite store at the
# real host's app-data path, not "in-memory" as this fixture originally assumed
# (every existing spec here bakes in has_persistent_store=False unless it opts
# into persistence below). Set BEFORE `blindfold.app` is imported -- that import
# already triggers a module-level `get_settings()` call, so setting this any
# later would still let one real file get touched under the operator's actual
# home directory at import time.
if os.environ.get("BLINDFOLD_FIXTURE_PERSISTENT_STORE") == "1":
    _persistent_store_dir = tempfile.mkdtemp(prefix="blindfold-fixture-store-")
    os.environ.setdefault(
        "BLINDFOLD_DATABASE_URL",
        f"sqlite:///{Path(_persistent_store_dir) / 'blindfold.sqlite3'}",
    )
os.environ.setdefault("BLINDFOLD_DATABASE_URL", "memory://")

# Issue #264: mirror this fixture's own bind address into BLINDFOLD_HOST/
# BLINDFOLD_PORT so GET /v1/status's config.host/config.port -- which the Connect
# page's snippets render verbatim -- reflect where this fixture actually listens,
# the same way a real `blindfold serve --port N` invocation keeps settings.host/
# settings.port in lockstep with the real bind (config.py). Left unset, every
# instance would report the compiled-in default (127.0.0.1:25463) instead, which
# collides with Connect.tsx's own hardcoded fallback constant -- making a real
# fetch and a silent fallback-to-default bug look identical in the DOM. Must be
# set before `blindfold.app` is imported (its module-level get_settings() call).
os.environ.setdefault("BLINDFOLD_HOST", "127.0.0.1")
os.environ.setdefault("BLINDFOLD_PORT", os.environ.get("BLINDFOLD_FIXTURE_PORT", "8951"))


def _start_stub_openbao_server() -> str:
    """A minimal loopback-only HTTP stub round-tripping Transit's encrypt/decrypt shape.

    Needed for the real network calls `blindfold.app`'s own module-level startup
    bootstrap makes when `BLINDFOLD_OPENBAO_TOKEN` is set (issue #227's "Transit
    cipher configured" banner-hidden case): first `bootstrap_from_vendored_seed`
    encrypts the vendored seed into the re-identify store, then (issue #343)
    `hydrate_mapping_from_reidentify_store` decrypts it straight back out -- both
    calls fire before this fixture's own `app.dependency_overrides[get_transit_client]`
    (the MockTransport `_stub_transit()` below) exists to intercept them.

    Issue #364: this used to answer every POST with the same fixed, fabricated
    `{"data": {"ciphertext": ...}}` body regardless of path -- fine for the
    encrypt-only call site that was here at issue #227, but #343's later decrypt
    call against that same fixed body has no `data.plaintext` field, so
    `TransitClient.decrypt` failed every fixture boot with a bare `KeyError`
    (`transit.py` now names that failure -- see `TransitError` -- but the fixture
    itself must still answer honestly). Mints a real per-request ciphertext on
    encrypt and remembers its plaintext (still never a real entity value: the only
    caller is the vendored-seed bootstrap) so a matching decrypt call resolves it,
    the same in-process round-trip `_stub_transit()`'s own MockTransport handler
    already does below -- just answered over a real loopback socket instead of a
    MockTransport, since this path runs before dependency_overrides exist.
    """

    plaintext_by_ciphertext: dict[str, str] = {}
    minted_counter = [0]

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path.endswith("/encrypt/blindfold-mapping"):
                minted_counter[0] += 1
                ciphertext = f"vault:v1:fixture-stub-ciphertext-{minted_counter[0]}"
                plaintext_by_ciphertext[ciphertext] = body["plaintext"]
                self._respond(200, {"data": {"ciphertext": ciphertext}})
                return
            if self.path.endswith("/decrypt/blindfold-mapping"):
                encoded = plaintext_by_ciphertext.get(body.get("ciphertext"))
                if encoded is None:
                    self._respond(404, {"errors": ["no such ciphertext"]})
                    return
                self._respond(200, {"data": {"plaintext": encoded}})
                return
            self._respond(404, {"errors": ["no such transit operation"]})

        def _respond(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"


if os.environ.get("BLINDFOLD_OPENBAO_TOKEN"):
    os.environ.setdefault("BLINDFOLD_OPENBAO_ADDR", _start_stub_openbao_server())

import httpx
import uvicorn

from blindfold.app import (
    _leak_gate_or_block,
    app,
    get_allowlist,
    get_audit_log,
    get_block_history,
    get_entity_graph,
    get_gliner_activation_store,
    get_gliner_classifier_factory,
    get_gliner_hub_client,
    get_gliner_provisioning_tracker,
    get_l3_health_probe,
    get_mapping,
    get_mapping_cipher,
    get_payload_inspection,
    get_processing_trace,
    get_rbac,
    get_reidentify_store,
    get_relationship_store,
    get_review_inbox,
    get_rewritten_leaf_store,
    get_store_health_probe,
    get_transit_client,
    get_transit_health_probe,
    get_upstream_client,
    get_upstream_health,
)
from blindfold.entity_graph import EntityGraph
from blindfold.gliner_status import GlinerProvisioningTracker
from blindfold.mapping_cipher import LocalKeyCipher
from blindfold.payload_inspection import PayloadInspection
from blindfold.policy import AuditLog, AuditRecord
from blindfold.processing_trace import ProcessingTraceBuffer
from blindfold.rbac import RbacRegistry
from blindfold.reidentify import InMemoryReIdentificationStore
from blindfold.relationships import RelationshipStore
from blindfold.review import Allowlist, ReviewInbox
from blindfold.rewritten_leaves import RewrittenLeaf, RewrittenLeafStore, RewrittenSpan
from blindfold.status import BlockHistory, DependencyHealth, RecentFailureHealth
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.transit import TransitClient
from blindfold.upstream import UpstreamClient

HOST = "127.0.0.1"
PORT = int(os.environ.get("BLINDFOLD_FIXTURE_PORT", "8951"))
# Settings -> Detection (issue #147, ADR-0034 §5): an install-global, per-process
# scratch Data directory so every fixture instance starts "not provisioned" --
# never the real host's app-data path (this is a test fixture, not a real install).
os.environ.setdefault("BLINDFOLD_DATA_DIR", tempfile.mkdtemp(prefix="blindfold-fixture-data-"))
FIXTURE_STATE = os.environ.get("BLINDFOLD_FIXTURE_STATE", "protected")
FORCE_DEPENDENCIES_HEALTHY = FIXTURE_STATE != "degraded"
# Setup shell spec (issue #107): a third instance with a genuinely empty store —
# no workspace, no entity, no RBAC grant — so the forced-redirect-to-/setup gate
# and the create-first-workspace/creator-becomes-admin flow exercise real state,
# not a stub.
IS_EMPTY = FIXTURE_STATE == "empty"
# Ninth and tenth fixture instances (issue #400, relocated to Payload
# inspection's own destination by #432): needs its own armed-with-retained-
# leaves and disarmed fixture state, distinct from the primary instance's 3
# seeded Processing trace rows (issue #151, seeded before `exchange_id`
# existed) -- see _build_payload_inspection_retained_fixture below.
PAYLOAD_INSPECTION_RETAINED = FIXTURE_STATE == "payload_inspection_retained"
PAYLOAD_INSPECTION_DISARMED_ONLY = FIXTURE_STATE == "payload_inspection_disarmed"
# Eleventh fixture instance (issue #417, browser-verify): the two causes behind
# `leak_detected`'s taxonomy split are both real-blinder-miss safety-net paths --
# neither is reachable by posting ordinary text at a healthy `/v1/messages`
# (detection is supposed to catch both cases first). Seeded directly onto this
# port's own `block_history` via the real `_leak_gate_or_block` funnel (never a
# hand-typed scrubbed_reason -- see build_app()) so the Home/Status recent-blocks
# table's differentiated remedy copy renders against genuine BlockRecord shapes,
# not a fixture guess at the string format. Kept off the primary/shared port:
# home-status.spec.ts's "recent-blocks empty state" test asserts that port's
# blocks.recent stays empty.
LEAK_TAXONOMY = FIXTURE_STATE == "leak_taxonomy"

WORKSPACE = "acme"
REAL_PERSON = "Martin Bach"
PERSON_SURROGATE = "Clara Hoffmann"
REAL_ORG = "Initech GmbH"
ORG_SURROGATE = "Pinnacle Corp"
CIPHERTEXT = "vault:v1:enc:martin-bach"
# Issue #401: ORG_SURROGATE had no reidentify_store/Transit entry at all before
# this -- nothing exercised revealing it until the exchange-level bulk Reveal
# switch auto-gathers every `confirmed` surrogate a retained exchange carries
# (including this one, seeded into `processing_trace_mapping` via the vendored
# seed below). Without this, the fixture's own "passed, retained" exchange
# would fail closed on every flip (ADR-0059 §5's own fail-closed-on-partial-
# batch behaviour), never revealing even PERSON_SURROGATE alongside it.
CIPHERTEXT_ORG = "vault:v1:enc:initech-gmbh"

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

# The leak-taxonomy fixture's own defect-cause real value (issue #417): a
# mapping-known "miss" simulated the same way tests/test_leak_detected_curation_
# sub_reason.py's `_LeakyMapping` does. Distinct from every other real value
# seeded above so this fixture's two ports never share entity content.
LEAK_TAXONOMY_DEFECT_REAL = "Rutherford Kessling"

# Processing trace's own "confirmed" surrogate chip (issue #154, ADR-0035): a
# hop-injected surrogate that's already a re-identifiable known entity, distinct
# from every entity-list/graph-editor fixture entity above so trace-view reveal
# specs never collide with theirs.
TRACE_HOP_REAL = "Klaus Weber"
TRACE_HOP_SURROGATE = "Berta Vogel"
CIPHERTEXT_TRACE_HOP = "vault:v1:enc:klaus-weber"


def _stub_transit() -> TransitClient:
    """A TransitClient whose decrypt() resolves the fixture's pre-seeded real
    values, AND whose encrypt() mints a fresh ciphertext round-tripping back
    through decrypt() -- Setup's Seed-bundle import / Sample-data seed path
    (issue #108) calls encrypt() to populate the re-identify store, which the
    pre-issue-#108 decrypt-only stub never exercised (a real gap this fixture
    had, the same class as the `lambda: EntityGraph()` one issue #107's own
    commit found and fixed).
    """
    plaintext_by_ciphertext = {
        CIPHERTEXT: REAL_PERSON,
        CIPHERTEXT_ORG: REAL_ORG,
        CIPHERTEXT_PERSON2: REAL_PERSON,
        CIPHERTEXT_ORG2: REAL_ORG2,
        CIPHERTEXT_PERSON3: REAL_PERSON3,
        CIPHERTEXT_ORG3: REAL_ORG3,
        CIPHERTEXT_TRACE_HOP: TRACE_HOP_REAL,
    }
    minted_counter = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/encrypt/blindfold-mapping"):
            minted_counter[0] += 1
            ciphertext = f"vault:v1:seed-mint:{minted_counter[0]}"
            plaintext_by_ciphertext[ciphertext] = base64.b64decode(body["plaintext"]).decode()
            return httpx.Response(200, json={"data": {"ciphertext": ciphertext}})
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


# Test connection (issue #265): a network-free stand-in for the shared
# `get_upstream_client` seam `POST /v1/messages` egresses through. Left
# unstubbed, a real browser click on the Connect page's "Test connection"
# button would build a real `httpx.AsyncClient` pointed at the compiled-in
# `DEFAULT_UPSTREAM_BASE_URL` (https://api.anthropic.com) and place a genuine
# outbound call -- non-hermetic (depends on this sandbox's network egress and
# a real provider credential) and non-deterministic (whatever that live call
# happens to return). Mirrors tests/test_test_connection_endpoint.py's own
# `_echoing_stub_upstream`: echoes the canary's surrogate back verbatim so
# restore has something to resolve, landing every ordinary click on
# `blindfolded_ok` (never `blindfolded_ok_restore_unproven`, since that needs a
# *non*-echoing upstream). One sentinel model id (`TEST_CONNECTION_TRIGGER_401`)
# answers 401 instead, so a browser-verify spec can also exercise a second,
# visually-distinct point in the typed failure taxonomy (Q4) against this
# feature's one real user-facing input field -- `base_url` itself is not a form
# field on the Connect page (Q3: proxy-side only), so no other taxonomy entry
# is reachable from a real click without stubbing the SPA's own endpoint.
TEST_CONNECTION_TRIGGER_401 = "trigger-test-connection-401"


def _stub_upstream() -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        if sent.get("model") == TEST_CONNECTION_TRIGGER_401:
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})
        blinded_text = sent["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": blinded_text}],
                "model": sent.get("model", "m"),
                "stop_reason": "end_turn",
            },
        )

    return UpstreamClient(
        base_url="http://upstream.test",
        client=httpx.AsyncClient(
            base_url="http://upstream.test", transport=httpx.MockTransport(handler)
        ),
    )


# Settings -> Detection (issue #147, ADR-0034 §5): a network-free stand-in for the
# GLiNER hub client, and its own small stand-in manifest -- writes/verifies against
# fabricated bytes rather than the real ~197 MB pinned model, so a browser-driven
# retry click in this fixture never reaches the real network.
_STUB_GLINER_FILE_CONTENT = b"fixture stand-in gliner model bytes"
_STUB_GLINER_MANIFEST = {
    "gliner_config.json": hashlib.sha256(_STUB_GLINER_FILE_CONTENT).hexdigest()
}


class _StubGlinerHubClient:
    def snapshot_download(self, *, repo_id, revision, local_dir, allow_patterns):
        for name in allow_patterns:
            path = Path(local_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_STUB_GLINER_FILE_CONTENT)
        return local_dir


class _StubGlinerClassifier:
    """Test double for the GlinerClassifier seam (issue #159's activation smoke
    test) -- mirrors tests/test_gliner_detection_settings_admin.py's own
    `_StubClassifier`. The fabricated `_STUB_GLINER_FILE_CONTENT` bytes above are
    not a real ONNX model, so the real `GlinerOnnxClassifier` (the
    `get_gliner_classifier_factory` production default) can never load them --
    a browser-driven retry click must never require the real `gliner` package.
    """

    def classify(self, candidate) -> bool:
        return True


def _stub_gliner_classifier_factory(model_path: str) -> _StubGlinerClassifier:
    return _StubGlinerClassifier()


class _InMemoryGlinerActivationStore:
    """Test double for PostgresActivationSettingsStore's get/set surface (#145) --
    this fixture has no `BLINDFOLD_DATABASE_URL`, so without this override
    `get_gliner_activation_store()` would always resolve to `None` and the "restart
    prompt after a fresh provision" browser-verify criterion could never render.
    """

    def __init__(self) -> None:
        self._activated = False

    def get_l3_gliner_activated(self) -> bool:
        return self._activated

    def set_l3_gliner_activated(self, activated: bool) -> None:
        self._activated = activated


def _apply_gliner_detection_overrides() -> None:
    """Wire the detection/settings view's seams to fixture-local, network-free
    doubles (issue #147). The activation store and provisioning tracker must be
    *single instances* shared across requests (state persists between the GET status
    read and the POST retry write, within one browser session) -- unlike the stub
    hub client, which is stateless and safe to reconstruct per request.
    """
    import blindfold.gliner_provisioning as gliner_provisioning_module

    gliner_provisioning_module.GLINER_MODEL_MANIFEST = _STUB_GLINER_MANIFEST
    activation_store = _InMemoryGlinerActivationStore()
    tracker = GlinerProvisioningTracker()
    app.dependency_overrides[get_gliner_hub_client] = _StubGlinerHubClient
    app.dependency_overrides[get_gliner_activation_store] = lambda: activation_store
    app.dependency_overrides[get_gliner_provisioning_tracker] = lambda: tracker
    app.dependency_overrides[get_gliner_classifier_factory] = lambda: _stub_gliner_classifier_factory


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
    # Issue #430: re-identify now decrypts through whichever mapping cipher is
    # active, not Transit specifically -- mirror the same stub onto
    # get_mapping_cipher so Reveal keeps resolving here exactly as before.
    app.dependency_overrides[get_mapping_cipher] = _stub_transit
    app.dependency_overrides[get_review_inbox] = lambda: review_inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist

    if FORCE_DEPENDENCIES_HEALTHY:
        # latency_ms is set on the three actively-probed dependencies only, never on
        # upstream -- it has no standalone active probe in production either (its
        # health is the passive RecentFailureHealth signal), so a fabricated number
        # there would be dishonest, not just untestable (issue #110).
        _all_healthy = lambda: _StaticHealthProbe(DependencyHealth(healthy=True, latency_ms=8.0))
        # RecentFailureHealth, not a static double (issue #265): unlike the other
        # three dependencies, production's real get_upstream_health is this exact
        # class, updated by every real exchange's own mark_success()/mark_failure()
        # calls (app.py's _exchange). A one-off `_StaticHealthProbe(...)` answers
        # `.check()` fine but has neither method -- invisible as long as nothing
        # ever drove a real /v1/messages exchange through this fixture, which
        # Test Connection now does. One shared instance (not a fresh one per
        # lambda call) for the same reason _apply_gliner_detection_overrides's own
        # activation_store/tracker are shared: state must persist across requests.
        _upstream_health = RecentFailureHealth()
        app.dependency_overrides[get_upstream_health] = lambda: _upstream_health
        app.dependency_overrides[get_l3_health_probe] = _all_healthy
        app.dependency_overrides[get_transit_health_probe] = _all_healthy
        app.dependency_overrides[get_store_health_probe] = _all_healthy

    _apply_gliner_detection_overrides()
    return app


def _build_payload_inspection_retained_fixture(*, armed: bool, pending_surrogate: str):
    """Issue #400, relocated to its own destination by #432: seeds the state
    Payload inspection's retained-exchange list/detail needs -- an armed,
    retained "passed" exchange (spans, including a deliberately overlapping
    pair -- ADR-0059 §3), an armed, retained "blocked" exchange (the "never
    sent" mark), one Processing-trace-only exchange that predates arming, and
    one that's armed but simply never retained -- neither of the latter two
    is ever retained, so neither appears in Payload inspection's own list;
    they exist only so the Processing trace's own link-or-nothing rendering
    (issue #432) has a non-retained row to assert against.

    A dedicated `ProcessingTraceBuffer`/`RewrittenLeafStore`/`PayloadInspection`
    triple, replacing (not joining) the standard 3-row seed built above --
    this only ever backs its own two fixture ports (payload_inspection_retained
    / payload_inspection_disarmed), never the primary instance every other spec
    file shares, so it can't perturb their assertions.

    Issue #401's own verification bar ("a real exchange containing all three
    lifecycles") needs the "passed, retained" exchange itself to carry a
    `confirmed`, a `pending`, and a `rejected` surrogate side by side --
    `pending_surrogate` is the caller's own already-seeded review-inbox
    candidate (`review_item_one.provisional_surrogate`, "pending" against the
    SAME `review_inbox`/`processing_trace_mapping` this fixture's `get_mapping`/
    `get_review_inbox` overrides already share with the base fixture), and
    "Igor Talvik" mirrors the base fixture's own "rejected" precedent
    (recognized by neither store).
    """
    armed_at = "2020-01-01T00:00:10+00:00"
    ts_sequence = iter(
        [
            "2020-01-01T00:00:00+00:00",  # predates arming
            "2020-01-01T00:00:20+00:00",  # passed, retained
            "2020-01-01T00:00:21+00:00",  # blocked, retained ("never sent")
            "2020-01-01T00:00:22+00:00",  # armed, but never retained ("evicted")
        ]
    )
    trace = ProcessingTraceBuffer(now_iso=lambda: next(ts_sequence))
    payload_inspection = PayloadInspection(now_iso=lambda: armed_at)
    if armed:
        payload_inspection.arm()
    store = RewrittenLeafStore()

    predates_id = "retained-fixture-predates-arming"
    passed_id = "retained-fixture-passed"
    blocked_id = "retained-fixture-blocked"
    evicted_id = "retained-fixture-evicted"

    trace.record(
        workspace=WORKSPACE, endpoint="messages", streamed=False,
        outcome="passed", detected=0, duration_ms=5.0, exchange_id=predates_id,
    )

    leaf1_text = (
        "Meeting notes: attendee list includes Clara Hoffmann from operations, "
        "discussing the Q3 roadmap timeline and budget allocations for the "
        "upcoming fiscal year review with stakeholders across engineering, "
        "product, and finance departments before the deadline arrives next "
        "week for final sign-off from Pinnacle Corp leadership."
    )
    clara_start = leaf1_text.index("Clara Hoffmann")
    pinnacle_start = leaf1_text.index("Pinnacle Corp")
    # Deliberately overlapping spans (ADR-0059 §3): a bare "Pinnacle Corp" mint
    # and a longer coalesced "Pinnacle Corp Holdings" mint both claim the same
    # leading substring -- the record keeps both, the renderer unions them.
    leaf2_text = "Company: Pinnacle Corp Holdings, confirmed by Devin Novak."
    pinnacle_short_start = leaf2_text.index("Pinnacle Corp")
    pinnacle_long_start = leaf2_text.index("Pinnacle Corp Holdings")

    # Issue #401: a third leaf carrying the exchange's `pending` and `rejected`
    # surrogates side by side with leaf1's `confirmed` ones above -- the
    # Reveal switch's own verification bar ("a real exchange containing all
    # three lifecycles") in ONE exchange, not three separate ones.
    leaf3_text = (
        f"Also cc: {pending_surrogate}, still awaiting triage, and Igor Talvik, "
        "who asked not to be included in this thread."
    )
    pending_start = leaf3_text.index(pending_surrogate)
    rejected_start = leaf3_text.index("Igor Talvik")

    trace.record(
        workspace=WORKSPACE, endpoint="messages", streamed=False,
        outcome="passed", detected=2, duration_ms=118.0, exchange_id=passed_id,
        # A real multi-hop exchange (issue #400's own bar: "verified in a
        # browser against a real multi-hop exchange, not only unit tests") --
        # mirrors the primary fixture's own 2-hop "passed" row (system, user)
        # so the retained-payload section renders alongside genuine hop cards
        # and surrogate chips, not a hopless stub.
        hops=[
            {
                "hop_index": 0,
                "hop_kind": "system",
                "l1_counts": {},
                "l1_duration_ms": 0.1,
                "l2_count": 0,
                "l2_duration_ms": 0.1,
                "l3_confirmed": 0,
                "l3_dismissed": 0,
                "l3_suppressed": 0,
                "l3_provider": None,
                "l3_duration_ms": None,
                "surrogates": [],
            },
            {
                "hop_index": 1,
                "hop_kind": "user",
                "l1_counts": {},
                "l1_duration_ms": 0.2,
                "l2_count": 1,
                "l2_duration_ms": 0.3,
                "l3_confirmed": 1,
                "l3_dismissed": 0,
                "l3_suppressed": 0,
                "l3_provider": "ollama",
                "l3_duration_ms": 30.0,
                "surrogates": ["Clara Hoffmann", "Pinnacle Corp", pending_surrogate, "Igor Talvik"],
            },
        ],
        l3_provider="ollama",
        l3_duration_ms=30.0,
    )
    trace.record(
        workspace=WORKSPACE, endpoint="messages", streamed=False,
        outcome="blocked", detected=0, duration_ms=9.0, exchange_id=blocked_id,
        reason="leak_gate: a mapped entity matched the outbound payload",
    )
    blocked_leaf_text = "Contact Clara Hoffmann to confirm the transfer of Pinnacle Corp assets."
    blocked_span_start = blocked_leaf_text.index("Clara Hoffmann")

    if armed:
        # Gated on `armed`, not recorded unconditionally: the disarmed
        # fixture instance must show EVERY row as "disarmed" (issue #400's own
        # bar for a clean empty-state read), not a mix of disarmed and
        # already-retained rows a real disarm would actually leave behind
        # (disarming never clears what the ring buffer already holds).
        store.retain(
            workspace=WORKSPACE,
            blocked=False,
            exchange_id=passed_id,
            leaves=[
                RewrittenLeaf(
                    leaf_id="leaf-0",
                    label="user: text block",
                    text=leaf1_text,
                    spans=(
                        RewrittenSpan(
                            clara_start, clara_start + len("Clara Hoffmann"),
                            "Clara Hoffmann", "l2",
                        ),
                        RewrittenSpan(
                            pinnacle_start, pinnacle_start + len("Pinnacle Corp"),
                            "Pinnacle Corp", "l3",
                        ),
                    ),
                ),
                RewrittenLeaf(
                    leaf_id="leaf-1",
                    label="tool_result: tool-result body",
                    text=leaf2_text,
                    spans=(
                        RewrittenSpan(
                            pinnacle_short_start, pinnacle_short_start + len("Pinnacle Corp"),
                            "Pinnacle Corp", "l3",
                        ),
                        RewrittenSpan(
                            pinnacle_long_start, pinnacle_long_start + len("Pinnacle Corp Holdings"),
                            "Pinnacle Corp Holdings", "l3",
                        ),
                    ),
                ),
                RewrittenLeaf(
                    leaf_id="leaf-2",
                    label="user: text block",
                    text=leaf3_text,
                    spans=(
                        RewrittenSpan(
                            pending_start, pending_start + len(pending_surrogate),
                            pending_surrogate, "l3",
                        ),
                        RewrittenSpan(
                            rejected_start, rejected_start + len("Igor Talvik"),
                            "Igor Talvik", "l3",
                        ),
                    ),
                ),
            ],
        )
        store.retain(
            workspace=WORKSPACE,
            blocked=True,
            exchange_id=blocked_id,
            leaves=[
                RewrittenLeaf(
                    leaf_id="leaf-0",
                    label="user: text block",
                    text=blocked_leaf_text,
                    spans=(
                        RewrittenSpan(
                            blocked_span_start, blocked_span_start + len("Clara Hoffmann"),
                            "Clara Hoffmann", "l2",
                        ),
                    ),
                ),
            ],
        )

    trace.record(
        workspace=WORKSPACE, endpoint="messages", streamed=False,
        outcome="passed", detected=0, duration_ms=5.0, exchange_id=evicted_id,
    )
    # evicted_id is deliberately never retained.

    return payload_inspection, store, trace


def build_app():
    if IS_EMPTY:
        return _build_empty_app()

    graph = EntityGraph()
    # A display name distinct from the slug (issue #114: switcher shows name + mono
    # slug on two lines, not slug-as-name).
    graph.create_workspace(WORKSPACE, "Acme Corp")
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
    # Structural entity-list edits: merge now runs on `curator` alone (ADR-0016/
    # ADR-0028, issue #314 -- the RBAC-vocabulary gap this comment used to describe
    # is fixed), but rename (app.py::edit_entity_surrogate) is still `admin`-gated
    # and out of that issue's scope. Granted here so the shell's structural-curation
    # specs can exercise the real endpoints end to end.
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
    # "erin" is deliberately NOT granted here: access-shell.spec.ts's "add identity"
    # test (against this same shared fixture) asserts erin starts with zero access
    # rows and grants her first role live. The issue #401 persona of the same name
    # (viewer only, no re-identifier) is granted below, scoped to the two dedicated
    # payload-inspection fixture ports only -- see the PAYLOAD_INSPECTION_RETAINED/
    # PAYLOAD_INSPECTION_DISARMED_ONLY block.

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
            (ORG_SURROGATE, WORKSPACE): CIPHERTEXT_ORG,
            (PERSON2_SURROGATE, WORKSPACE): CIPHERTEXT_PERSON2,
            (ORG2_SURROGATE, WORKSPACE): CIPHERTEXT_ORG2,
            (PERSON3_SURROGATE, WORKSPACE): CIPHERTEXT_PERSON3,
            (ORG3_SURROGATE, WORKSPACE): CIPHERTEXT_ORG3,
            (TRACE_HOP_SURROGATE, WORKSPACE): CIPHERTEXT_TRACE_HOP,
        }
    )
    transit = _stub_transit()

    review_inbox = ReviewInbox()
    # entity_type="person" (issue #364, second finding): review-inbox.spec.ts's
    # "dual-encoded kind shape" spec needs one seeded candidate of each rendered
    # kind side by side (round person / square term, issue #176 hard rule §6.2).
    # This seed predates #346, which changed `_entity_kind_for`'s untyped (`None`)
    # mapping from "person" to the less-committal "term" (a deliberate posture
    # about *real*, adjudicator-minted verdicts -- l3_openai_compat.py/ollama.py
    # never set entity_type, so `None` had to stop implying "confirmed human").
    # Left untyped here, this fixture candidate silently started rendering as
    # "term" too, same as the other seeded candidate -- losing the "person" shape
    # this spec exists to cover. That drift was undiscoverable until issue #364's
    # own fix (this file's `_start_stub_openbao_server`), since #343's hydration
    # bug kept the whole fixture from booting at all. This fixture simulates an
    # already-adjudicated candidate for UI coverage, not #346's untyped-verdict
    # case, so pinning an explicit type here doesn't relitigate that decision.
    review_item_one = review_inbox.upsert(
        REVIEW_ITEM_REAL_ONE, context=REVIEW_ITEM_CONTEXT_ONE, entity_type="person"
    )
    review_inbox.upsert(
        REVIEW_ITEM_REAL_TWO, context=REVIEW_ITEM_CONTEXT_TWO, entity_type="organization"
    )
    allowlist = Allowlist()

    # Processing trace's own surrogate-mapping (issue #154, ADR-0035): the exact
    # same seeded pairs the module-global `_mapping` singleton starts from, plus
    # two additive seeds so the trace's own fixture surrogates (TRACE_HOP_SURROGATE,
    # the email PII surrogate below) classify as reveal-eligible ("confirmed") --
    # never a fresh, unseeded mapping, so rename/merge/confirm specs elsewhere
    # keep seeing the identical default entity set.
    processing_trace_mapping = SurrogateMapping.from_pairs(
        vendored_seed_repository().seeded_pairs()
    )
    processing_trace_mapping.seed(TRACE_HOP_REAL, TRACE_HOP_SURROGATE)
    processing_trace_mapping.seed("ops@bramblewick.example", "notice@bramblewick.invalid")

    # Seeded processing-trace records (ADR-0035, issue #151) for the Processing
    # trace view's outcome-first grid -- one of each of the 3 outcome buckets. The
    # "passed" record also carries per-hop detail (issue #153) so the inline
    # expansion + L3 column have something to render in the fixture. Its
    # surrogates (issue #154) exercise all three reveal lifecycles:
    # TRACE_HOP_SURROGATE and the email surrogate are seeded into
    # processing_trace_mapping above ("confirmed" -> Reveal control);
    # review_item_one's own provisional surrogate is still sitting in
    # review_inbox ("pending" -> Review-inbox deep-link); "Igor Talvik" is
    # recognized by neither store ("rejected" -> no affordance).
    processing_trace = ProcessingTraceBuffer()
    processing_trace.record(
        workspace=WORKSPACE, endpoint="messages", streamed=False,
        outcome="passed", detected=2, duration_ms=118.0,
        hops=[
            {
                "hop_index": 0,
                "hop_kind": "system",
                "l1_counts": {},
                "l1_duration_ms": 0.1,
                "l2_count": 1,
                "l2_duration_ms": 0.4,
                "l3_confirmed": 0,
                "l3_dismissed": 0,
                "l3_suppressed": 0,
                "l3_provider": None,
                "l3_duration_ms": None,
                "surrogates": [TRACE_HOP_SURROGATE, "Igor Talvik"],
            },
            {
                "hop_index": 1,
                "hop_kind": "user",
                "l1_counts": {"email": 1},
                "l1_duration_ms": 0.2,
                "l2_count": 0,
                "l2_duration_ms": 0.1,
                "l3_confirmed": 1,
                "l3_dismissed": 1,
                "l3_suppressed": 2,
                "l3_provider": "ollama",
                "l3_duration_ms": 42.0,
                "surrogates": ["notice@bramblewick.invalid", review_item_one.provisional_surrogate],
            },
        ],
        l3_provider="ollama",
        l3_duration_ms=42.0,
        # Issue #158: upstream (Claude) was fast -- the bulk of this exchange's
        # 118ms was blindfold's own L3 minting, the exact "90% blindfold" shape
        # the issue's own live example reports.
        upstream_duration_ms=15.0,
    )
    processing_trace.record(
        workspace=WORKSPACE, endpoint="messages", streamed=False,
        outcome="blocked", detected=0, duration_ms=9.0,
        reason="leak_gate: a mapped entity matched the outbound payload",
        # Blocked before the exchange ever reached upstream -- no upstream call
        # happened (mirrors the l3_provider=None convention).
        upstream_duration_ms=None,
    )
    processing_trace.record(
        workspace=WORKSPACE, endpoint="chat_completions", streamed=False,
        outcome="upstream_error", detected=1, duration_ms=302.0,
        reason="upstream returned HTTP 500",
        upstream_duration_ms=302.0,
    )

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: relationship_store
    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store
    app.dependency_overrides[get_transit_client] = lambda: transit
    # Issue #430: re-identify now decrypts through whichever mapping cipher is
    # active, not Transit specifically -- mirror the same stub instance onto
    # get_mapping_cipher so Reveal keeps resolving here exactly as before.
    app.dependency_overrides[get_mapping_cipher] = lambda: transit
    app.dependency_overrides[get_review_inbox] = lambda: review_inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    app.dependency_overrides[get_processing_trace] = lambda: processing_trace
    app.dependency_overrides[get_mapping] = lambda: processing_trace_mapping
    app.dependency_overrides[get_upstream_client] = _stub_upstream

    if PAYLOAD_INSPECTION_RETAINED or PAYLOAD_INSPECTION_DISARMED_ONLY:
        # erin holds ONLY viewer on "acme" -- can see the Processing trace (and this
        # exchange's retained payload) but not Reveal it. Distinct from dave (curator,
        # no viewer at all -- refused the retained-leaves endpoint outright) and from
        # every other persona above (all either hold re-identifier or no role at
        # all): the exchange-level bulk Reveal switch (issue #401, ADR-0059 §5) needs
        # an identity that reaches the switch itself -- visible, not hidden -- and is
        # denied only at the point of attempting it. Granted only on these two
        # dedicated fixture ports, never on the shared/default one, so it can't
        # perturb access-shell.spec.ts's own use of "erin" as a fresh, role-less
        # identity against the shared fixture.
        rbac.grant("erin", WORKSPACE, "viewer")
        payload_inspection, rewritten_leaf_store, retained_trace = (
            _build_payload_inspection_retained_fixture(
                armed=PAYLOAD_INSPECTION_RETAINED,
                pending_surrogate=review_item_one.provisional_surrogate,
            )
        )
        app.dependency_overrides[get_payload_inspection] = lambda: payload_inspection
        app.dependency_overrides[get_rewritten_leaf_store] = lambda: rewritten_leaf_store
        app.dependency_overrides[get_processing_trace] = lambda: retained_trace
        # Issue #401: this fixture's own "passed, retained" exchange needs
        # PERSON_SURROGATE/ORG_SURROGATE to classify `confirmed` (the bulk
        # Reveal switch's own verification bar). A fresh mapping scoped to
        # ONLY these two ports -- never mutating the shared
        # `processing_trace_mapping` the primary port's own hop-chip specs
        # (processing-trace.spec.ts) depend on, which never seeds these two
        # tokens at all today.
        retained_mapping = SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())
        retained_mapping.seed(REAL_PERSON, PERSON_SURROGATE)
        retained_mapping.seed(REAL_ORG, ORG_SURROGATE)
        app.dependency_overrides[get_mapping] = lambda: retained_mapping

        # Issue #430 AC6: this port is the browser-verify target for "bulk Reveal
        # on a retained payload succeeds against a proxy running the Local key
        # cipher" -- a real LocalKeyCipher, never the shared Transit stub every
        # other port resolves Reveal through, decrypting real ciphertext for the
        # two surrogates this port's own "passed, retained" exchange marks
        # confirmed (PERSON_SURROGATE, ORG_SURROGATE). TRACE_HOP_SURROGATE's
        # fixed literal ciphertext is untouched and unused here -- this port's
        # own trace/mapping never carries that surrogate.
        retained_cipher = LocalKeyCipher(base64.b64encode(os.urandom(32)).decode())
        reidentify_store.seed(PERSON_SURROGATE, WORKSPACE, retained_cipher.encrypt(REAL_PERSON))
        reidentify_store.seed(ORG_SURROGATE, WORKSPACE, retained_cipher.encrypt(REAL_ORG))
        app.dependency_overrides[get_mapping_cipher] = lambda: retained_cipher

    if LEAK_TAXONOMY:
        # Issue #417 (browser-verify): seed one real block of each cause behind
        # `leak_detected`'s taxonomy split, through the actual `_leak_gate_or_block`
        # funnel -- see LEAK_TAXONOMY's own docstring above for why this can't be
        # reached by a live POST /v1/messages against a healthy fixture, and why a
        # hand-typed scrubbed_reason would risk drifting from the real format.
        leak_taxonomy_block_history = BlockHistory(window_minutes=15)
        app.dependency_overrides[get_block_history] = lambda: leak_taxonomy_block_history
        leak_taxonomy_audit_log = AuditLog()

        class _LeakyMapping(SurrogateMapping):
            """Mirrors tests/test_leak_detected_curation_sub_reason.py's own
            `_LeakyMapping`: a mapping-known real value the blinder should have
            rewritten but didn't -- the defect cause, never a review-inbox row."""

            def real_values(self) -> list[str]:
                return [LEAK_TAXONOMY_DEFECT_REAL]

        _leak_gate_or_block(
            {
                "messages": [
                    {"role": "user", "content": f"Brief {LEAK_TAXONOMY_DEFECT_REAL} now."}
                ]
            },
            _LeakyMapping(),
            WORKSPACE,
            leak_taxonomy_audit_log,
            leak_taxonomy_block_history,
        )
        # A fresh, empty mapping/inbox pair -- not `processing_trace_mapping`/
        # `review_inbox` (this fixture's shared instances): `processing_trace_
        # mapping` already confirms TRACE_HOP_REAL ("Klaus Weber"), and issue
        # #394's bare-word-component check would match this scenario's own
        # review-inbox real value on the shared first name "Klaus" alone --
        # firing the DEFECT cause (a confirmed-entity component match) before
        # the review-inbox items loop is ever reached, masking the curation
        # cause this call exists to seed. Mirrors
        # tests/test_leak_detected_curation_sub_reason.py's own isolated
        # `_mapping()`/`ReviewInbox()` pair for exactly this reason.
        leak_taxonomy_inbox = ReviewInbox()
        leak_taxonomy_inbox.upsert(
            REVIEW_ITEM_REAL_ONE,
            context=REVIEW_ITEM_CONTEXT_ONE,
            entity_type="person",
        )
        _leak_gate_or_block(
            {
                "messages": [
                    {"role": "user", "content": f"Follow up with {REVIEW_ITEM_REAL_ONE}."}
                ]
            },
            SurrogateMapping.from_pairs([]),
            WORKSPACE,
            leak_taxonomy_audit_log,
            leak_taxonomy_block_history,
            inbox=leak_taxonomy_inbox,
        )

    if FORCE_DEPENDENCIES_HEALTHY:
        # See _build_empty_app()'s identical override for why upstream gets its
        # own RecentFailureHealth instance rather than joining `_all_healthy`.
        _all_healthy = lambda: _StaticHealthProbe(DependencyHealth(healthy=True, latency_ms=8.0))
        _upstream_health = RecentFailureHealth()
        app.dependency_overrides[get_upstream_health] = lambda: _upstream_health
        app.dependency_overrides[get_l3_health_probe] = _all_healthy
        app.dependency_overrides[get_transit_health_probe] = _all_healthy
        app.dependency_overrides[get_store_health_probe] = _all_healthy

    _apply_gliner_detection_overrides()
    return app


class _StaticHealthProbe:
    def __init__(self, health: DependencyHealth) -> None:
        self._health = health

    def check(self) -> DependencyHealth:
        return self._health


if __name__ == "__main__":
    build_app()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
