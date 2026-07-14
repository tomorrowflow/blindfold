"""Blocked 503s carry an actionable message + management_url deep link (ADR-0027, issue #91).

A fail-closed or leak-gate block strands the user's prompt mid-exchange. Per ADR-0027,
the block must carry its own call to action rather than leave the client to guess: a
plain-language `message` (most clients, Claude Code included, render API error messages
verbatim -- this is the in-tool delivery channel) and a `management_url` deep link into
the management app's Home/Status page, chosen by `sub_reason`.

This extends the existing `blindfold_blocked` 503 contract (#86 / SEC-7) -- same
body+audit+log funnel (`_blocked_response`), same scrubbed-reason invariant applied to
the new `message` field too (SEC-3): no entity plaintext, ever, on this observability
surface.

Leak-audit clauses: F (fail-closed body shape) is the only clause this slice touches --
it doesn't change blindfold/restore/surrogate-mint mechanics (A-E, G N/A). The new
`message` field must obey the identical scrubbed-reason invariant as the existing body,
asserted directly below (leak-audit-style, per the issue's acceptance criteria).
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_l3_detector,
    get_mapping,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.config import Settings, get_settings
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.policy import WorkspacePolicies
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient, UpstreamError


class _UnavailableAdjudicator:
    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise ConnectionError("ollama unreachable")


def _make_stub_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_l3_unavailable_block_carries_a_management_url_derived_from_settings(
    monkeypatch,
):
    monkeypatch.setenv("BLINDFOLD_HOST", "127.0.0.1")
    monkeypatch.setenv("BLINDFOLD_PORT", "8000")
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "Please brief Quentin."}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    error = resp.json()["error"]
    # Not hardcoded -- reflects the settings-derived loopback host/port (ADR-0021).
    assert error["management_url"] == "http://127.0.0.1:8000/ui/status"


@pytest.mark.anyio
async def test_management_url_reflects_a_non_default_configured_host_and_port(
    monkeypatch,
):
    monkeypatch.setenv("BLINDFOLD_HOST", "0.0.0.0")
    monkeypatch.setenv("BLINDFOLD_PORT", "9000")
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "Please brief Quentin."}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.json()["error"]["management_url"] == "http://0.0.0.0:9000/ui/status"


@pytest.mark.anyio
async def test_l3_unavailable_block_carries_a_human_actionable_message_with_the_deep_link():
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "Please brief Quentin."}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    error = resp.json()["error"]
    # Claude Code and most clients render API error `message` verbatim -- the
    # in-tool delivery channel -- so it must itself carry the call to action, not
    # just the raw scrubbed technical reason.
    assert error["message"].startswith("Blindfold blocked this request:")
    assert error["management_url"] in error["message"]
    # The original scrubbed technical reason is still available (diagnosability),
    # now under its own key -- unchanged from #48/SEC-7's contract.
    assert error["reason"] == (
        "L3 candidate-span adjudication is unavailable and the payload contains "
        "a novel candidate that cannot be scanned: " + str(
            ConnectionError("ollama unreachable")
        )
    ) or "hash:" in error["reason"]


@pytest.mark.anyio
async def test_leak_detected_block_also_carries_message_and_management_url():
    class _LeakyMapping(SurrogateMapping):
        def real_values(self) -> list[str]:
            return ["Quentin"]

    recorded: list[httpx.Request] = []
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only("gamma")
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_mapping] = lambda: _LeakyMapping()
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "Brief Quentin now."}]},
                headers={"x-blindfold-workspace": "gamma"},
            )
    finally:
        app.dependency_overrides.clear()

    error = resp.json()["error"]
    assert error["sub_reason"] == "leak_detected"
    assert error["management_url"].endswith("/ui/status")
    assert error["message"].startswith("Blindfold blocked this request:")
    assert "Quentin" not in error["message"]
    assert "Quentin" not in error["management_url"]


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


def _make_stub_upstream_returning(body: dict, recorded: list[httpx.Request]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_unresolved_surrogate_block_also_carries_message_and_management_url():
    # The buffered path's resolution gate (SEC-6): restore_response only rewrites
    # "text"/"tool_use" content blocks (ADR-0006 scope), so a "thinking" block that
    # echoes an injected surrogate verbatim is left unresolved -- caught here, not
    # a silent pass-through. Same _blocked_response funnel, sub_reason
    # unresolved_surrogate.
    mapping = _seeded_mapping()
    martin = "Martin Bach"
    martin_surrogate = mapping.surrogate_for(martin)
    assert martin_surrogate is not None and martin_surrogate != martin

    recorded: list[httpx.Request] = []
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only("epsilon")
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream_returning(
        {
            "content": [
                {"type": "thinking", "thinking": martin_surrogate},
                {"type": "text", "text": "ok"},
            ]
        },
        recorded,
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": f"Greet {martin} for me."}],
                },
                headers={"x-blindfold-workspace": "epsilon"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    error = resp.json()["error"]
    assert error["sub_reason"] == "unresolved_surrogate"
    assert error["management_url"].endswith("/ui/status")
    assert error["message"].startswith("Blindfold blocked this request:")
    assert error["management_url"] in error["message"]
    # The real value must never appear (leak-audit invariant); the surrogate itself
    # is safe to display (it's what would have egressed) and does appear, naming
    # which surrogate was left unresolved for diagnosability.
    assert martin not in error["message"]
    assert martin_surrogate in error["message"]


@pytest.mark.anyio
async def test_streaming_request_blocked_before_headers_carries_the_same_fields():
    # AC: a block surfaced before headers (the mint pass / leak gate both run before
    # upstream.open_stream) must carry the identical message + management_url shape
    # as the buffered path -- the client never even sees a 200 to begin with.
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "stream": True,
                    "messages": [{"role": "user", "content": "Please brief Quentin."}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/json")
    error = resp.json()["error"]
    assert error["sub_reason"] == "l3_unavailable"
    assert error["management_url"] == "http://127.0.0.1:25463/ui/status"
    assert error["message"].startswith("Blindfold blocked this request:")
    assert recorded == []


@pytest.mark.anyio
async def test_upstream_error_response_is_unaffected_by_the_blocked_shape_change():
    # AC: `blindfold_upstream_error` responses are out of scope and unchanged --
    # they are an availability/contract failure (#86), not a privacy block, and
    # must never grow a `management_url`/ADR-0027 shape of their own.
    class _FailingUpstream:
        async def send_messages(self, blinded, headers):
            raise UpstreamError(status_code=502, sub_reason="upstream_unreachable", message="boom")

    app.dependency_overrides[get_upstream_client] = lambda: _FailingUpstream()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                headers={"x-blindfold-workspace": "zzz-unaffected"},
            )
    finally:
        app.dependency_overrides.clear()

    error = resp.json()["error"]
    assert error["type"] == "blindfold_upstream_error"
    assert "management_url" not in error
    assert "reason" not in error
