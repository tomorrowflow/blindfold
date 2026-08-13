"""``build_diagnostic_app`` (ADR-0047 §4/§5/§7, issue #254): the devtools entry
point that serves ``blindfold.app:app`` with capture installed -- refusing to
start against a shared store or on override drift, named, before returning
the wrapped ASGI callable a Diagnostic session actually runs.
"""

import json

import httpx
import pytest

from blindfold.app import app, get_upstream_client, get_workspace_policies
from blindfold.config import Settings
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
