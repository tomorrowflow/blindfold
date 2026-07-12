"""Upstream provider client.

Thin wrapper around an httpx client pointed at the configurable upstream base URL.
Tests inject a stub client at the network boundary (the egress oracle) by passing a
pre-built ``httpx.AsyncClient`` with a ``MockTransport``.
"""

from __future__ import annotations

import httpx

from .config import Settings

# Issue #86: httpx.AsyncClient(base_url=...) with no timeout config inherits httpx's
# implicit 5s connect/read default -- a hosted provider's time-to-first-byte on a large
# blinded request (coding-agent system prompt, thinking enabled) routinely exceeds 5s,
# raising httpcore.ReadTimeout while waiting for response headers. Connect stays
# bounded (a dead upstream should fail fast); read is generous because the same client
# serves both the buffered send_* calls (TTFB can be slow) and the SSE streaming call
# (providers send pings, but gaps between them are normal and must not time out).
DEFAULT_UPSTREAM_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_UPSTREAM_READ_TIMEOUT_SECONDS = 300.0


class UpstreamError(Exception):
    """A mapped upstream-boundary failure (issue #86, mirrors SEC-7 / #48's contract).

    Distinct from :class:`~blindfold.engine.LeakError` / the ``blindfold_fail_closed``
    block path: this is an availability/contract failure at the provider egress (a
    connect timeout, a TTFB read timeout, or an upstream HTTP error status), not a
    privacy violation. ``message`` is scrubbed by construction -- it never echoes
    request/response payload content, only the transport-level failure shape -- so it
    is safe to route to the response body, the audit record, and the log, the same
    single-funnel pattern :func:`~blindfold.app._blocked_response` uses.
    """

    def __init__(self, status_code: int, sub_reason: str, message: str) -> None:
        self.status_code = status_code
        self.sub_reason = sub_reason
        super().__init__(message)


def _map_httpx_error(exc: httpx.HTTPError) -> UpstreamError:
    """Map an httpx transport/HTTP error to the structured, scrubbed ``UpstreamError``.

    - ``HTTPStatusError`` (from ``raise_for_status``) -> 502, the upstream itself
      returned an error status; only the status code is reported, never the body.
    - ``TimeoutException`` (connect/read/write/pool timeout) -> 504.
    - Any other transport error (DNS failure, connection refused, reset) -> 502.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return UpstreamError(
            status_code=502,
            sub_reason="upstream_http_error",
            message=f"upstream returned HTTP {exc.response.status_code}",
        )
    if isinstance(exc, httpx.TimeoutException):
        return UpstreamError(
            status_code=504,
            sub_reason="upstream_timeout",
            message="upstream did not respond in time",
        )
    return UpstreamError(
        status_code=502,
        sub_reason="upstream_unreachable",
        message="failed to reach upstream",
    )


class UpstreamClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url
        if client is not None:
            self._client = client
            return
        # Issue #101: this construction previously ran unguarded. FastAPI's
        # get_upstream_client/get_openai_upstream_client dependencies call this
        # eagerly during dependency *resolution* -- before a route's own try/except
        # ever runs -- so an unguarded construction failure here (a bad transport
        # config: missing CA bundle, malformed base URL) escaped as a raw ASGI 500
        # traceback. Map it to the same structured UpstreamError #86 already uses for
        # request-time failures; the app-level exception handler (app.py) catches it
        # regardless of whether it surfaces at dependency-resolution time or from
        # inside a route body.
        try:
            self._client = httpx.AsyncClient(
                base_url=base_url,
                timeout=httpx.Timeout(
                    DEFAULT_UPSTREAM_CONNECT_TIMEOUT_SECONDS,
                    connect=DEFAULT_UPSTREAM_CONNECT_TIMEOUT_SECONDS,
                    read=DEFAULT_UPSTREAM_READ_TIMEOUT_SECONDS,
                ),
            )
        except Exception as exc:
            raise UpstreamError(
                status_code=502,
                sub_reason="upstream_client_init_failed",
                message="failed to construct upstream client",
            ) from exc

    @property
    def base_url(self) -> str:
        """The configured upstream base URL this client forwards to."""
        return self._base_url

    @classmethod
    def from_settings(cls, settings: Settings) -> "UpstreamClient":
        return cls(base_url=settings.upstream_base_url)

    @classmethod
    def from_openai_settings(cls, settings: Settings) -> "UpstreamClient":
        """Build the client ``POST /v1/chat/completions`` egresses through.

        Uses the dedicated ``BLINDFOLD_OPENAI_UPSTREAM_BASE_URL`` when set (issue #76,
        transport sliver of #37); falls back to the shared upstream var otherwise, so
        an unconfigured dedicated var reproduces today's behavior exactly.
        """
        return cls(base_url=settings.effective_openai_upstream_base_url)

    async def send_messages(
        self, payload: dict, headers: dict[str, str]
    ) -> dict:
        try:
            response = await self._client.post(
                "/v1/messages", json=payload, headers=headers
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise _map_httpx_error(exc) from exc
        return response.json()

    async def send_chat_completions(
        self, payload: dict, headers: dict[str, str]
    ) -> dict:
        try:
            response = await self._client.post(
                "/v1/chat/completions", json=payload, headers=headers
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise _map_httpx_error(exc) from exc
        return response.json()

    async def open_stream(
        self, payload: dict, headers: dict[str, str]
    ) -> httpx.Response:
        """Open a streaming POST to ``/v1/messages``, returning once headers arrive.

        Issue #86: this method completes the connect + time-to-first-byte round trip
        before returning, so a connect/TTFB failure raises the structured
        ``UpstreamError`` here -- *before* the caller has started the client-facing
        ``StreamingResponse`` and committed a 200 status line. This is the httpx
        "manual streaming" pattern (``build_request`` + ``send(..., stream=True)``)
        rather than the ``async with client.stream(...)`` context-manager form,
        because the context-manager form ties connect and body-consumption to the
        same ``__aenter__``/``__aexit__`` pair, giving the caller no seam to observe
        "headers received" independently of "body fully consumed".

        The caller MUST call ``await response.aclose()`` when done consuming
        ``response.aiter_bytes()`` (including on a mid-stream error) — this method
        does not manage that lifetime, matching httpx's own manual-streaming contract.
        A transport error while reading the body (mid-stream disconnect, after this
        method already returned successfully) is NOT mapped to ``UpstreamError`` here:
        it propagates as the underlying httpx exception, because the caller is
        already streaming bytes to the client and must terminate the stream cleanly
        rather than construct a fresh JSON error response.
        """
        request = self._client.build_request(
            "POST", "/v1/messages", json=payload, headers=headers
        )
        try:
            response = await self._client.send(request, stream=True)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            await exc.response.aclose()
            raise _map_httpx_error(exc) from exc
        except httpx.HTTPError as exc:
            raise _map_httpx_error(exc) from exc
        return response
