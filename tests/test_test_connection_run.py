"""Test connection (issue #265) -- ``run_test_connection``'s trace-assertion and
restore-echo half (the honesty split, Q2), stubbed at the network boundary: an
``httpx.MockTransport`` stands in for "the proxy's listening socket" here so these
stay fast, hermetic unit tests. ``tests/test_test_connection_endpoint.py`` covers the
real-socket property itself (Q3) plus the taxonomy end-to-end through the live app.

Leak-audit clauses: N/A for this file -- these tests exercise only the test-connection
verdict's own trace/mapping bookkeeping; they never construct a real entity, and the
canary is the already-defined non-colliding reserved-shape constant.
"""

import json

import httpx
import pytest

from blindfold.engine import HopDetail
from blindfold.processing_trace import ProcessingTraceBuffer
from blindfold.surrogates import SurrogateMapping
from blindfold.test_connection import (
    CANARY_EMAIL,
    CODE_BLINDFOLDED_OK,
    CODE_BLINDFOLDED_OK_RESTORE_UNPROVEN,
    CODE_LEAK_FLAGGED,
    run_test_connection,
)


def _client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _record_passed_exchange(trace: ProcessingTraceBuffer, workspace: str, surrogate: str) -> None:
    hop = HopDetail(
        hop_index=0,
        hop_kind="user",
        l1_counts={"email": 1},
        l1_duration_ms=0.1,
        l2_count=0,
        l2_duration_ms=0.0,
        l3_confirmed=0,
        l3_dismissed=0,
        l3_suppressed=0,
        l3_provider=None,
        l3_duration_ms=None,
        surrogates=(surrogate,),
    )
    trace.record(
        workspace=workspace,
        endpoint="messages",
        streamed=False,
        outcome="passed",
        detected=1,
        duration_ms=5.0,
        hops=[hop.to_dict()],
    )


@pytest.mark.anyio
async def test_egressed_surrogate_plus_echoed_canary_is_blindfolded_ok():
    mapping = SurrogateMapping()
    surrogate = mapping.mint_pii("email", CANARY_EMAIL)
    trace = ProcessingTraceBuffer()
    _record_passed_exchange(trace, "default", surrogate)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": CANARY_EMAIL}],
                "model": "m",
                "stop_reason": "end_turn",
            },
        )

    verdict = await run_test_connection(
        base_url="http://proxy.test",
        model="m",
        headers={},
        workspace="default",
        mapping=mapping,
        trace=trace,
        client=_client_for(handler),
    )

    assert verdict.code == CODE_BLINDFOLDED_OK


@pytest.mark.anyio
async def test_egressed_surrogate_but_no_echo_is_restore_unproven():
    mapping = SurrogateMapping()
    surrogate = mapping.mint_pii("email", CANARY_EMAIL)
    trace = ProcessingTraceBuffer()
    _record_passed_exchange(trace, "default", surrogate)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "I can't do that."}],
                "model": "m",
                "stop_reason": "end_turn",
            },
        )

    verdict = await run_test_connection(
        base_url="http://proxy.test",
        model="m",
        headers={},
        workspace="default",
        mapping=mapping,
        trace=trace,
        client=_client_for(handler),
    )

    assert verdict.code == CODE_BLINDFOLDED_OK_RESTORE_UNPROVEN


@pytest.mark.anyio
async def test_a_200_with_no_matching_trace_record_is_leak_flagged():
    # A 200 whose exchange trace never shows the canary's surrogate on egress is
    # exactly the scenario this whole feature exists to catch -- Blindfold's own
    # belt-and-suspenders verify pass, independent of what the proxy's internal
    # gates already decided. Must never be a silent pass.
    mapping = SurrogateMapping()
    mapping.mint_pii("email", CANARY_EMAIL)
    trace = ProcessingTraceBuffer()  # no record appended at all

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": CANARY_EMAIL}],
                "model": "m",
                "stop_reason": "end_turn",
            },
        )

    verdict = await run_test_connection(
        base_url="http://proxy.test",
        model="m",
        headers={},
        workspace="default",
        mapping=mapping,
        trace=trace,
        client=_client_for(handler),
    )

    assert verdict.code == CODE_LEAK_FLAGGED


@pytest.mark.anyio
async def test_forwards_the_configured_headers_and_model_on_the_loopback_call():
    mapping = SurrogateMapping()
    surrogate = mapping.mint_pii("email", CANARY_EMAIL)
    trace = ProcessingTraceBuffer()
    _record_passed_exchange(trace, "default", surrogate)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        assert body["model"] == "claude-test-model"
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": CANARY_EMAIL}],
                "model": "claude-test-model",
                "stop_reason": "end_turn",
            },
        )

    await run_test_connection(
        base_url="http://proxy.test",
        model="claude-test-model",
        headers={"x-api-key": "sk-test-token"},
        workspace="default",
        mapping=mapping,
        trace=trace,
        client=_client_for(handler),
    )

    assert len(seen) == 1
    assert seen[0].headers["x-api-key"] == "sk-test-token"
