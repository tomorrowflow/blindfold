"""FastAPI proxy exposing Anthropic- and OpenAI-compatible endpoints.

Request path (tracer-bullet slice), identical for both endpoints:
  blindfold every hop  ->  forward to upstream  ->  restore the response  ->  verify pass

Streaming path (issue #6): when ``stream: true`` is set, the proxy opens a streaming
request to the upstream and runs the sliding-window restorer over each SSE
``content_block_delta`` text fragment before forwarding it to the client. The tail
buffer ensures a surrogate split across upstream chunks is restored before any byte
of it crosses the client-facing boundary (ADR-0006).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse

from .config import get_settings
from .engine import (
    ExchangeSession,
    StreamingRestorer,
    blindfold_chat_completions_payload,
    blindfold_payload,
    restore_chat_completion,
    restore_response,
    verify_pass,
)
from .store import vendored_seed_repository
from .surrogates import SurrogateMapping
from .upstream import UpstreamClient

app = FastAPI(title="Blindfold")

# Process-wide surrogate mapping built from the entity-graph repository seam (the seeded
# real->surrogate pairs, including variations), NOT a hardcoded dict. Keeping it a
# singleton makes surrogates stable across exchanges within the process (leak-audit
# clause E-stable). The default seam is the in-process vendored seed; tests substitute it
# via dependency_overrides[get_mapping]. Postgres-backed persistence lands via the same
# repository seam (ETL into the graph); this slice keeps the request path hermetic.
_mapping = SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())

# Client auth/version headers forwarded upstream. content-type is intentionally omitted
# so it is not duplicated with the JSON body the upstream client serializes. The union
# covers both providers: Anthropic uses x-api-key + anthropic-* version headers; OpenAI
# uses authorization (Bearer …) + optional openai-organization / openai-project.
_FORWARDED_HEADERS = (
    "x-api-key",
    "authorization",
    "anthropic-version",
    "anthropic-beta",
    "openai-organization",
    "openai-project",
)


def get_mapping() -> SurrogateMapping:
    return _mapping


def get_upstream_client() -> UpstreamClient:
    return UpstreamClient.from_settings(get_settings())


def _forwarded_headers(request: Request) -> dict[str, str]:
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() in _FORWARDED_HEADERS
    }


@app.post("/v1/messages")
async def messages(
    request: Request,
    upstream: UpstreamClient = Depends(get_upstream_client),
    mapping: SurrogateMapping = Depends(get_mapping),
):
    payload = await request.json()

    blinded, session = blindfold_payload(payload, mapping)
    forwarded = _forwarded_headers(request)

    if payload.get("stream"):
        return StreamingResponse(
            _stream_restored(upstream, blinded, forwarded, session),
            media_type="text/event-stream",
        )

    raw_response = await upstream.send_messages(blinded, forwarded)
    restored = restore_response(raw_response, session)
    verify_pass(blinded, restored, session, mapping)
    return restored


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    upstream: UpstreamClient = Depends(get_upstream_client),
    mapping: SurrogateMapping = Depends(get_mapping),
) -> dict:
    payload = await request.json()

    blinded, session = blindfold_chat_completions_payload(payload, mapping)
    raw_response = await upstream.send_chat_completions(
        blinded, _forwarded_headers(request)
    )
    restored = restore_chat_completion(raw_response, session)
    verify_pass(blinded, restored, session, mapping)
    return restored


async def _stream_restored(
    upstream: UpstreamClient,
    blinded: dict,
    forwarded: dict[str, str],
    session: ExchangeSession,
) -> AsyncIterator[bytes]:
    """Stream restored SSE bytes to the client.

    Parses upstream SSE events line-by-line, feeds ``text_delta`` payloads through a
    ``StreamingRestorer`` so a surrogate split across upstream chunks is held back
    until matched, and re-emits restored ``content_block_delta`` events. Non-text
    events pass through unchanged.
    """
    restorer = StreamingRestorer(session)
    buffer = ""
    async with upstream.stream_messages(blinded, forwarded) as response:
        async for raw in response.aiter_bytes():
            buffer += raw.decode("utf-8")
            while "\n\n" in buffer:
                event, buffer = buffer.split("\n\n", 1)
                async for out in _process_sse_event(event, restorer):
                    yield out
        if buffer.strip():
            async for out in _process_sse_event(buffer, restorer):
                yield out
        # Flush any held-back tail at end of stream so nothing buffered is lost.
        tail = restorer.flush()
        if tail:
            yield _emit_text_delta(tail)


async def _process_sse_event(
    event: str, restorer: StreamingRestorer
) -> AsyncIterator[bytes]:
    """Split one SSE event into ``event:`` / ``data:`` lines and rewrite text deltas."""
    event_name, data_line = None, None
    for line in event.split("\n"):
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_line = line[len("data:") :].strip()
    if event_name == "content_block_delta" and data_line:
        try:
            payload = json.loads(data_line)
        except json.JSONDecodeError:
            yield (event + "\n\n").encode("utf-8")
            return
        delta = payload.get("delta", {})
        if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
            restored_text = restorer.feed(delta["text"])
            if restored_text:
                yield _emit_text_delta(restored_text, index=payload.get("index", 0))
            return
    # Non-text-delta event: pass through unchanged.
    yield (event + "\n\n").encode("utf-8")


def _emit_text_delta(text: str, index: int = 0) -> bytes:
    payload = {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text},
    }
    return f"event: content_block_delta\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
