"""Re-identify store seeding from the vendored seed (issue #43 / UX-1).

A fresh install's Reveal must resolve a seeded surrogate on localhost with no
Postgres/ETL population path -- the in-memory re-identify store needs the same
vendored seed the entity graph and mapping already use.

Transit is stubbed at the network boundary (httpx.MockTransport), per leak-audit
convention. Leak-audit clause analysis: A/B/C/D/E/F -- N/A, no proxy request path.
G (mapping secrecy) -- covered: the store holds only Transit ciphertext, never the
plaintext real value; this test asserts the store's ciphertext round-trips through
Transit decrypt to recover the real value, never that the store holds plaintext.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from blindfold.reidentify import InMemoryReIdentificationStore
from blindfold.store import vendored_seed_repository
from blindfold.transit import TransitClient


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _recording_transit() -> TransitClient:
    """A TransitClient whose encrypt/decrypt round-trip via a fake in-memory vault."""
    vault: dict[str, str] = {}
    counter = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/encrypt/blindfold-mapping"):
            counter[0] += 1
            ciphertext = f"vault:v1:{counter[0]}"
            vault[ciphertext] = body["plaintext"]
            return httpx.Response(200, json={"data": {"ciphertext": ciphertext}})
        if request.url.path.endswith("/decrypt/blindfold-mapping"):
            plaintext = vault[body["ciphertext"]]
            return httpx.Response(200, json={"data": {"plaintext": plaintext}})
        return httpx.Response(400, json={"errors": ["unhandled"]})

    return TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.anyio
async def test_seed_reidentify_store_lets_an_authorized_reveal_resolve_a_seeded_surrogate():
    store = InMemoryReIdentificationStore()
    transit = _recording_transit()
    repo = vendored_seed_repository()

    repo.seed_reidentify_store(store, transit, workspace="default")

    pairs = dict(repo.seeded_pairs())
    surrogate = pairs["Martin Bach"]

    ciphertext = await store.surrogate_to_ciphertext(surrogate, "default")
    assert ciphertext is not None
    assert transit.decrypt(ciphertext) == "Martin Bach"
