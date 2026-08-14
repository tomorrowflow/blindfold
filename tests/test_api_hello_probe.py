"""``HEAD /api/hello`` (issue #267): Claude Code's connection-warming probe.

Gateway protocol: "a gateway also sees best-effort startup traffic it can reject
without breaking anything." The cheapest correct answer is a bare 200 with no
body, so it stops showing up as an unhandled-route oddity in logs.

N/A this slice throughout (issue's own framing): this probe touches no hop, mints
nothing, and reveals no config -- there is no request path here to leak-audit.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app


@pytest.mark.anyio
async def test_head_api_hello_returns_a_bare_200_with_no_body():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.head("/api/hello")

    assert resp.status_code == 200
    assert resp.content == b""
