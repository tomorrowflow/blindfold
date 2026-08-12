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
from blindfold.upstream import UpstreamClient
from blindfold_devtools.diagnostic_entry import build_diagnostic_app
from blindfold_devtools.override_targets import OverrideDriftError
from blindfold_devtools.settings import DevtoolsSettings
from blindfold_devtools.shared_store_refusal import SharedStoreRefusalError


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
