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

    ``anthropic_error_type``/``retry_after`` (issue #380, option B, dated amendment to
    ADR-0019): set only for the preserved upstream status-class case (401/403/429/400/
    529) -- ``_map_httpx_error`` below relays the upstream's status *class*, never its
    body text, so ``message`` stays a fixed, Blindfold-authored string per class and
    ``anthropic_error_type`` carries the matching Anthropic-vocabulary ``error.type``
    (e.g. ``authentication_error``) so a client that keys off that shape (Claude
    Desktop's 3P Gateway mode) can render it meaningfully instead of a generic gateway
    failure. ``None`` (the default) means "no class mapping" -- the generic
    ``blindfold_upstream_error`` shape applies, unchanged.
    """

    def __init__(
        self,
        status_code: int,
        sub_reason: str,
        message: str,
        *,
        anthropic_error_type: str | None = None,
        retry_after: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.sub_reason = sub_reason
        self.anthropic_error_type = anthropic_error_type
        self.retry_after = retry_after
        super().__init__(message)


# Issue #380 (option B, trusted-maintainer decision, dated amendment to ADR-0019):
# the upstream status *class* is preserved for these five statuses -- each maps to a
# fixed, Blindfold-authored message and the matching Anthropic-vocabulary
# ``error.type`` so a client that keys off that shape (Claude Desktop's 3P Gateway
# mode) can render a bad key / rate limit meaningfully. No upstream body text is ever
# relayed. Any status not in this table keeps the pre-existing generic 502 mapping.
_UPSTREAM_STATUS_CLASSES: dict[int, tuple[str, str]] = {
    400: ("invalid_request_error", "Upstream rejected the request as malformed."),
    401: ("authentication_error", "Upstream rejected the configured API key."),
    403: ("permission_error", "Upstream denied access with the configured credentials."),
    429: ("rate_limit_error", "Upstream is rate-limiting the configured API key."),
    529: ("overloaded_error", "Upstream is temporarily overloaded."),
}

# Statuses whose ``retry-after`` response header is preserved onto the mapped error
# (issue #380 AC2) -- the class table above additionally carries this hint.
_RETRY_AFTER_PRESERVED_STATUSES = {429, 529}


def _map_httpx_error(exc: httpx.HTTPError) -> UpstreamError:
    """Map an httpx transport/HTTP error to the structured, scrubbed ``UpstreamError``.

    - ``HTTPStatusError`` (from ``raise_for_status``) whose status is one of
      ``_UPSTREAM_STATUS_CLASSES`` -> preserved as that status, with the matching
      ``anthropic_error_type`` and a fixed message (issue #380, option B): the
      upstream status *class* is kept, the body is not.
    - ``HTTPStatusError`` at any other status -> 502, the upstream itself returned an
      error status; only the status code is reported, never the body.
    - ``TimeoutException`` (connect/read/write/pool timeout) -> 504.
    - Any other transport error (DNS failure, connection refused, reset) -> 502.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        mapped = _UPSTREAM_STATUS_CLASSES.get(status_code)
        if mapped is not None:
            anthropic_error_type, message = mapped
            retry_after = None
            if status_code in _RETRY_AFTER_PRESERVED_STATUSES:
                retry_after = exc.response.headers.get("retry-after")
            return UpstreamError(
                status_code=status_code,
                sub_reason=f"upstream_{status_code}",
                message=message,
                anthropic_error_type=anthropic_error_type,
                retry_after=retry_after,
            )
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

    async def send_count_tokens(
        self, payload: dict, headers: dict[str, str]
    ) -> dict:
        """Forward a blindfolded body to the upstream's own count-tokens endpoint
        (issue #267): a hop-shaped request (``system``/``messages``/``tools``, no
        sampling params) that returns a bare token count, never surrogate text --
        there is no restore side to this call.
        """
        try:
            response = await self._client.post(
                "/v1/messages/count_tokens", json=payload, headers=headers
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
