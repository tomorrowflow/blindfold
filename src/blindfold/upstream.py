"""Upstream provider client.

Thin wrapper around an httpx client pointed at the configurable upstream base URL.
Tests inject a stub client at the network boundary (the egress oracle) by passing a
pre-built ``httpx.AsyncClient`` with a ``MockTransport``.
"""

from __future__ import annotations

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

    async def send_messages(
        self, payload: dict, headers: dict[str, str]
    ) -> dict:
        response = await self._client.post(
            "/v1/messages", json=payload, headers=headers
        )
        response.raise_for_status()
        return response.json()
