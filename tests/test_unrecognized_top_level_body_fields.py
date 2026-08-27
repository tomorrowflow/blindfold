"""ADR-0054 §6 (issue #367): unrecognised top-level body fields pass through.

The gateway protocol reference warns that a gateway which rewrites/redacts
request bodies for content inspection breaks the header/body capability
pairing. Blindfold's blinder rewrites `system`/`messages`/`tools` in place
inside a deep copy of the whole payload and never enumerates the fields it
keeps -- so an unrecognised top-level field (`context_management`,
`output_config`, or a future one) survives byte-identical. The pre-egress
leak gate is the partial safety net: it walks every string leaf of the
outbound payload, including fields the blinder never touched, so a *known*
real value sitting in one of those fields is still a fail-closed block.

Leak-audit clauses: A (a known real value in an unrecognised field is blocked
before egress -- proven directly), F N/A (no L3/fail-closed path exercised;
the app-level test opts into deterministic-only the same way
tests/test_proxy_round_trip.py does). Synthetic field names/values here are
invented stand-ins, never brief/pool entity values.
"""

import copy
import json

import httpx
import pytest

from blindfold.app import app, get_upstream_client, get_workspace_policies
from blindfold.engine import blindfold_chat_completions_payload, blindfold_payload
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


def _deterministic_only_policies() -> WorkspacePolicies:
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _synthetic_unrecognized_fields() -> dict:
    # Invented stand-ins for the two fields ADR-0054 §6 names as evidence
    # (both configuration, not content): nested string leaves that are not
    # entity values.
    return {
        "context_management": {"edits": [{"type": "clear_tool_uses_20250919"}]},
        "output_config": {"format": "structured-v2", "nested": {"mode": "strict"}},
    }


def _make_stub_upstream(recorded: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


def test_blindfold_payload_preserves_unrecognized_top_level_fields_byte_identical():
    mapping = SurrogateMapping()
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        **_synthetic_unrecognized_fields(),
    }
    expected = copy.deepcopy(_synthetic_unrecognized_fields())

    out, _session = blindfold_payload(payload, mapping)

    assert out["context_management"] == expected["context_management"]
    assert out["output_config"] == expected["output_config"]


def test_blindfold_chat_completions_payload_preserves_unrecognized_top_level_fields_byte_identical():
    mapping = SurrogateMapping()
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        **_synthetic_unrecognized_fields(),
    }
    expected = copy.deepcopy(_synthetic_unrecognized_fields())

    out, _session = blindfold_chat_completions_payload(payload, mapping)

    assert out["context_management"] == expected["context_management"]
    assert out["output_config"] == expected["output_config"]


@pytest.mark.anyio
async def test_unrecognized_top_level_fields_reach_stub_upstream_byte_identical():
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hi"}],
                    **_synthetic_unrecognized_fields(),
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    sent = json.loads(recorded[0].content.decode("utf-8"))
    expected = _synthetic_unrecognized_fields()
    assert sent["context_management"] == expected["context_management"]
    assert sent["output_config"] == expected["output_config"]


@pytest.mark.anyio
async def test_known_real_value_in_unrecognized_top_level_field_is_blocked_before_egress():
    mapping = _seeded_mapping()
    martin = "Martin Bach"  # a real seeded entity (issue #3's vendored repository)

    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hi"}],
                    "context_management": {"edits": [{"note": martin}]},
                },
            )
    finally:
        app.dependency_overrides.clear()

    # Fail-closed: the known real value never reached egress, and the exchange
    # never got far enough for anything to be recorded at the stub upstream.
    assert resp.status_code == 503
    assert len(recorded) == 0
