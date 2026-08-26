"""Transit client: OpenBao Transit API seam (ADR-0008 / issue #10).

Tests stub OpenBao at the HTTP network boundary using httpx.MockTransport.
The transit client never holds key material — it drives the API.

Leak-audit clause analysis:
- A/B/C/D/E — N/A: this file tests the Transit seam, not the proxy request path.
- F (access control) — N/A: access control is tested at the endpoint level.
- G (mapping secrecy) — covered by design: TransitClient only handles ciphertext
  strings; key material never enters the app process.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from blindfold.transit import TransitClient, TransitError


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _make_transit_transport(responses: dict[str, dict]) -> httpx.MockTransport:
    """Return a MockTransport that serves pre-canned Transit API responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        if key in responses:
            return httpx.Response(200, json={"data": responses[key]})
        return httpx.Response(404, json={"errors": ["not found"]})

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# 1. encrypt — wraps plaintext as Transit ciphertext
# ---------------------------------------------------------------------------


def test_transit_encrypt_sends_base64_plaintext_and_returns_ciphertext():
    ciphertext = "vault:v1:abc123"
    transport = _make_transit_transport(
        {"POST /v1/transit/encrypt/blindfold-mapping": {"ciphertext": ciphertext}}
    )
    client = TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=transport),
    )
    result = client.encrypt("Martin Bach")
    assert result == ciphertext


# ---------------------------------------------------------------------------
# 2. decrypt — returns plaintext from Transit ciphertext
# ---------------------------------------------------------------------------


def test_transit_decrypt_sends_ciphertext_and_returns_plaintext():
    plaintext = "Martin Bach"
    transport = _make_transit_transport(
        {"POST /v1/transit/decrypt/blindfold-mapping": {"plaintext": _b64(plaintext)}}
    )
    client = TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=transport),
    )
    result = client.decrypt("vault:v1:abc123")
    assert result == plaintext


# ---------------------------------------------------------------------------
# 2b. decrypt — a non-200 response is a named TransitError (issue #364), never a
#     bare KeyError from indexing a response body that was never a success body.
# ---------------------------------------------------------------------------


def test_transit_decrypt_raises_named_error_on_non_200_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": ["permission denied"]})

    client = TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(TransitError) as excinfo:
        client.decrypt("vault:v1:abc123")
    assert excinfo.value.operation == "decrypt"
    assert excinfo.value.status_code == 403
    message = str(excinfo.value)
    assert "403" in message
    assert "decrypt" in message
    assert "abc123" not in message


def test_transit_decrypt_raises_named_error_on_200_response_missing_plaintext_field():
    """issue #364's exact repro: a 200 response shaped like encrypt's own reply
    (``data.ciphertext``, no ``data.plaintext``) must never surface as a bare
    KeyError.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"ciphertext": "vault:v1:fixture-stub"}})

    client = TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(TransitError) as excinfo:
        client.decrypt("vault:v1:abc123")
    assert excinfo.value.operation == "decrypt"
    assert excinfo.value.status_code == 200
    message = str(excinfo.value)
    assert "plaintext" in message
    assert "abc123" not in message
    assert "fixture-stub" not in message


def test_transit_encrypt_raises_named_error_on_non_200_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"errors": ["internal error"]})

    client = TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(TransitError) as excinfo:
        client.encrypt("Martin Bach")
    assert excinfo.value.operation == "encrypt"
    assert excinfo.value.status_code == 500
    assert "Martin Bach" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 3. blind_index — returns HMAC digest for equality lookups
# ---------------------------------------------------------------------------


def test_transit_blind_index_sends_base64_input_and_returns_hmac():
    hmac = "vault:v1:hmac:sha2-256:xyz789"
    transport = _make_transit_transport(
        {
            "POST /v1/transit/hmac/blindfold-blind-index/sha2-256": {
                "hmac": hmac
            }
        }
    )
    client = TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=transport),
    )
    result = client.blind_index("Martin Bach")
    assert result == hmac


# ---------------------------------------------------------------------------
# 4. Token header is forwarded
# ---------------------------------------------------------------------------


def test_transit_sends_vault_token_header():
    received_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received_headers.update(dict(request.headers))
        return httpx.Response(200, json={"data": {"ciphertext": "vault:v1:x"}})

    client = TransitClient(
        addr="http://openbao.test",
        token="my-secret-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.encrypt("hello")
    assert received_headers.get("x-vault-token") == "my-secret-token"


# ---------------------------------------------------------------------------
# 5. is_root_token — self-lookup identifies a root token by its policy set
#    (SEC-2 / issue #44: the proxy must refuse to start on a root token).
# ---------------------------------------------------------------------------


def test_transit_is_root_token_true_when_self_lookup_policies_are_root():
    transport = _make_transit_transport(
        {"GET /v1/auth/token/lookup-self": {"policies": ["root"]}}
    )
    client = TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=transport),
    )
    assert client.is_root_token() is True


def test_transit_is_root_token_false_for_a_scoped_policy_token():
    transport = _make_transit_transport(
        {"GET /v1/auth/token/lookup-self": {"policies": ["default", "blindfold-proxy"]}}
    )
    client = TransitClient(
        addr="http://openbao.test",
        token="blindfold-proxy-token",
        http=httpx.Client(transport=transport),
    )
    assert client.is_root_token() is False


# ---------------------------------------------------------------------------
# 6. health_check — /v1/status's transit dependency probe (issue #92)
# ---------------------------------------------------------------------------


def test_transit_health_check_reports_healthy_when_the_daemon_answers():
    from blindfold.status import DependencyHealth

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/sys/health"
        return httpx.Response(200, json={"initialized": True, "sealed": False})

    client = TransitClient(
        addr="http://openbao.test",
        token="blindfold-proxy-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.health_check() == DependencyHealth(healthy=True)


# ---------------------------------------------------------------------------
# 7. TransitClient satisfies the MappingCipher seam unchanged (ADR-0045 §2,
#    issue #228) -- encrypt/decrypt/blind_index is the entire touch surface.
# ---------------------------------------------------------------------------


def test_transit_client_satisfies_the_mapping_cipher_protocol():
    from blindfold.mapping_cipher import MappingCipher

    client = TransitClient(addr="http://openbao.test", token="dev-root-token")
    assert isinstance(client, MappingCipher)


def test_transit_health_check_reports_unhealthy_scrubbed_detail_when_unreachable():
    from blindfold.status import DependencyHealth

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = TransitClient(
        addr="http://openbao.test",
        token="blindfold-proxy-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.health_check() == DependencyHealth(healthy=False, detail="openbao unreachable")
