"""Store persistence: confirm -> persist -> process restart -> restore round trip
(issue #343), parameterized over both dialects (the SQLite/Postgres dialect seam,
ADR-0043 §3).

#74's live-verify exercises the request path only (blind -> egress -> restore); it
never reaches **confirm**, so it never proves anything about the store layer. This
is the first live coverage of that path: a novel entity is minted via L3, confirmed
through the review inbox, and the resulting mapping is proven durable across a
simulated process restart (the established convention in this suite -- see
test_postgres_reidentify_store.py / test_review_inbox_persistence.py -- is a fresh
store/object built against the same DSN, never a spawned OS process).

Writing this test surfaced a real gap, not just a coverage hole: `blindfold.app`'s
process-global `SurrogateMapping` (`_mapping`) -- the dictionary the request path's
L2 pass and mint-time disjointness check consult -- was seeded only from the
vendored seed at import time and was **never** rehydrated from the durable
`ReIdentificationStore` (confirmed via a throwaway reproduction before writing this
test: confirm an entity, discard the in-process `SurrogateMapping`/`ReviewInbox` and
rebuild fresh ones against the same store the way an app restart would, resend the
same real value -- it re-adjudicated through L3 and minted a brand-new, different
surrogate). `hydrate_mapping_from_reidentify_store` (app.py) closes this: it seeds
`_mapping` from every persisted (surrogate, workspace, ciphertext) entry at the same
startup point `hydrate_review_inbox_from_store` already hydrates the review inbox
from, so a confirmed entity's surrogate is stable across a restart, matching the
confirm endpoint's own documented promise ("the same real value is detected
deterministically by L2 ... without an L3 call").

Leak-audit clause analysis:
- A/B/C/D: covered directly -- the round trip drives the full request path through
  the stub upstream/L3 boundary both before and after the simulated restart and
  asserts the egress/response shape each time (clean pass, no real value egressed,
  full restore on the client side).
- E: the primary property this issue names -- proven false before the fix (see
  above) and true after; stability is asserted end to end, not just at the store
  layer.
- F (fail-closed): N/A for this slice -- unaffected by store persistence, and
  already covered against a real SQLite backend by
  test_sqlite_transit_leak_audit.py's T4 (its Postgres analogue lives in the
  existing Docker-gated Postgres suite).
- G (mapping secrecy): covered directly -- the persisted `persons`/
  `reidentify_mappings` columns are asserted to hold Local-key-cipher ciphertext,
  never the plaintext real value, and the equality lookup that finds the row goes
  through the blind index (a `WHERE ... blind_index = ?` query), with no decrypt in
  the lookup itself.

The Postgres variant is Docker-gated via `conftest._docker_available` (the existing
gate every `tests/test_postgres_*.py` file uses) and skips cleanly, matching this
issue's own note not to fight #218 (no Docker-capable Sandcastle runner yet).
"""

from __future__ import annotations

import base64
import os

import httpx
import pytest

from conftest import _docker_available

pytestmark = pytest.mark.anyio


def _local_key_cipher():
    from blindfold.mapping_cipher import LocalKeyCipher

    return LocalKeyCipher(base64.b64encode(os.urandom(32)).decode())


@pytest.fixture(
    scope="module",
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(not _docker_available(), reason="Docker unavailable"),
        ),
    ],
)
def dsn(request, tmp_path_factory):
    """One DSN per dialect, backing every store this test drives (ADR-0043 §3):
    the confirm-and-persist round trip must behave identically regardless of which
    dialect ``connect()`` dispatches to.
    """
    if request.param == "sqlite":
        db_path = tmp_path_factory.mktemp("store-persistence-343") / "blindfold.sqlite3"
        yield f"sqlite:///{db_path}"
        return

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        yield pg.get_connection_url()


class _StubAdjudicator:
    """Stub for Ollama (L3): confirms only the whitelisted real value, and records
    every candidate it was asked about -- so a later assertion can prove L3 was
    *not* consulted again once the mapping is rehydrated (clause E, deterministic
    L2 detection)."""

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm
        self.calls: list[str] = []

    def adjudicate(self, candidate):
        from blindfold.l3 import L3Adjudication

        self.calls.append(candidate.text)
        return L3Adjudication(is_entity=candidate.text in self._confirm)


def _stub_upstream(scripted_response: dict, recorded: list[httpx.Request]):
    from blindfold.upstream import UpstreamClient

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


def _ack_response(text: str = "Acknowledged.") -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }


async def test_confirm_persist_restart_restore_round_trip(dsn):
    from blindfold.app import (
        app,
        get_entity_graph,
        get_l3_detector,
        get_mapping,
        get_mapping_cipher,
        get_reidentify_store,
        get_review_inbox,
        get_upstream_client,
        hydrate_mapping_from_reidentify_store,
    )
    from blindfold.l3 import L3Detector
    from blindfold.policy import DEFAULT_WORKSPACE
    from blindfold.review import ReviewInbox
    from blindfold.store.dialect import connect
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore
    from blindfold.store.reidentify_store import PostgresReIdentificationStore
    from blindfold.surrogates import SurrogateMapping

    cipher = _local_key_cipher()
    real_value = "Kestrelholt"

    # --- Step 1/2: mint via L3, then confirm ("process 1") ---------------------
    entity_graph_1 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    reidentify_store_1 = PostgresReIdentificationStore(dsn)
    mapping_1 = SurrogateMapping.from_pairs([])
    inbox_1 = ReviewInbox()
    adjudicator_1 = _StubAdjudicator(confirm={real_value})
    recorded_1: list[httpx.Request] = []

    app.dependency_overrides[get_upstream_client] = lambda: _stub_upstream(
        _ack_response(), recorded_1
    )
    app.dependency_overrides[get_mapping] = lambda: mapping_1
    app.dependency_overrides[get_review_inbox] = lambda: inbox_1
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(adjudicator_1)
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph_1
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store_1
    app.dependency_overrides[get_mapping_cipher] = lambda: cipher
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            mint_resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": f"Please brief {real_value} tomorrow."}
                    ],
                },
            )
            assert mint_resp.status_code == 200
            item = inbox_1.list()[0]
            confirm_resp = await client.post(
                f"/v1/management/review-inbox/{item.id}/confirm"
            )
    finally:
        app.dependency_overrides.clear()

    assert confirm_resp.status_code == 200
    surrogate = item.provisional_surrogate
    # Clause A: the real value never crossed egress -- only its surrogate did.
    assert real_value not in recorded_1[0].content.decode("utf-8")
    assert surrogate in recorded_1[0].content.decode("utf-8")

    # --- Step 3/4: confirm's writes are ciphertext, found via the blind index ---
    # Issue #346: _StubAdjudicator's verdict is untyped (entity_type=None), which
    # _entity_kind_for now maps to "term" rather than a confident "person" claim
    # -- confirm's write lands in the terms table, not persons.
    expected_blind_index = cipher.blind_index(real_value)
    with connect(dsn) as conn:
        row = conn.execute(
            "SELECT canonical_name_ciphertext FROM terms WHERE canonical_name_blind_index = %s",
            (expected_blind_index,),
        ).fetchone()
    assert row is not None
    term_ciphertext = row[0]
    # Clause G: opaque ciphertext, never the plaintext real value.
    assert term_ciphertext != real_value
    assert cipher.decrypt(term_ciphertext) == real_value

    reidentify_ciphertext = await reidentify_store_1.surrogate_to_ciphertext(
        surrogate, DEFAULT_WORKSPACE
    )
    assert reidentify_ciphertext is not None
    assert reidentify_ciphertext != real_value
    assert cipher.decrypt(reidentify_ciphertext) == real_value

    # --- Step 5: restart -- fresh store instances against the SAME dsn, and a
    # fresh SurrogateMapping rehydrated the way app.py's real startup wiring
    # hydrates it (mirrors the established restart-simulation convention: a fresh
    # object built against the same DSN, e.g. test_postgres_reidentify_store.py's
    # store1/store2 pair -- never a spawned OS process). ------------------------
    entity_graph_2 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    reidentify_store_2 = PostgresReIdentificationStore(dsn)
    mapping_2 = SurrogateMapping.from_pairs([])
    hydrate_mapping_from_reidentify_store(mapping_2, reidentify_store_2, cipher)
    inbox_2 = ReviewInbox()
    adjudicator_2 = _StubAdjudicator(confirm={real_value})
    recorded_2: list[httpx.Request] = []
    scripted = _ack_response(text=f"Contacting {surrogate} now.")

    app.dependency_overrides[get_upstream_client] = lambda: _stub_upstream(
        scripted, recorded_2
    )
    app.dependency_overrides[get_mapping] = lambda: mapping_2
    app.dependency_overrides[get_review_inbox] = lambda: inbox_2
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(adjudicator_2)
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph_2
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store_2
    app.dependency_overrides[get_mapping_cipher] = lambda: cipher
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            # Step 6: send the same entity again.
            resend_resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": f"Please brief {real_value} tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resend_resp.status_code == 200
    # Clause E: the same real value maps to the SAME surrogate after the restart
    # -- detected deterministically by L2 from the rehydrated mapping (the real
    # value itself is substituted before the L3 candidate pass ever runs, so it
    # never becomes a candidate span; L3 is still legitimately asked about *other*
    # unresolved tokens in the already-blindfolded text, including the injected
    # surrogate's own word fragments -- the ADR-0022/issue #68 guard this mapping
    # state also feeds is what stops those from being re-minted, proven by the
    # empty inbox below, not by L3 going uncalled).
    assert real_value not in adjudicator_2.calls
    assert inbox_2.list() == []
    egressed = recorded_2[0].content.decode("utf-8")
    assert real_value not in egressed
    assert surrogate in egressed

    # Step 7 / clauses B+D: the client-visible response restores the surrogate the
    # (stub) provider echoed back to the real value -- closed-world restore still
    # works with a freshly-rehydrated mapping/session, no leftover state from the
    # pre-restart process.
    restored_text = resend_resp.json()["content"][0]["text"]
    assert surrogate not in restored_text
    assert real_value in restored_text


# ---------------------------------------------------------------------------
# Focused unit coverage for the new hydration seam itself (mirrors
# test_review_inbox_persistence.py's direct hydrate_review_inbox_from_store
# tests) -- the round trip above proves the property end to end; these pin
# hydrate_mapping_from_reidentify_store's own contract in isolation.
# ---------------------------------------------------------------------------


def test_hydrate_mapping_from_reidentify_store_is_a_no_op_when_cipher_is_none():
    from blindfold.app import hydrate_mapping_from_reidentify_store
    from blindfold.reidentify import InMemoryReIdentificationStore
    from blindfold.surrogates import SurrogateMapping

    store = InMemoryReIdentificationStore()
    store.seed("Alex Brenner", "acme", "opaque-ciphertext")
    mapping = SurrogateMapping.from_pairs([])

    hydrate_mapping_from_reidentify_store(mapping, store, None)  # must not raise

    assert mapping.real_values() == []


def test_hydrate_mapping_from_reidentify_store_seeds_every_persisted_pair():
    from blindfold.app import hydrate_mapping_from_reidentify_store
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.reidentify import InMemoryReIdentificationStore
    from blindfold.surrogates import SurrogateMapping

    cipher = _local_key_cipher()
    store = InMemoryReIdentificationStore()
    store.seed("Alex Brenner", "acme", cipher.encrypt("Kestrelholt"))
    store.seed("Berta Falke", "acme", cipher.encrypt("Torvald Lindqvist"))
    mapping = SurrogateMapping.from_pairs([])

    hydrate_mapping_from_reidentify_store(mapping, store, cipher)

    assert set(mapping.real_values()) == {"Kestrelholt", "Torvald Lindqvist"}
    assert mapping.is_known_surrogate("Alex Brenner")
    assert mapping.is_known_surrogate("Berta Falke")


def test_hydrate_mapping_from_reidentify_store_raises_named_error_when_cipher_cannot_decrypt():
    """issue #364: a mapping cipher that cannot decrypt a persisted entry must
    refuse hydration with a named, scrubbed error -- never an unnamed KeyError
    escaping from inside the cipher's own response-shape indexing (the #343
    web-verify repro: the fixture's OpenBao stub answers every POST, including
    decrypt, with an encrypt-shaped body).
    """
    from blindfold.app import MappingHydrationError, hydrate_mapping_from_reidentify_store
    from blindfold.reidentify import InMemoryReIdentificationStore
    from blindfold.surrogates import SurrogateMapping
    from blindfold.transit import TransitClient

    def handler(request: httpx.Request) -> httpx.Response:
        # Always answers with an encrypt-shaped body, never a decrypt-shaped one --
        # mirrors the broken fixture stub this issue's traceback came from.
        return httpx.Response(200, json={"data": {"ciphertext": "vault:v1:stub"}})

    cipher = TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    store = InMemoryReIdentificationStore()
    store.seed("Alex Brenner", "acme", "vault:v1:some-ciphertext")
    mapping = SurrogateMapping.from_pairs([])

    with pytest.raises(MappingHydrationError) as excinfo:
        hydrate_mapping_from_reidentify_store(mapping, store, cipher)

    message = str(excinfo.value)
    assert "some-ciphertext" not in message
    assert mapping.real_values() == []


def test_in_memory_reidentification_store_all_entries_returns_every_seeded_triple():
    from blindfold.reidentify import InMemoryReIdentificationStore

    store = InMemoryReIdentificationStore()
    store.seed("Alex Brenner", "acme", "ciphertext-a")
    store.seed("Berta Falke", "other-ws", "ciphertext-b")

    assert set(store.all_entries()) == {
        ("Alex Brenner", "acme", "ciphertext-a"),
        ("Berta Falke", "other-ws", "ciphertext-b"),
    }
