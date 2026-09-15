"""Live-session capture (ADR-0047 §4, issue #254; issue #382).

Composes an **Exchange capture** (issue #253) around a real exchange through
``blindfold.app:app`` on three seams only, per the ADR -- no new hook is added
to the request path:

- ``app.dependency_overrides`` for ``get_upstream_client`` / ``get_mapping`` /
  ``get_l3_detector`` -- the test suite's own established substitution
  mechanism (:func:`check_override_targets` resolves and shape-checks these
  three at install time, failing loudly on drift).
- A plain module-attribute substitution of ``blindfold.app.blindfold_payload``
  (a new seam, issue #382 -- ADR-0047 §4 named only the three overrides above,
  but the same "no new hook" discipline applies) -- ``_exchange`` (app.py)
  reads this name from its own module namespace at call time, so replacing
  the attribute composes the same way as the three dependency overrides.
  This is what tees the engine's own ``ExchangeSession.injected`` --
  ADR-0047 §3's "complete pair table", covering both a graph-known
  **lookup** and a novel **mint** -- into the capture; also resolved and
  checked at install time.
- A plain ASGI callable wrapping ``blindfold.app:app`` for the client side
  (the real inbound payload, the restored response) -- deliberately *not*
  registered via Starlette's ``app.add_middleware`` (which refuses once the
  app's own middleware stack has already been built by an earlier request
  elsewhere in the process; a devtools entry point must compose regardless
  of what else already touched the shared ``app`` singleton).
"""

from __future__ import annotations

import contextvars
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request

from blindfold import app as blindfold_app
from blindfold.build_info import get_build_identity
from blindfold.policy import DEFAULT_WORKSPACE
from blindfold.processing_trace import (
    OUTCOME_BLOCKED,
    OUTCOME_PASSED,
    OUTCOME_UPSTREAM_ERROR,
)
from blindfold.upstream import UpstreamClient

from .capture import (
    OUTCOME_ABANDONED,
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
    """Per-request capture state, shared between the ASGI middleware and the
    wrapped dependency providers (via ``request.state.capture``) and the
    wrapped ``blindfold_payload`` substitution (via ``_active_capture``,
    below -- that seam has no ``Request`` to hang state off, only the
    module-level function it replaces)."""

    writer: CaptureWriter
    capture_id: str
    injected: dict[str, str] = field(default_factory=dict)
    upstream_duration_ms: float | None = None
    # Issue #385: set once the outbound record is written, by whichever seam gets
    # there first -- ``_wrap_blindfold_payload`` (every exchange that blinds,
    # including one the leak gate goes on to block) or ``_CapturingUpstreamClient``
    # (unprotected mode, ADR-0038, where blinding is bypassed entirely). Guards
    # against writing it twice for the ordinary pass/upstream-error outcomes, where
    # both seams run.
    outbound_recorded: bool = False
    # Issue #385: set once the completion-marker footer is written (the normal
    # pass/blocked/upstream-error path, in ``send_and_capture`` below). Lets the
    # middleware's own teardown tell a normal finish apart from one that never got
    # there, so it never overwrites a real footer with an "abandoned" one.
    footer_written: bool = False


# Issue #382: `blindfold_payload` is substituted as a plain module attribute,
# not a FastAPI dependency -- so, unlike the three `dependency_overrides`
# providers, its wrapper has no `Request` to read
# `request.state.capture` from. The ASGI middleware and the wrapped function
# run in the same asyncio task for a given request, so a `ContextVar` carries
# the active `_CaptureContext` across that boundary without adding any new
# hook to the request path.
_active_capture: contextvars.ContextVar["_CaptureContext | None"] = contextvars.ContextVar(
    "_active_capture", default=None
)

# Bound at import time, before `install_capture` can ever wrap it -- so every
# `install_capture` call re-wraps the true original rather than stacking a new
# layer onto whatever a previous call already installed (this module attribute,
# unlike `app.dependency_overrides`, is never reset between installs).
_ORIGINAL_BLINDFOLD_PAYLOAD = blindfold_app.blindfold_payload


def _wrap_blindfold_payload(existing):
    """Tee ``session.injected`` -- the engine's own authoritative pair table
    (ADR-0047 §3: it records both a graph-known **lookup** and a novel
    **mint**, via ``session.record``) -- into the active request's capture
    context, and record the blindfolded outbound payload itself (issue #385).

    Writing the ``OutboundRecord`` here -- the moment blinding produces it --
    rather than only at the actual upstream call, is what makes a **blocked**
    exchange's capture forensic: ``_leak_gate_or_block`` (app.py) returns
    straight to the client on a block, never reaching
    ``upstream.send_*``/``open_stream``, so a capture that only teed at that
    later seam had no ``outbound`` record at all for the one outcome the
    payload is most worth inspecting for. ``ctx.outbound_recorded`` guards
    against a second, redundant write once the (still-blinded, unchanged)
    payload actually reaches ``_CapturingUpstreamClient`` on a pass or an
    upstream error.

    A passthrough otherwise: ``payload``/``mapping`` are declared explicitly
    (rather than folded into ``*args``) only so this wrapper keeps
    ``check_override_targets``'s "still requires an argument" shape check
    meaningful on a re-``install_capture`` call, which sees this wrapper
    itself as the current ``blindfold_payload``; everything else ``_exchange``
    (app.py) passes is forwarded unchanged."""

    def _wrapped(payload, mapping, *args, **kwargs):
        out, session = existing(payload, mapping, *args, **kwargs)
        ctx = _active_capture.get()
        if ctx is not None:
            ctx.injected.update(session.injected)
            if not ctx.outbound_recorded:
                ctx.writer.write(
                    OutboundRecord(section=SECTION_OBSERVED, ts=_now_iso(), payload=out)
                )
                ctx.outbound_recorded = True
        return out, session

    return _wrapped


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
        # Issue #385: already written by ``_wrap_blindfold_payload`` for the
        # ordinary (blinded) case -- this fires only under unprotected mode
        # (ADR-0038), which bypasses blinding entirely and never reaches that seam.
        if self._ctx.outbound_recorded:
            return
        self._ctx.writer.write(
            OutboundRecord(section=SECTION_OBSERVED, ts=_now_iso(), payload=payload)
        )
        self._ctx.outbound_recorded = True

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
    # Issue #382: the pair table used to be reconstructed here, from every
    # `seed()`/`mint_pii()` call the mapping saw -- but a lookup (a value the
    # entity graph already knows, minted on a *prior* request) never calls
    # either: detection attaches `span.surrogate` from the graph and the
    # engine records the pair straight onto its own `ExchangeSession.injected`
    # (engine.py's `session.record`). That session -- teed by
    # `_wrap_blindfold_payload` below -- is ADR-0047 §3's "complete pair
    # table"; this provider composes cleanly with drift detection (one of the
    # ADR's three named override targets) but no longer needs to record
    # anything itself.
    def _provider(request: Request):
        return existing()

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
    the effective provider -- production or a test's own stub, composably),
    substitute ``blindfold_payload`` (a new seam, issue #382), and return a
    plain ASGI callable wrapping ``app`` for the client side.

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
    blindfold_app.blindfold_payload = _wrap_blindfold_payload(_ORIGINAL_BLINDFOLD_PAYLOAD)
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
        build_identity = get_build_identity()
        writer.write(
            HeaderRecord(
                section=SECTION_OBSERVED,
                ts=_now_iso(),
                capture_id=capture_id,
                endpoint=endpoint,
                streamed=streamed,
                workspace=workspace,
                inbound_payload=inbound_payload,
                build_sha=build_identity.sha,
                build_dirty=build_identity.dirty,
                build_source=build_identity.source,
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
                    ctx.footer_written = True
                    writer.close()
            await send(message)

        token = _active_capture.set(ctx)
        try:
            await self._replay(scope, messages, receive, send_and_capture)
        except BaseException as exc:
            # Issue #385: the exchange's own coroutine is tearing down without ever
            # reaching `send_and_capture`'s own completion marker above -- a client
            # disconnect or a mid-stream failure `_stream_restored` (app.py) does not
            # itself catch. Record what had arrived, with the incomplete state
            # explicit (`OUTCOME_ABANDONED`), rather than leaving a footer-less file
            # that reads identically to one still genuinely in flight (ADR-0047 §5).
            if not ctx.footer_written:
                writer.write(
                    FooterRecord(
                        section=SECTION_OBSERVED,
                        ts=_now_iso(),
                        outcome=OUTCOME_ABANDONED,
                        reason=f"{type(exc).__name__}: {exc}",
                        duration_ms=(time.monotonic() - start) * 1000,
                        upstream_duration_ms=ctx.upstream_duration_ms,
                        injected=dict(ctx.injected),
                    )
                )
                ctx.footer_written = True
                writer.close()
            raise
        finally:
            _active_capture.reset(token)

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
