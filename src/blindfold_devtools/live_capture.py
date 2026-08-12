"""Live-session capture (ADR-0047 §4, issue #254).

Composes an **Exchange capture** (issue #253) around a real exchange through
``blindfold.app:app`` on two seams only, per the ADR -- no new hook is added
to the request path:

- ``app.dependency_overrides`` for ``get_upstream_client`` / ``get_mapping`` /
  ``get_l3_detector`` -- the test suite's own established substitution
  mechanism (:func:`check_override_targets` resolves and shape-checks these
  three at install time, failing loudly on drift).
- A plain ASGI callable wrapping ``blindfold.app:app`` for the client side
  (the real inbound payload, the restored response) -- deliberately *not*
  registered via Starlette's ``app.add_middleware`` (which refuses once the
  app's own middleware stack has already been built by an earlier request
  elsewhere in the process; a devtools entry point must compose regardless
  of what else already touched the shared ``app`` singleton).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request

from blindfold import app as blindfold_app
from blindfold.policy import DEFAULT_WORKSPACE
from blindfold.processing_trace import (
    OUTCOME_BLOCKED,
    OUTCOME_PASSED,
    OUTCOME_UPSTREAM_ERROR,
)
from blindfold.upstream import UpstreamClient

from .capture import (
    SECTION_OBSERVED,
    CaptureWriter,
    FooterRecord,
    HeaderRecord,
    OutboundRecord,
    ProviderChunkRecord,
    RestoredChunkRecord,
)
from .capture_directory import CaptureDirectory
from .override_targets import check_override_targets

# issue #254 wraps `get_upstream_client` only (the ADR's own named target
# list) -- not `get_openai_upstream_client`, which `/v1/chat/completions`
# uses instead. Capturing that path too would silently omit the outbound/
# provider-chunk sides, which ADR-0047 §4 explicitly rules worse than no
# capture at all. Restricted to the one endpoint the wrapped seam actually
# covers; widening to chat_completions is a follow-up, not this slice.
_CAPTURABLE_PATHS = {"/v1/messages": "messages"}

_WORKSPACE_HEADER = b"x-blindfold-workspace"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _CaptureContext:
    """Per-request capture state, shared (via ``request.state.capture``) between
    the ASGI middleware and the three wrapped dependency providers."""

    writer: CaptureWriter
    capture_id: str
    injected: dict[str, str] = field(default_factory=dict)
    upstream_duration_ms: float | None = None


class _CapturingMapping:
    """Delegates to the real mapping; records every ``seed()``/``mint_pii()``
    call made during this request -- the "mint" half of ADR-0047 §4's
    "recording wrapper yielding the authoritative mint/lookup list".
    """

    def __init__(self, inner, ctx: _CaptureContext) -> None:
        self._inner = inner
        self._ctx = ctx

    def seed(self, real, surrogate):
        result = self._inner.seed(real, surrogate)
        self._ctx.injected[surrogate] = real
        return result

    def mint_pii(self, kind, value):
        surrogate = self._inner.mint_pii(kind, value)
        self._ctx.injected[surrogate] = value
        return surrogate

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _TeeingProviderResponse:
    """Duck-types the two methods ``_stream_restored`` actually calls on the
    ``httpx.Response`` :meth:`UpstreamClient.open_stream` returns, teeing every
    chunk to the capture as it is consumed."""

    def __init__(self, inner: httpx.Response, ctx: _CaptureContext) -> None:
        self._inner = inner
        self._ctx = ctx

    async def aiter_bytes(self):
        sequence = 0
        async for chunk in self._inner.aiter_bytes():
            self._ctx.writer.write(
                ProviderChunkRecord(
                    section=SECTION_OBSERVED,
                    ts=_now_iso(),
                    sequence=sequence,
                    chunk=chunk.decode("utf-8", errors="replace"),
                )
            )
            sequence += 1
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class _CapturingUpstreamClient:
    """Tees the blindfolded outbound payload and the provider response --
    *not* via httpx ``event_hooks`` (they fire on response start and cannot
    see a streamed body), but by wrapping the calls directly, per ADR-0047 §4.
    """

    def __init__(self, inner: UpstreamClient, ctx: _CaptureContext) -> None:
        self._inner = inner
        self._ctx = ctx

    @property
    def base_url(self) -> str:
        return self._inner.base_url

    def _record_outbound(self, payload: dict) -> None:
        self._ctx.writer.write(
            OutboundRecord(section=SECTION_OBSERVED, ts=_now_iso(), payload=payload)
        )

    async def _send_buffered(self, inner_send, payload, headers):
        """Tee a non-streaming send: record the blindfolded outbound, time the
        upstream call, and record the whole buffered response as a single
        provider chunk. ``send_messages``/``send_chat_completions`` differ only
        in which inner method they forward to."""
        self._record_outbound(payload)
        start = time.monotonic()
        response = await inner_send(payload, headers)
        self._ctx.upstream_duration_ms = (time.monotonic() - start) * 1000
        self._ctx.writer.write(
            ProviderChunkRecord(
                section=SECTION_OBSERVED, ts=_now_iso(), sequence=0,
                chunk=json.dumps(response),
            )
        )
        return response

    async def send_messages(self, payload, headers):
        return await self._send_buffered(self._inner.send_messages, payload, headers)

    async def send_chat_completions(self, payload, headers):
        return await self._send_buffered(self._inner.send_chat_completions, payload, headers)

    async def open_stream(self, payload, headers):
        self._record_outbound(payload)
        start = time.monotonic()
        response = await self._inner.open_stream(payload, headers)
        self._ctx.upstream_duration_ms = (time.monotonic() - start) * 1000
        return _TeeingProviderResponse(response, self._ctx)


def _wrap_upstream_provider(existing):
    def _provider(request: Request):
        inner = existing()
        ctx = getattr(request.state, "capture", None)
        if ctx is None:
            return inner
        return _CapturingUpstreamClient(inner, ctx)

    return _provider


def _wrap_mapping_provider(existing):
    def _provider(request: Request):
        inner = existing()
        ctx = getattr(request.state, "capture", None)
        if ctx is None:
            return inner
        return _CapturingMapping(inner, ctx)

    return _provider


def _wrap_l3_detector_provider(existing):
    def _provider(request: Request):
        # No live-capture behavior yet beyond composing cleanly with drift
        # detection (ADR-0047 §4 names this as one of the three override
        # targets); verdict capture is deferred -- see handoff notes.
        return existing()

    return _provider


def install_capture(app: FastAPI, directory: CaptureDirectory):
    """Install the three dependency overrides (wrapping whatever is *currently*
    the effective provider -- production or a test's own stub, composably)
    and return a plain ASGI callable wrapping ``app`` for the client side.

    Deliberately returns a new callable rather than mutating ``app``'s own
    middleware stack (``app.add_middleware`` refuses once that stack has
    already been built by an earlier request elsewhere in the process).
    """
    check_override_targets(blindfold_app)

    existing_upstream = app.dependency_overrides.get(
        blindfold_app.get_upstream_client, blindfold_app.get_upstream_client
    )
    existing_mapping = app.dependency_overrides.get(
        blindfold_app.get_mapping, blindfold_app.get_mapping
    )
    existing_l3_detector = app.dependency_overrides.get(
        blindfold_app.get_l3_detector, blindfold_app.get_l3_detector
    )
    app.dependency_overrides[blindfold_app.get_upstream_client] = _wrap_upstream_provider(
        existing_upstream
    )
    app.dependency_overrides[blindfold_app.get_mapping] = _wrap_mapping_provider(
        existing_mapping
    )
    app.dependency_overrides[blindfold_app.get_l3_detector] = _wrap_l3_detector_provider(
        existing_l3_detector
    )
    return CaptureMiddleware(app, directory)


def _workspace_from_scope(scope) -> str:
    for key, value in scope.get("headers") or ():
        if key == _WORKSPACE_HEADER:
            return value.decode("utf-8")
    return DEFAULT_WORKSPACE


class CaptureMiddleware:
    """Plain ASGI callable wrapping ``blindfold.app:app`` for the client side
    (ADR-0047 §4): the real inbound payload, and the restored response --
    teed as its bytes are actually sent, not via ``BaseHTTPMiddleware`` (which
    buffers a streamed response wholesale before handing it back).
    """

    def __init__(self, app, directory: CaptureDirectory) -> None:
        self._app = app
        self._directory = directory

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in _CAPTURABLE_PATHS:
            await self._app(scope, receive, send)
            return

        endpoint = _CAPTURABLE_PATHS[scope["path"]]

        messages = []
        more_body = True
        body_chunks = []
        while more_body:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
                more_body = message.get("more_body", False)
            else:
                more_body = False

        raw_body = b"".join(body_chunks)
        try:
            inbound_payload = json.loads(raw_body)
        except json.JSONDecodeError:
            await self._replay(scope, messages, receive, send)
            return

        streamed = bool(inbound_payload.get("stream"))
        workspace = _workspace_from_scope(scope)
        capture_id, writer = self._directory.start_capture()
        ctx = _CaptureContext(writer=writer, capture_id=capture_id)
        writer.write(
            HeaderRecord(
                section=SECTION_OBSERVED,
                ts=_now_iso(),
                capture_id=capture_id,
                endpoint=endpoint,
                streamed=streamed,
                workspace=workspace,
                inbound_payload=inbound_payload,
            )
        )

        scope.setdefault("state", {})["capture"] = ctx

        start = time.monotonic()
        status_holder: dict[str, int] = {}
        response_chunks: list[bytes] = []
        sequence = 0

        async def send_and_capture(message):
            nonlocal sequence
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    response_chunks.append(body)
                    writer.write(
                        RestoredChunkRecord(
                            section=SECTION_OBSERVED,
                            ts=_now_iso(),
                            sequence=sequence,
                            chunk=body.decode("utf-8", errors="replace"),
                        )
                    )
                    sequence += 1
                if not message.get("more_body", False):
                    outcome, reason = _outcome_and_reason(
                        status_holder.get("status"), b"".join(response_chunks)
                    )
                    writer.write(
                        FooterRecord(
                            section=SECTION_OBSERVED,
                            ts=_now_iso(),
                            outcome=outcome,
                            reason=reason,
                            duration_ms=(time.monotonic() - start) * 1000,
                            upstream_duration_ms=ctx.upstream_duration_ms,
                            injected=dict(ctx.injected),
                        )
                    )
                    writer.close()
            await send(message)

        await self._replay(scope, messages, receive, send_and_capture)

    async def _replay(self, scope, messages, receive, send):
        index = 0

        async def replay_receive():
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return await receive()

        await self._app(scope, replay_receive, send)


def _outcome_and_reason(status: int | None, body: bytes) -> tuple[str, str | None]:
    if status == 200:
        return OUTCOME_PASSED, None
    reason = None
    try:
        reason = json.loads(body.decode("utf-8"))["error"]["reason"]
    except (json.JSONDecodeError, KeyError, UnicodeDecodeError, TypeError):
        reason = None
    if status == 503:
        return OUTCOME_BLOCKED, reason
    return OUTCOME_UPSTREAM_ERROR, reason
