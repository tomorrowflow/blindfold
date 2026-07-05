"""OpenBao Transit client — encryption-as-a-service seam (ADR-0008 / issue #10).

Keys live in OpenBao; the app process never holds key material. Callers pass in
a pre-configured ``httpx.Client`` (or ``httpx.AsyncClient``) so tests can stub
the network boundary with ``httpx.MockTransport``.

Transit key names (decided in HITL comment on issue #10):
  blindfold-mapping        encrypt/decrypt real values
  blindfold-blind-index    deterministic HMAC for equality lookups (blind index)
"""

from __future__ import annotations

import base64

import httpx

_MAPPING_KEY = "blindfold-mapping"
_BLIND_INDEX_KEY = "blindfold-blind-index"
_HMAC_ALGORITHM = "sha2-256"


class TransitClient:
    """Synchronous OpenBao Transit client.

    Inject via ``http=httpx.Client(transport=httpx.MockTransport(...))`` in tests.
    Production wiring reads ``BLINDFOLD_OPENBAO_ADDR`` / ``BLINDFOLD_OPENBAO_TOKEN``
    from the environment and constructs a real httpx.Client.
    """

    def __init__(
        self,
        addr: str,
        token: str,
        http: httpx.Client | None = None,
        mapping_key: str = _MAPPING_KEY,
        blind_index_key: str = _BLIND_INDEX_KEY,
    ) -> None:
        self._addr = addr.rstrip("/")
        self._token = token
        self._mapping_key = mapping_key
        self._blind_index_key = blind_index_key
        self._http = http or httpx.Client(
            base_url=self._addr,
            headers={"X-Vault-Token": self._token},
        )

    def encrypt(self, plaintext: str) -> str:
        """Encrypt ``plaintext`` via Transit; returns ciphertext (vault:v1:…)."""
        encoded = base64.b64encode(plaintext.encode()).decode()
        resp = self._http.post(
            f"{self._addr}/v1/transit/encrypt/{self._mapping_key}",
            json={"plaintext": encoded},
            headers={"X-Vault-Token": self._token},
        )
        resp.raise_for_status()
        return resp.json()["data"]["ciphertext"]

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt ``ciphertext`` via Transit; returns the original plaintext string."""
        resp = self._http.post(
            f"{self._addr}/v1/transit/decrypt/{self._mapping_key}",
            json={"ciphertext": ciphertext},
            headers={"X-Vault-Token": self._token},
        )
        resp.raise_for_status()
        encoded = resp.json()["data"]["plaintext"]
        return base64.b64decode(encoded).decode()

    def is_root_token(self) -> bool:
        """Self-lookup the configured token; True iff it carries the ``root`` policy.

        Used by the startup guard (SEC-2, issue #44): the proxy refuses to run against
        a root Transit token outside an explicit dev-mode opt-in, since root bypasses
        every policy (blindfold-proxy/-human/-admin) the store's RBAC separation
        depends on.
        """
        resp = self._http.get(
            f"{self._addr}/v1/auth/token/lookup-self",
            headers={"X-Vault-Token": self._token},
        )
        resp.raise_for_status()
        return resp.json()["data"]["policies"] == ["root"]

    def blind_index(self, value: str) -> str:
        """Return the HMAC digest of ``value`` for equality lookups over ciphertext columns."""
        encoded = base64.b64encode(value.encode()).decode()
        resp = self._http.post(
            f"{self._addr}/v1/transit/hmac/{self._blind_index_key}/{_HMAC_ALGORITHM}",
            json={"input": encoded},
            headers={"X-Vault-Token": self._token},
        )
        resp.raise_for_status()
        return resp.json()["data"]["hmac"]
