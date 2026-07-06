"""HTTP proxy seam: tool-call JSON round trip (issue #11).

Drives a request through ``POST /v1/messages`` against a STUB UPSTREAM injected at
the network boundary (httpx MockTransport). Covers the make-or-break ADR-0006
property for tool-call JSON:

- the assistant's ``tool_use.input`` is treated as a hop too — real entities inside
  its structured args are blindfolded before egress (every-hop, ADR-0002);
- on the way back, surrogates the provider injected inside ``tool_use.input`` string
  values are **restored** with JSON escaping preserved (the dict is walked, so the
  ASGI serializer handles escaping correctly);
- streaming ``input_json_delta`` fragments are **reassembled** before restoring inside
  the reconstructed JSON (sliding-window restore over text doesn't apply once a
  surrogate may straddle a partial-JSON chunk boundary).

Leak-audit clauses asserted here:
- A: zero real entity values egressed — *including* inside tool_use.input JSON.
- B: client received fully restored real values inside tool_use.input.
- D: verify pass clean (no real value in egress, no surrogate left in client output).

N/A this slice: C closed-world (single-exchange focus; covered elsewhere), E reserved-
namespace / coherent world (no PII here), G mapping secrecy (plaintext mapping this
slice; #10). F fail-closed: no L3 wired here, so (issue #48, SEC-7) the default
workspace explicitly opts into the documented deterministic-only degrade (ADR-0009)
rather than relying on there being no pipeline to fail.
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import app, get_upstream_client, get_workspace_policies
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


def _deterministic_only_policies() -> WorkspacePolicies:
    # This slice is L1/L2-only (no L3 wired) -- opt the default workspace into
    # deterministic-only mode so the SEC-7 fail-closed-by-default gate (issue #48)
    # doesn't block on incidental capitalized words ("Email", "Show") the
    # deterministic passes already handle correctly.
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


def _make_stub_upstream(scripted_response, recorded):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


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
async def test_tool_use_input_in_response_is_restored_with_escaping_preserved():
    """A surrogate inside a structured-arg string value round-trips to the real value."""
    mapping = _seeded_mapping()
    martin = "Martin Bach"
    martin_surrogate = mapping.surrogate_for(martin)
    assert martin_surrogate is not None

    # The provider emits a tool_use block — its `input` is structured JSON that
    # contains the surrogate inside a string value. Restore must walk into it.
    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "send_message",
                "input": {
                    "recipient": martin_surrogate,
                    "body": f"Please follow up with {martin_surrogate}.",
                },
            }
        ],
        "stop_reason": "tool_use",
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-3-5-sonnet",
                    "messages": [
                        {"role": "user", "content": f"Email {martin} the report."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200

    # --- Clause A: real value did not egress. ---
    egressed = recorded[0].content.decode("utf-8")
    assert martin not in egressed
    assert martin_surrogate in egressed

    # --- Clause B: tool_use.input was restored, escaping preserved by JSON round trip. ---
    body = resp.json()
    tool_use = body["content"][0]
    assert tool_use["type"] == "tool_use"
    assert tool_use["input"]["recipient"] == martin
    assert tool_use["input"]["body"] == f"Please follow up with {martin}."
    # The surrogate is gone from every string in the input.
    assert martin_surrogate not in json.dumps(tool_use["input"])


@pytest.mark.anyio
async def test_tool_use_input_in_assistant_turn_is_blindfolded_outbound():
    """A real entity inside an assistant-turn ``tool_use.input`` does not egress.

    A client can echo prior assistant tool_use blocks back to the provider in a
    multi-turn conversation. Treating tool_use.input as a hop (ADR-0002) ensures any
    real value that slipped in there is blindfolded before crossing the egress
    boundary — clause A across every hop, not just text.
    """
    mapping = _seeded_mapping()
    martin = "Martin Bach"
    martin_surrogate = mapping.surrogate_for(martin)

    scripted_response = {
        "content": [{"type": "text", "text": "ok"}],
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
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
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_2",
                                    "name": "send_message",
                                    "input": {
                                        "recipient": martin,
                                        "body": f"Hi from {martin}.",
                                    },
                                }
                            ],
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    egressed = recorded[0].content.decode("utf-8")
    # Clause A: the real value never crossed the wire, inside tool_use.input either.
    assert martin not in egressed
    assert martin_surrogate in egressed
    # Structural sanity: the JSON tool_use block survived the rewrite.
    sent = json.loads(egressed)
    sent_input = sent["messages"][0]["content"][0]["input"]
    assert sent_input["recipient"] == martin_surrogate
    assert sent_input["body"] == f"Hi from {martin_surrogate}."


@pytest.mark.anyio
async def test_streamed_tool_use_json_is_reassembled_then_restored():
    """``input_json_delta`` fragments are reassembled before restore (ADR-0006).

    The provider emits a tool_use block whose JSON is split across several
    ``input_json_delta`` events — with the surrogate straddling a chunk boundary so
    a naive char-level restore over the partial_json string would miss it. The proxy
    must reassemble the full JSON, restore inside its string values (escaping
    preserved by re-encoding), and emit the restored payload to the client.

    Leak-audit clauses: A (surrogate is what egressed), B + D (client sees the real
    value across the streamed tool_use, no surrogate left visible).
    """
    mapping = _seeded_mapping()
    martin = "Martin Bach"
    martin_surrogate = mapping.surrogate_for(martin)
    assert martin_surrogate is not None and martin_surrogate != martin

    # Split partial_json so the surrogate straddles a chunk boundary — the case
    # closed-world JSON reassembly is for. (A sliding-window string restore over
    # partial_json fragments would still leave each half visible to the client.)
    head_len = len(martin_surrogate) // 2
    head, tail = martin_surrogate[:head_len], martin_surrogate[head_len:]
    chunks = [
        _sse_event(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "send_message",
                    "input": {},
                },
            }
        ),
        _sse_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"recipient": "' + head,
                },
            }
        ),
        _sse_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": tail + '"}',
                },
            }
        ),
        _sse_event({"type": "content_block_stop", "index": 0}),
        _sse_event({"type": "message_stop"}),
    ]

    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_streaming_upstream(
        chunks, recorded
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
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
                        {"role": "user", "content": f"Email {martin} the report."}
                    ],
                },
            ) as resp:
                assert resp.status_code == 200
                async for chunk in resp.aiter_bytes():
                    received.append(chunk)
    finally:
        app.dependency_overrides.clear()

    egressed = recorded[0].content.decode("utf-8")
    # Clause A: only surrogate egressed.
    assert martin not in egressed
    assert martin_surrogate in egressed

    full = b"".join(received).decode("utf-8")
    # Clause B + D: real value restored, no surrogate (or half-surrogate) visible.
    assert martin in full
    assert martin_surrogate not in full
    assert head not in full
    assert tail not in full
    # Reassembled JSON content must round-trip cleanly to a dict (escaping preserved).
    reassembled = _reassemble_tool_use_input(full)
    assert reassembled == {"recipient": martin}


@pytest.mark.anyio
async def test_tool_use_input_round_trip_preserves_json_escape_sequences():
    """A code-context arg with JSON-escaped chars round-trips intact (ADR-0006).

    The provider emits a tool_use whose ``input`` carries a code string with embedded
    quotes, backslashes, and a real surrogate at the same time. After restore the
    client must see the real value AND the original escape sequences — proves the
    "preserve escaping" half of the acceptance criterion (clause B).
    """
    mapping = _seeded_mapping()
    martin = "Martin Bach"
    martin_surrogate = mapping.surrogate_for(martin)

    # Source code with quotes, backslash, newline — every char that JSON must escape.
    code = (
        'def greet(name=' + repr(martin_surrogate) + '):\n'
        '    print("hi, \\"" + name + "\\"")\n'
    )
    scripted_response = {
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_3",
                "name": "run_code",
                "input": {"language": "python", "source": code},
            }
        ],
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
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
                    "messages": [
                        {"role": "user", "content": f"Show code for {martin}."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # The restored source contains the real name AND the original escape sequences.
    restored_source = resp.json()["content"][0]["input"]["source"]
    assert martin in restored_source
    assert martin_surrogate not in restored_source
    # Escapes survive byte-for-byte: the literal backslash-quote pair the provider
    # emitted is still in the client output (no double-escaping, no de-escaping).
    assert '\\"' in restored_source
    # And the JSON itself is still valid (parses cleanly) when re-encoded.
    assert json.loads(json.dumps(resp.json()["content"][0]["input"]))


def test_surrogates_used_in_structured_args_look_like_safe_identifiers():
    """Every seeded surrogate is JSON-safe and identifier-shaped.

    ADR-0006 + issue #11: surrogates injected into code/tool args must be plain
    printable characters so the provider's JSON parser and downstream code-analysis
    tools don't break. No quote, no backslash, no control characters — the chars
    that would force JSON to introduce an escape the surrounding code didn't have.
    """
    mapping = _seeded_mapping()
    # Person surrogates (entity-graph + L1 PII reserved-namespace) — every surrogate
    # this mapping can issue across the request path.
    pii_kinds = ("email", "phone", "iban", "id")
    sample_surrogates = list({pair[1] for pair in
                              # via from_pairs the seam already exposes them
                              [(p, mapping.surrogate_for(p)) for p in mapping.real_values()]})
    # Mint one of each PII kind so the assertion also covers reserved-namespace shape.
    sample_surrogates.extend(mapping.mint_pii(kind, f"probe-{kind}") for kind in pii_kinds)

    for surrogate in sample_surrogates:
        assert surrogate is not None
        # JSON-unsafe characters would force an escape and could desync streaming reassembly.
        assert '"' not in surrogate
        assert "\\" not in surrogate
        for char in surrogate:
            assert char.isprintable() and ord(char) >= 0x20, (
                f"surrogate {surrogate!r} contains non-printable char {char!r}"
            )


def _reassemble_tool_use_input(sse_bytes: str) -> dict:
    """Concatenate every ``input_json_delta.partial_json`` for content_block index 0."""
    parts: list[str] = []
    for raw_event in sse_bytes.split("\n\n"):
        if not raw_event.strip():
            continue
        data_line = next(
            (
                line[len("data:") :].strip()
                for line in raw_event.split("\n")
                if line.startswith("data:")
            ),
            None,
        )
        if not data_line:
            continue
        try:
            payload = json.loads(data_line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "content_block_delta":
            continue
        delta = payload.get("delta", {})
        if delta.get("type") == "input_json_delta":
            parts.append(delta.get("partial_json", ""))
    return json.loads("".join(parts))
