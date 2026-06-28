"""HTTP proxy seam — contactable PII round trip (issue #5).

L1 deterministic detection (ADR-0003) blindfolds emails, phones, IBANs, and IDs on
every hop with reserved-namespace surrogates (ADR-0005); restore returns the real
contactable values to the client. The stub upstream is the egress oracle.

Leak-audit clauses asserted here:
- A: zero real PII values egressed (any hop, prose).
- B: the client receives fully restored real PII.
- E reserved-namespace: every PII surrogate that egressed sits inside its reserved /
  non-routable namespace (`.invalid`, NANPA `555-01XX`, ISO 3166 `XX`, `RESERVED`).

N/A this slice: streaming (issue #6/#12), tool-call JSON args (issue #11), L3 + the
fail-closed gate (issue #7), Transit-encrypted mapping (issue #10) — all out of scope.
"""

import json

import httpx
import pytest

from blindfold.app import app, get_upstream_client
from blindfold.upstream import UpstreamClient


@pytest.mark.anyio
async def test_round_trip_blindfolds_contactable_pii_and_restores_for_client():
    real_email = "contractor@third-party.com"
    real_phone = "+49 30 1234 5678"
    real_iban = "DE89 3704 0044 0532 0130 00"
    real_id = "ID: 9876543210"

    # The provider only ever sees surrogates, so it echoes the surrogates back. The
    # stub upstream records its inbound request so the test can read what egressed.
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        sent = json.loads(request.content.decode("utf-8"))
        # Echo back whatever surrogates appeared in the user turn so restore has
        # something to reverse on the response side (clause B).
        user_text = sent["messages"][0]["content"][0]["text"]
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": f"Acknowledged: {user_text}"}
                ],
                "model": "claude-3-5-sonnet",
                "stop_reason": "end_turn",
            },
        )

    upstream_client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    app.dependency_overrides[get_upstream_client] = lambda: UpstreamClient(
        base_url="http://upstream.test", client=upstream_client
    )
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
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        f"Email {real_email}, call {real_phone}, "
                                        f"wire to {real_iban}, customer {real_id}."
                                    ),
                                }
                            ],
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200

    # --- Clause A: zero real PII egressed upstream. ---
    egressed = captured["request"].content.decode("utf-8")
    for real in (real_email, real_phone, real_iban, real_id):
        assert real not in egressed, f"real PII leaked upstream: {real!r}"

    # --- Clause E reserved-namespace: PII surrogates sit in reserved namespaces. ---
    # Every PII surrogate the engine minted will appear in the egressed prose.
    assert "@blindfold.invalid" in egressed  # email surrogate (.invalid TLD)
    assert "+1-555-01" in egressed  # phone surrogate (NANPA fictional range)
    assert "XX99 " in egressed  # IBAN surrogate (unassigned ISO 3166 country)
    assert "ID-RESERVED-" in egressed  # ID surrogate (explicit reserved prefix)

    # --- Clause B: the client receives fully restored real PII. ---
    body = resp.json()
    client_text = body["content"][0]["text"]
    for real in (real_email, real_phone, real_iban, real_id):
        assert real in client_text, f"real PII not restored to client: {real!r}"
    # And no surrogate leaked into the client-visible response.
    assert "@blindfold.invalid" not in client_text
    assert "+1-555-01" not in client_text
    assert "XX99 " not in client_text
    assert "ID-RESERVED-" not in client_text
