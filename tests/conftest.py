import os
import socket
import sys
import threading
import time
from dataclasses import dataclass, field

import httpx
import pytest
import uvicorn

# Pin the whole suite to the explicit in-memory sentinel by default (ADR-0043 §1,
# issue #204), set at conftest import time -- before pytest collection imports any
# test module's `from blindfold.app import app`, which runs app.py's module-level
# bootstrap (get_rbac()/get_reidentify_store()) against whatever BLINDFOLD_DATABASE_URL
# resolves to right then. A per-test fixture would be too late for that one-time
# import-time read. An unset BLINDFOLD_DATABASE_URL no longer means ephemeral
# in-memory -- it now resolves to a durable SQLite store at a computed default path
# under the real OS Store directory (blindfold.config.resolve_store_dir()). Almost
# none of the suite sets this var (it relied on the old accidental unset default),
# so without this, nearly every test that touches a store-backed endpoint would
# create/reuse a real SQLite file on the machine running the tests -- cross-test
# pollution, not hermetic isolation. A test that cares about the store-selection
# contract itself (this issue's own tests) overrides the var with its own
# monkeypatch.setenv/delenv call, which -- being scoped to that test -- reverts to
# this default at teardown.
os.environ["BLINDFOLD_DATABASE_URL"] = "memory://"


@pytest.fixture
def anyio_backend():
    # Run anyio-marked ASGI tests on asyncio only (no trio dependency).
    return "asyncio"


@pytest.fixture
def block_import(monkeypatch):
    """Make ``import <name>`` raise ``ImportError`` for the rest of the test,
    regardless of whether the package is actually installed in this environment
    (issue #284) -- so a test asserting "this optional dependency is absent"
    behavior doesn't depend on ambient venv state. Shared here rather than
    re-improvised per test file: setting ``sys.modules[name] = None`` is the
    documented way to force the next ``import name`` to fail closed, and
    ``monkeypatch.setitem`` reverts it (to whatever was there before, present or
    absent) at teardown.
    """

    def _block(name: str) -> None:
        monkeypatch.setitem(sys.modules, name, None)

    return _block


def _docker_available() -> bool:
    """Single source for the Docker-gate check (issue #318) -- was copy-pasted
    verbatim into 9 ``tests/test_postgres_*.py`` / ``test_entity_graph_postgres.py`` /
    ``test_transit_ciphertext_columns.py`` / ``test_bootstrap_wiring.py`` files.
    Those files now do ``from conftest import _docker_available``.
    """
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _dependency_overrides_snapshot():
    """Snapshot ``app.dependency_overrides`` before each test and restore it after,
    regardless of what the test did to it (issue #318).

    Before this fixture, every test that set an override had to pair it with a
    ``try: ... finally: app.dependency_overrides.clear()`` -- ~290 of them across the
    suite -- because ``dependency_overrides`` is a single dict on the process-wide
    ``app`` singleton, shared by every test. A test no longer needs its own
    ``.clear()`` call: whatever it adds, changes, or removes is undone here at
    teardown, restoring exactly the overrides that were in place when the test
    started (usually none). This is what makes a shared autouse fixture like
    ``wired_app`` below safe to add incrementally, file by file, without a
    stray blanket ``.clear()`` elsewhere wiping it mid-test.
    """
    from blindfold.app import app

    original = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original)


@dataclass
class WiredApp:
    """The standard stub graph a ``wired_app``-consuming test gets for free.

    Fresh, in-memory instances of the five seams the leak-audit discipline names
    (RBAC, upstream client, mapping, entity graph, audit log) -- isolated from every
    other test and from the process-wide real singletons. ``upstream_requests``
    records every request the stub upstream received, so a test can assert on
    what actually crossed egress (the leak-audit property) without hand-rolling its
    own recording ``httpx.MockTransport`` handler. A test needing a different stub
    for one of these five, or an additional override (``get_l3_detector``,
    ``get_review_inbox``, ...), sets ``app.dependency_overrides[...]`` itself --
    the snapshot/restore fixture above cleans it up regardless.
    """

    rbac: "RbacRegistry"
    upstream_client: "UpstreamClient"
    mapping: "SurrogateMapping"
    entity_graph: "EntityGraph"
    audit_log: "AuditLog"
    upstream_requests: list = field(default_factory=list)


@pytest.fixture
def wired_app() -> WiredApp:
    """Wire ``blindfold.app.app`` with the standard stub graph (issue #318).

    Overrides ``get_rbac`` / ``get_upstream_client`` / ``get_mapping`` /
    ``get_entity_graph`` / ``get_audit_log`` with fresh stub instances, returned as a
    :class:`WiredApp` so a test can reach into them (grant a role, seed a mapping
    pair, inspect ``upstream_requests``) without re-importing the getters itself.
    Restoration is handled by the autouse ``_dependency_overrides_snapshot`` fixture
    above, not by this fixture -- no ``.clear()`` call needed here or in the test.
    """
    from blindfold.app import (
        app,
        get_audit_log,
        get_entity_graph,
        get_mapping,
        get_rbac,
        get_upstream_client,
    )
    from blindfold.entity_graph import EntityGraph
    from blindfold.policy import AuditLog
    from blindfold.rbac import RbacRegistry
    from blindfold.surrogates import SurrogateMapping
    from blindfold.upstream import UpstreamClient

    upstream_requests: list[httpx.Request] = []

    def _stub_upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    upstream_client = UpstreamClient(
        base_url="http://upstream.test",
        client=httpx.AsyncClient(
            base_url="http://upstream.test",
            transport=httpx.MockTransport(_stub_upstream_handler),
        ),
    )

    stubs = WiredApp(
        rbac=RbacRegistry(),
        upstream_client=upstream_client,
        mapping=SurrogateMapping(),
        entity_graph=EntityGraph(),
        audit_log=AuditLog(),
        upstream_requests=upstream_requests,
    )

    app.dependency_overrides[get_rbac] = lambda: stubs.rbac
    app.dependency_overrides[get_upstream_client] = lambda: stubs.upstream_client
    app.dependency_overrides[get_mapping] = lambda: stubs.mapping
    app.dependency_overrides[get_entity_graph] = lambda: stubs.entity_graph
    app.dependency_overrides[get_audit_log] = lambda: stubs.audit_log

    return stubs


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.02)
    raise TimeoutError(f"nothing listening on 127.0.0.1:{port} after {timeout}s")


@pytest.fixture
def live_proxy_server():
    """Serve ``blindfold.app.app`` on a real loopback TCP socket (issue #265, Q3).

    Test-connection's own defining property is that its exchange crosses a genuine
    socket -- "never an internal function call, because the point is to prove the
    socket a client would hit" -- so its test suite needs a real listener too. The
    ``httpx.ASGITransport`` pattern used everywhere else in this suite (e.g.
    ``test_provisional_leak_gate_request_path.py``) deliberately does *not* open a
    socket, so it can't stand in here.

    Runs uvicorn in a background thread of this same process -- shares the exact
    ``app`` singleton/module state (``app.dependency_overrides``, the in-process
    ``_mapping``/processing-trace buffer) the rest of the suite already wires via
    ``wired_app``, so a test can set overrides before requesting this fixture and
    the live server honors them like any other ASGI call would.
    """
    from blindfold.app import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    # A background thread can't install process signal handlers (uvicorn's default
    # behavior raises ValueError there); this fixture owns shutdown via should_exit.
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_for_port(port)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
