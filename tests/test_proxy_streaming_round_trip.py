"""HTTP proxy seam: streamed-response round trip with sliding-window restore (issue #6).

Drives a streamed request through ``POST /v1/messages`` against a stub upstream that
emits SSE text deltas with the surrogate split across two events — the canonical case
the sliding-window restore (ADR-0006) is designed for.

Leak-audit clauses asserted here:
- A: the upstream saw zero real entity values (the surrogate is what egressed).
- B: the client received fully restored real values across the streamed text deltas.
- D (streaming): no injected surrogate was ever client-visible — not even the
  half-surrogate prefix that split the chunk boundary.

N/A this slice: C (no coincidental lookalike in this fixture), E reserved-namespace,
G mapping secrecy. F fail-closed: no L3 wired here, so (issue #48, SEC-7) each
workspace exercised below explicitly opts into the documented deterministic-only
degrade (ADR-0009) rather than relying on there being no pipeline to fail.
"""

import json

import httpx
import pytest

from blindfold.app import app, get_audit_log, get_upstream_client, get_workspace_policies
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


def _deterministic_only_policies(*workspaces: str) -> WorkspacePolicies:
    # This slice is L1/L2-only (no L3 wired) -- opt the workspace(s) exercised here
    # into deterministic-only mode so the SEC-7 fail-closed-by-default gate (issue
    # #48) doesn't block on incidental capitalized words ("Greet") the deterministic
    # passes already handle correctly.
    policies = WorkspacePolicies()
    for workspace in workspaces:
        policies.opt_in_deterministic_only(workspace)
    return policies


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


def _sse_event(payload: dict) -> bytes:
    return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")


class _AsyncChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def _make_stub_streaming_upstream(chunks: list[bytes], recorded: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200,
            stream=_AsyncChunkStream(chunks),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_streamed_round_trip_restores_surrogate_split_across_two_chunks():
    mapping = _seeded_mapping()
    martin = "Martin Bach"
    martin_surrogate = mapping.surrogate_for(martin)
    assert martin_surrogate is not None and martin_surrogate != martin

    # The surrogate is intentionally split mid-token across two text_delta events.
    head_len = len(martin_surrogate) // 2
    head, tail = martin_surrogate[:head_len], martin_surrogate[head_len:]
    chunks = [
        _sse_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": f"Hello {head}"},
            }
        ),
        _sse_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": f"{tail}, welcome."},
            }
        ),
        _sse_event({"type": "content_block_stop", "index": 0}),
        _sse_event({"type": "message_stop"}),
    ]
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_streaming_upstream(
        chunks, recorded
    )
    app.dependency_overrides[get_workspace_policies] = lambda: _deterministic_only_policies(
        DEFAULT_WORKSPACE
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            received: list[bytes] = []
            async with client.stream(
                "POST",
                "/v1/messages",
                json={
                    "model": "claude-3-5-sonnet",
                    "stream": True,
                    "messages": [
                        {"role": "user", "content": f"Greet {martin} for me."}
                    ],
                },
                headers={"x-api-key": "secret-token"},
            ) as resp:
                assert resp.status_code == 200
                async for chunk in resp.aiter_bytes():
                    received.append(chunk)
    finally:
        app.dependency_overrides.clear()

    # --- Clause A: only surrogate egressed; the real value never crossed the wire. ---
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    assert martin not in egressed
    assert martin_surrogate in egressed
    # Stream flag is forwarded so upstream knows to stream back.
    assert json.loads(egressed)["stream"] is True

    # --- Clause B + D (streaming): real value visible to client, surrogate never is. ---
    full = b"".join(received).decode("utf-8")
    # The surrogate was never emitted intact — not even the half-prefix that split
    # the boundary (would only happen if the sliding window was missing).
    assert martin_surrogate not in full
    assert head not in full  # "Hello {head}" was held back until "{tail}" arrived
    # The real value appears in the restored stream.
    assert martin in full


@pytest.mark.anyio
async def test_streaming_terminal_resolution_check_catches_an_unresolved_surrogate():
    # SEC-6 / issue #47: the streaming path gets the pre-egress leak gate for free, but
    # also needs its own terminal resolution check — the same net the buffered path has
    # (post-restore: no injected surrogate left client-visible). An SSE delta type the
    # restore loop doesn't special-case (e.g. a ``citations_delta``) is passed through
    # verbatim today, so an injected surrogate embedded in it reaches the client
    # unresolved. The terminal check must catch this and audit a block, rather than the
    # exchange completing as if nothing went wrong.
    mapping = _seeded_mapping()
    martin = "Martin Bach"
    martin_surrogate = mapping.surrogate_for(martin)
    assert martin_surrogate is not None and martin_surrogate != martin

    chunks = [
        _sse_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "citations_delta", "text": martin_surrogate},
            }
        ),
        _sse_event({"type": "content_block_stop", "index": 0}),
        _sse_event({"type": "message_stop"}),
    ]
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_streaming_upstream(
        chunks, recorded
    )
    app.dependency_overrides[get_workspace_policies] = lambda: _deterministic_only_policies(
        "delta"
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            try:
                async with client.stream(
                    "POST",
                    "/v1/messages",
                    json={
                        "model": "claude-3-5-sonnet",
                        "stream": True,
                        "messages": [
                            {"role": "user", "content": f"Greet {martin} for me."}
                        ],
                    },
                    headers={
                        "x-api-key": "secret-token",
                        "x-blindfold-workspace": "delta",
                    },
                ) as resp:
                    async for _chunk in resp.aiter_bytes():
                        pass
            except Exception:
                pass
    finally:
        app.dependency_overrides.clear()

    assert any(
        r.workspace == "delta" and r.event == "blocked-unresolved-surrogate"
        for r in audit_log.records
    ), f"expected a blocked-unresolved-surrogate audit record; got: {audit_log.records}"
