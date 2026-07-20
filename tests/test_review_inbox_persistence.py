"""Persisted review inbox survives a process restart (ADR-0037, issue #169).

Fast, hermetic unit tests only -- no real Postgres/OpenBao (the store's own
round-trip is covered, Docker-gated, in test_postgres_review_inbox_store.py).
Transit is stubbed at the network boundary (mirrors _EchoTransit in
test_bootstrap_wiring.py) -- never a plaintext shortcut. Mirrors
test_allowlist_persistence.py's convention of a recording test double
standing in for the Postgres-backed store.

Leak-audit clauses for this slice:
- A/B/C/D N/A -- no request-path egress/restore behavior changes; only the
  review inbox's own persistence seam is exercised here.
- E -- covered: the cross-restart collision test strengthens stable/
  idempotent-mint (issue #80's cursor must survive a restart).
- F N/A -- fail-closed (L3Unavailable) untouched.
- G -- covered: `real`/`context` are asserted to reach the store only as
  Transit ciphertext, never plaintext (see also the Docker-gated on-disk
  assertion in test_postgres_review_inbox_store.py).
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from blindfold.app import app, get_allowlist, get_review_inbox
from blindfold.review import Allowlist, ReviewInbox
from blindfold.transit import TransitClient


class _EchoTransit(TransitClient):
    """encrypt() wraps plaintext in a fake ciphertext tag; the stub network
    handler reverses it on decrypt / computes a deterministic blind index --
    a network-boundary stub (mirrors test_bootstrap_wiring.py's _EchoTransit),
    never a plaintext shortcut."""

    def encrypt(self, plaintext: str) -> str:
        return f"vault:v1:{plaintext}"


def _stub_transit() -> TransitClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "/hmac/" in str(request.url):
            plaintext = base64.b64decode(body["input"]).decode()
            return httpx.Response(200, json={"data": {"hmac": f"blind:{plaintext}"}})
        plaintext = body["ciphertext"].removeprefix("vault:v1:")
        encoded = base64.b64encode(plaintext.encode()).decode()
        return httpx.Response(200, json={"data": {"plaintext": encoded}})

    return _EchoTransit(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


class _RecordingReviewInboxStore:
    """Test double standing in for PostgresReviewInboxStore -- no real Postgres."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple] = {}
        self.positions: dict[str, int] = {}

    def upsert_row(
        self,
        item_id,
        real_ciphertext,
        real_blind_index,
        context_ciphertext,
        context_offset,
        provisional_surrogate,
        entity_type,
    ) -> None:
        self.rows[item_id] = (
            real_ciphertext,
            real_blind_index,
            context_ciphertext,
            context_offset,
            provisional_surrogate,
            entity_type,
        )

    def remove_row(self, item_id) -> None:
        self.rows.pop(item_id, None)

    def list_rows(self):
        return [
            (item_id, r[0], r[2], r[3], r[4], r[5]) for item_id, r in self.rows.items()
        ]

    def pool_positions(self) -> dict[str, int]:
        return dict(self.positions)

    def set_pool_position(self, pool_key: str, position: int) -> None:
        self.positions[pool_key] = position


def test_upsert_persists_real_and_context_through_transit_encrypt_and_blind_index():
    # The store never encrypts anything itself (leak-audit clause G) --
    # ReviewInbox must call transit.encrypt()/transit.blind_index() itself
    # and hand the store only the result. The stub's "vault:v1:" tag/"blind:"
    # prefix are the network-boundary stub's own round-trippable encoding
    # (mirrors _EchoTransit elsewhere), not a plaintext passthrough -- the
    # Docker-gated store test separately asserts an opaque real store never
    # sees the plaintext at all.
    store = _RecordingReviewInboxStore()
    transit = _stub_transit()
    inbox = ReviewInbox(store=store, transit=transit)

    inbox.upsert("Helga Krause", context="Please brief Helga Krause tomorrow.")

    (real_ciphertext, real_blind_index, context_ciphertext, *_rest) = next(
        iter(store.rows.values())
    )
    assert real_ciphertext == "vault:v1:Helga Krause"
    assert real_blind_index == "blind:Helga Krause"
    assert context_ciphertext == "vault:v1:Please brief Helga Krause tomorrow."


def test_upsert_is_a_no_op_persistence_wise_when_store_is_none():
    # #149 graceful degradation: no store configured -> in-memory, ephemeral,
    # byte-identical to before this slice. Must not raise even with no transit.
    inbox = ReviewInbox()

    item = inbox.upsert("Klaus", context="Please brief Klaus tomorrow.")

    assert item.real == "Klaus"


def test_upsert_is_a_no_op_persistence_wise_when_transit_is_none():
    # Both Postgres AND Transit must be configured to persist (ADR-0037) --
    # a store with no transit must not crash and must not persist.
    store = _RecordingReviewInboxStore()
    inbox = ReviewInbox(store=store, transit=None)

    inbox.upsert("Klaus", context="Please brief Klaus tomorrow.")

    assert store.rows == {}


def test_remove_deletes_the_persisted_row():
    store = _RecordingReviewInboxStore()
    transit = _stub_transit()
    inbox = ReviewInbox(store=store, transit=transit)
    item = inbox.upsert("Helga", context="Please brief Helga tomorrow.")

    inbox.remove(item.id)

    assert store.rows == {}


def test_attach_store_hydrates_every_persisted_item():
    # Simulates a process restart: a store already holds an item from "before"
    # (encrypted with the same Transit key), and a freshly-constructed inbox
    # (as app.py's bootstrap builds at startup) must reconstruct it in full,
    # including entity_type (acceptance criterion 1).
    store = _RecordingReviewInboxStore()
    transit = _stub_transit()
    store.upsert_row(
        "1",
        "vault:v1:Nordwind Logistik",
        "blind:Nordwind Logistik",
        "vault:v1:...von Nordwind Logistik heute",
        4,
        "Waldstein Industries",
        "organization",
    )
    store.set_pool_position("organization", 1)

    inbox = ReviewInbox()
    inbox.attach_store(store, transit)

    assert len(inbox.list()) == 1
    item = inbox.get("1")
    assert item.real == "Nordwind Logistik"
    assert item.context == "...von Nordwind Logistik heute"
    assert item.context_offset == 4
    assert item.provisional_surrogate == "Waldstein Industries"
    assert item.entity_type == "organization"


def test_attach_store_is_a_no_op_when_transit_is_none():
    # Acceptance criterion 6: no Transit configured -> stay in-memory/ephemeral,
    # never attempt to decrypt (would crash without a real Transit client).
    store = _RecordingReviewInboxStore()
    store.upsert_row(
        "1", "vault:v1:X", "blind:X", "vault:v1:ctx", 0, "Surrogate", None
    )

    inbox = ReviewInbox()
    inbox.attach_store(store, None)

    assert inbox.list() == []


def test_re_encountering_a_persisted_real_after_restart_reuses_its_item():
    # Acceptance criterion 4: dedup survives a restart -- no duplicate row.
    store = _RecordingReviewInboxStore()
    transit = _stub_transit()
    store.upsert_row(
        "1", "vault:v1:Helga", "blind:Helga", "vault:v1:ctx", 0, "Alex Brenner", None
    )

    inbox = ReviewInbox()
    inbox.attach_store(store, transit)
    item = inbox.upsert("Helga", context="ctx")

    assert item.id == "1"
    assert len(store.rows) == 1


def test_cross_restart_mint_does_not_reuse_a_persisted_items_surrogate():
    # Acceptance criterion 3: mint items -> restart (reload from store) ->
    # mint a new novel value -> must not reuse any persisted item's surrogate.
    store = _RecordingReviewInboxStore()
    transit = _stub_transit()
    first_inbox = ReviewInbox(store=store, transit=transit)
    seeded = first_inbox.upsert("Existing Real", context="ctx")

    restarted_inbox = ReviewInbox()
    restarted_inbox.attach_store(store, transit)
    novel = restarted_inbox.upsert("Brand New Real", context="ctx")

    assert novel.provisional_surrogate != seeded.provisional_surrogate


@pytest.mark.anyio
async def test_reject_after_restart_deletes_the_persisted_row():
    # Acceptance criterion 5.
    store = _RecordingReviewInboxStore()
    transit = _stub_transit()
    inbox = ReviewInbox(store=store, transit=transit)
    item = inbox.upsert("Helga", context="Please brief Helga tomorrow.")
    allowlist = Allowlist()

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(f"/v1/management/review-inbox/{item.id}/reject")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert store.rows == {}


@pytest.mark.anyio
async def test_confirm_after_restart_deletes_the_persisted_row():
    # Acceptance criterion 5's other half: confirm() also removes the row.
    from blindfold.app import get_mapping
    from blindfold.surrogates import SurrogateMapping

    store = _RecordingReviewInboxStore()
    transit = _stub_transit()
    inbox = ReviewInbox(store=store, transit=transit)
    item = inbox.upsert("Helga", context="Please brief Helga tomorrow.")
    mapping = SurrogateMapping.from_pairs([])

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_mapping] = lambda: mapping
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(f"/v1/management/review-inbox/{item.id}/confirm")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert store.rows == {}


def test_get_review_inbox_store_returns_none_when_database_url_unset(monkeypatch):
    from blindfold.app import get_review_inbox_store

    monkeypatch.delenv("BLINDFOLD_DATABASE_URL", raising=False)

    assert get_review_inbox_store() is None


def test_get_review_inbox_store_returns_a_store_when_database_url_configured(monkeypatch):
    from blindfold.app import get_review_inbox_store

    monkeypatch.setenv("BLINDFOLD_DATABASE_URL", "postgresql://user:pass@localhost/blindfold")
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "ollama")

    calls = []

    class _FakeStore:
        def __init__(self, database_url):
            calls.append(database_url)

    monkeypatch.setattr(
        "blindfold.store.review_inbox_store.PostgresReviewInboxStore", _FakeStore
    )

    store = get_review_inbox_store()

    assert isinstance(store, _FakeStore)
    assert calls == ["postgresql://user:pass@localhost/blindfold"]


def test_hydrate_review_inbox_from_store_is_a_no_op_when_store_is_none():
    from blindfold.app import hydrate_review_inbox_from_store

    inbox = ReviewInbox()
    hydrate_review_inbox_from_store(inbox, None, _stub_transit())  # must not raise

    assert inbox.list() == []


def test_hydrate_review_inbox_from_store_is_a_no_op_when_transit_is_none():
    from blindfold.app import hydrate_review_inbox_from_store

    store = _RecordingReviewInboxStore()
    store.upsert_row("1", "vault:v1:X", "blind:X", "vault:v1:ctx", 0, "Surrogate", None)
    inbox = ReviewInbox()

    hydrate_review_inbox_from_store(inbox, store, None)  # must not raise

    assert inbox.list() == []


def test_hydrate_review_inbox_from_store_wires_persistence_for_future_upserts():
    from blindfold.app import hydrate_review_inbox_from_store

    store = _RecordingReviewInboxStore()
    transit = _stub_transit()
    inbox = ReviewInbox()

    hydrate_review_inbox_from_store(inbox, store, transit)
    inbox.upsert("Klaus", context="Please brief Klaus tomorrow.")

    assert len(store.rows) == 1
