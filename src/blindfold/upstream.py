"""Upstream provider client.

Thin wrapper around an httpx client pointed at the configurable upstream base URL.
Tests inject a stub client at the network boundary (the egress oracle) by passing a
pre-built ``httpx.AsyncClient`` with a ``MockTransport``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from .config import Settings


class UpstreamClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url
        self._client = client or httpx.AsyncClient(base_url=base_url)

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
        response = await self._client.post(
            "/v1/messages", json=payload, headers=headers
        )
        response.raise_for_status()
        return response.json()

    async def send_chat_completions(
        self, payload: dict, headers: dict[str, str]
    ) -> dict:
        response = await self._client.post(
            "/v1/chat/completions", json=payload, headers=headers
        )
        response.raise_for_status()
        return response.json()

    @asynccontextmanager
    async def stream_messages(
        self, payload: dict, headers: dict[str, str]
    ) -> AsyncIterator[httpx.Response]:
        """Open a streaming POST to ``/v1/messages`` (SSE response from the provider)."""
        async with self._client.stream(
            "POST", "/v1/messages", json=payload, headers=headers
        ) as response:
            response.raise_for_status()
            yield response
