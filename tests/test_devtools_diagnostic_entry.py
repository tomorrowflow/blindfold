"""``build_diagnostic_app`` (ADR-0047 §4/§5/§7, issue #254): the devtools entry
point that serves ``blindfold.app:app`` with capture installed -- refusing to
start against a shared store or on override drift, named, before returning
the wrapped ASGI callable a Diagnostic session actually runs.
"""

import json
import threading

import httpx
import pytest
import uvicorn

from conftest import _free_port, _wait_for_port

from blindfold.app import app, get_l3_detector, get_upstream_client, get_workspace_policies
from blindfold.config import Settings
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.serve import DevModeRequiredError
from blindfold.upstream import UpstreamClient
from blindfold_devtools.diagnostic_entry import (
    MissingCaptureDirectoryError,
    build_diagnostic_app,
    run_diagnostic_server,
)
from blindfold_devtools.override_targets import OverrideDriftError
from blindfold_devtools.settings import DevtoolsSettings
from blindfold_devtools.shared_store_refusal import SharedStoreRefusalError


class _UnavailableAdjudicator:
    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise ConnectionError("ollama unreachable")


class _StubTransitClient:
    """Mirrors ``test_serve_entrypoint.py``'s own stub -- the SEC-2 root-token
    guard's test seam, reused here rather than reimplemented."""

    def __init__(self, *, root: bool) -> None:
        self._root = root

    def is_root_token(self) -> bool:
        return self._root


def _deterministic_only_policies() -> WorkspacePolicies:
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _stub_upstream() -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_1", "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn",
            },
        )

    client = httpx.AsyncClient(base_url="http://upstream.test", transport=httpx.MockTransport(handler))
    return UpstreamClient(base_url="http://upstream.test", client=client)


def test_refuses_to_start_against_a_shared_postgres_dsn(tmp_path):
    settings = Settings(database_url="postgresql://user:pw@db.internal/blindfold")
    devtools_settings = DevtoolsSettings(exchange_capture_dir=str(tmp_path / "captures"))

    with pytest.raises(SharedStoreRefusalError):
        build_diagnostic_app(settings=settings, devtools_settings=devtools_settings)


@pytest.mark.anyio
async def test_returns_a_working_capturing_app_when_unconfigured_for_a_shared_store(tmp_path):
    settings = Settings(database_url="")
    devtools_settings = DevtoolsSettings(exchange_capture_dir=str(tmp_path / "captures"))

    app.dependency_overrides[get_upstream_client] = _stub_upstream
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        wrapped = build_diagnostic_app(settings=settings, devtools_settings=devtools_settings)
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            response = await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "hello"}]},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(list((tmp_path / "captures").glob("*.jsonl"))) == 1


def test_run_diagnostic_server_makes_the_actual_bind_port_visible_to_get_settings(tmp_path):
    # Issue #396 (residual of #388): a Diagnostic session started on a non-default
    # port must report *that* port everywhere get_settings() is later consulted --
    # not BLINDFOLD_PORT/DEFAULT_PORT, which is silent on what the runner was
    # actually told to bind. The default-port case can't distinguish the two
    # sources, so this pins a non-default one -- mirrors
    # test_serve_entrypoint.py's own run_server pin for the same issue.
    from blindfold.config import get_settings

    devtools_settings = DevtoolsSettings(exchange_capture_dir=str(tmp_path / "captures"))
    observed = {}

    def runner(app, **kwargs):
        observed["settings"] = get_settings()

    try:
        run_diagnostic_server(
            host="127.0.0.1",
            port=25464,
            devtools_settings=devtools_settings,
            runner=runner,
        )
    finally:
        app.dependency_overrides.clear()

    assert observed["settings"].host == "127.0.0.1"
    assert observed["settings"].port == 25464


def test_run_diagnostic_server_end_to_end_block_names_the_actual_bound_port_and_stays_scrubbed(
    tmp_path,
):
    # Issue #396 acceptance: a block raised by a Diagnostic session bound to a
    # non-default port must produce a 503 whose management_url names that port
    # (not a dead default-port instance) -- the live-socket counterpart to
    # test_serve_entrypoint.py's own run_server end-to-end pin for #388, exercised
    # here through the devtools entry point #388 never reached. Leak-audit: the
    # block message must stay scrubbed even with a real entity ("Quentin") planted
    # in the exchange -- unchanged shared _blocked_response funnel, asserted
    # directly rather than assumed.
    settings = Settings(database_url="")
    devtools_settings = DevtoolsSettings(exchange_capture_dir=str(tmp_path / "captures"))
    port = _free_port()
    server_holder = {}

    def runner(app_target, *, host, port):
        config = uvicorn.Config(app_target, host=host, port=port, log_level="error")
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None
        server_holder["server"] = server
        server.run()

    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    server_thread = threading.Thread(
        target=run_diagnostic_server,
        kwargs={
            "host": "127.0.0.1",
            "port": port,
            "settings": settings,
            "devtools_settings": devtools_settings,
            "runner": runner,
        },
        daemon=True,
    )
    try:
        server_thread.start()
        _wait_for_port(port)

        status_resp = httpx.get(f"http://127.0.0.1:{port}/v1/status")
        assert status_resp.json()["config"]["port"] == port

        block_resp = httpx.post(
            f"http://127.0.0.1:{port}/v1/messages",
            json={
                "model": "m",
                "messages": [{"role": "user", "content": "Please brief Quentin."}],
            },
        )
        assert block_resp.status_code == 503
        error = block_resp.json()["error"]
        assert error["management_url"] == f"http://127.0.0.1:{port}/ui/status"
        assert "Quentin" not in json.dumps(error)
    finally:
        server_holder["server"].should_exit = True
        server_thread.join(timeout=10)
        app.dependency_overrides.pop(get_l3_detector, None)


def test_run_diagnostic_server_binds_the_same_loopback_default_as_blindfold_serve(tmp_path):
    from blindfold.config import DEFAULT_HOST, DEFAULT_PORT

    settings = Settings(database_url="")
    devtools_settings = DevtoolsSettings(exchange_capture_dir=str(tmp_path / "captures"))
    calls = []

    try:
        run_diagnostic_server(
            settings=settings,
            devtools_settings=devtools_settings,
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )
    finally:
        app.dependency_overrides.clear()

    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs == {"host": DEFAULT_HOST, "port": DEFAULT_PORT}


@pytest.mark.anyio
async def test_run_diagnostic_server_end_to_end_lands_a_capture_file(tmp_path):
    settings = Settings(database_url="")
    devtools_settings = DevtoolsSettings(exchange_capture_dir=str(tmp_path / "captures"))
    calls = []

    app.dependency_overrides[get_upstream_client] = _stub_upstream
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        run_diagnostic_server(
            settings=settings,
            devtools_settings=devtools_settings,
            runner=lambda wrapped_app, **kwargs: calls.append((wrapped_app, kwargs)),
        )
        assert len(calls) == 1
        wrapped_app, _ = calls[0]

        transport = httpx.ASGITransport(app=wrapped_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            response = await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "hello"}]},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(list((tmp_path / "captures").glob("*.jsonl"))) == 1


def test_run_diagnostic_server_reuses_serve_pys_root_transit_token_refusal(tmp_path):
    settings = Settings(openbao_token="dev-root-token", allow_root_transit_token=False)
    devtools_settings = DevtoolsSettings(exchange_capture_dir=str(tmp_path / "captures"))
    calls = []

    with pytest.raises(DevModeRequiredError):
        run_diagnostic_server(
            settings=settings,
            devtools_settings=devtools_settings,
            transit_client=_StubTransitClient(root=True),
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_diagnostic_server_refuses_a_configured_transit_token_before_binding(tmp_path):
    settings = Settings(database_url="", openbao_token="s.some-vault-token")
    devtools_settings = DevtoolsSettings(exchange_capture_dir=str(tmp_path / "captures"))
    calls = []

    with pytest.raises(SharedStoreRefusalError):
        run_diagnostic_server(
            settings=settings,
            devtools_settings=devtools_settings,
            transit_client=_StubTransitClient(root=False),
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_diagnostic_server_refuses_with_no_capture_directory_before_binding():
    settings = Settings(database_url="")
    devtools_settings = DevtoolsSettings(exchange_capture_dir=None)
    calls = []

    with pytest.raises(MissingCaptureDirectoryError):
        run_diagnostic_server(
            settings=settings,
            devtools_settings=devtools_settings,
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_diagnostic_server_refuses_on_override_drift_before_binding(tmp_path, monkeypatch):
    from blindfold import app as blindfold_app_module

    monkeypatch.delattr(blindfold_app_module, "get_upstream_client")
    settings = Settings(database_url="")
    devtools_settings = DevtoolsSettings(exchange_capture_dir=str(tmp_path / "captures"))
    calls = []

    with pytest.raises(OverrideDriftError):
        run_diagnostic_server(
            settings=settings,
            devtools_settings=devtools_settings,
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_diagnostic_server_refuses_a_shared_postgres_dsn_before_binding(tmp_path):
    settings = Settings(database_url="postgresql://user:pw@db.internal/blindfold")
    devtools_settings = DevtoolsSettings(exchange_capture_dir=str(tmp_path / "captures"))
    calls = []

    with pytest.raises(SharedStoreRefusalError):
        run_diagnostic_server(
            settings=settings,
            devtools_settings=devtools_settings,
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []
