"""Phone reserved-namespace pool exhaustion (issue #312).

``_mint_pii_surrogate``'s phone branch draws from the NANPA fictional
``555-01XX`` range (ADR-0005) -- exactly 100 slots (``index % 100``). The
101st distinct phone value wraps the index back to 0 and would silently
reissue position 0's surrogate to a second, different real value:
``mint_pii``'s collision check (``collides_with_known_entity``) only compares
a candidate against known *real* values, never against surrogates already
*issued* by this mapping, so the wrapped duplicate sails through untouched.

Leak-audit clause E (reserved-namespace, mint-time disjointness): a surrogate,
once issued, must never be handed to a second referent -- that's a de-facto
collision (the injected table is keyed by surrogate; the second referent's
real value wins and the first restores wrong -- ADR-0048 unreproducible-miss
territory). Since the NANPA reserved range genuinely has only 100 fictional
numbers, there is no headroom to "extend the pool" here without emitting a
non-reserved (real-routable-risk) surrogate -- so exhaustion must fail
closed, never loop forever and never silently reuse.
"""

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_declared_tool_vocabulary,
    get_mapping,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.engine import DeclaredToolVocabulary
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.surrogates import MintPoolExhaustedError, SurrogateMapping
from blindfold.upstream import UpstreamClient


def test_phone_pool_exhaustion_fails_closed_never_reissues_a_surrogate():
    mapping = SurrogateMapping()
    reals = [f"+1-202-555-{i:04d}" for i in range(100)]
    surrogates = [mapping.mint_pii("phone", real) for real in reals]

    # All 100 reserved-namespace slots consumed, each one distinct.
    assert len(set(surrogates)) == 100

    # The 101st distinct phone value must not silently receive position 0's
    # surrogate ("+1-555-0100") back -- the pool is exhausted, so mint fails
    # closed instead of looping forever or reusing an already-issued surrogate.
    with pytest.raises(MintPoolExhaustedError):
        mapping.mint_pii("phone", "+1-202-555-9999")


def _deterministic_only_policies() -> WorkspacePolicies:
    # No L3 wired in this module -- opt the default workspace into the
    # documented deterministic-only degrade (ADR-0009) so the L1-only phone
    # mint under test isn't entangled with SEC-7's separate L3-unavailable
    # fail-closed path.
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _make_stub_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_phone_pool_exhaustion_blocks_the_request_fail_closed_and_scrubbed():
    """HTTP proxy seam (issue #312): a request that would mint a 101st distinct
    phone value against an already-exhausted mapping must block, not 500 or
    silently reuse a surrogate. Mirrors the L3-unavailable fail-closed shape
    (test_proxy_fail_closed.py): a stable ``blindfold_fail_closed`` code, a
    distinct ``sub_reason``, and -- SEC-3/SEC-7 -- the real phone value never
    appears anywhere in the 503 body, and the stub upstream never sees the
    request at all (leak-audit clause A: zero egress on the blocked path).
    """
    mapping = SurrogateMapping()
    for i in range(100):
        mapping.mint_pii("phone", f"+1-202-555-{i:04d}")

    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    app.dependency_overrides[get_declared_tool_vocabulary] = DeclaredToolVocabulary
    colliding_real_phone = "+1-202-555-9999"
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": f"Call {colliding_real_phone} today."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "blindfold_fail_closed"
    assert body["error"]["sub_reason"] == "mint_pool_exhausted"
    assert colliding_real_phone not in json.dumps(body)
    # Clause A: blocked before any egress -- the stub upstream saw nothing.
    assert recorded == []
    assert any(record.event == "blocked-mint-pool-exhausted" for record in audit_log.records)
    assert not any(
        colliding_real_phone in record.reason for record in audit_log.records if record.reason
    )
