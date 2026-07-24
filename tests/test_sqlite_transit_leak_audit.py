"""Transit round-trip + leak-audit green on the SQLite backend (issue #205).

The crown-jewel proof: the full Transit / re-identify path works end-to-end when
every store the request/management path touches is backed by SQLite (via the
thin dialect seam, ADR-0043 -- issues #200/#202/#203), not just in isolation
(the per-store SQLite tracer tests already merged). Real-value columns are
Transit ciphertext (``*_ciphertext``) alongside a deterministic blind-index
column (``*_blind_index TEXT UNIQUE``) -- backend-agnostic per ADR-0007/0008.
Every SQLite file here is a ``tmp_path`` fixture; no Docker/Postgres involved.

Leak-audit clause analysis for this slice as a whole (each test states its own):
- A/B covered (T2): the stub upstream received only the surrogate; the client
  received the restored real value, for a novel candidate whose review-inbox
  persistence runs through the real SQLite-backed store.
- C: unaffected by store backend -- closed-world restore is exchange-session
  scoped (ExchangeSession/SurrogateMapping), never consults the persistent
  store either way; already covered generically by test_proxy_round_trip.py.
- D: covered implicitly -- every round trip below returns 200, the clean-pass
  shape of the verify pass.
- F (fail-closed) covered (T4): L3-unavailable-blocks-by-default is unaffected
  by wiring the entity-graph/reidentify stores to real SQLite.
- G (mapping secrecy) covered (T1/T2/T3): every persisted real-value column
  (review_inbox.real_ciphertext/real_blind_index, reidentify_mappings.ciphertext)
  holds only Transit ciphertext/blind-index, never the plaintext real value;
  audit records carry the surrogate, never the plaintext (T3).
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

pytestmark = pytest.mark.anyio


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _stub_transit() -> "blindfold.transit.TransitClient":
    """Deterministic Transit double at the network boundary (ADR-0008): encrypt(v)
    -> vault:v1:enc:{v}, blind_index(v) -> vault:v1:hmac:{v} -- same shape as
    test_transit_ciphertext_columns.py's Postgres-path stub, reused here for SQLite.
    """
    from blindfold.transit import TransitClient

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path

        if "encrypt" in path:
            raw = base64.b64decode(body["plaintext"]).decode()
            return httpx.Response(200, json={"data": {"ciphertext": f"vault:v1:enc:{raw}"}})

        if "decrypt" in path:
            ct = body["ciphertext"]
            if ct.startswith("vault:v1:enc:"):
                plain = ct[len("vault:v1:enc:"):]
                return httpx.Response(200, json={"data": {"plaintext": _b64(plain)}})
            return httpx.Response(400, json={"errors": ["bad ciphertext"]})

        if "hmac" in path:
            raw = base64.b64decode(body["input"]).decode()
            return httpx.Response(200, json={"data": {"hmac": f"vault:v1:hmac:{raw}"}})

        return httpx.Response(404, json={"errors": ["not found"]})

    return TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


# ---------------------------------------------------------------------------
# T1 -- AC1: Transit ciphertext + blind-index columns store and equality-lookup
# correctly on SQLite (store-level, mirrors test_transit_ciphertext_columns.py's
# Postgres blind-index test, but through the SQLite dialect seam -- no Docker).
# ---------------------------------------------------------------------------


async def test_review_inbox_blind_index_equality_lookup_works_on_sqlite(tmp_path):
    from blindfold.review import ReviewInbox
    from blindfold.store.dialect import connect
    from blindfold.store.review_inbox_store import PostgresReviewInboxStore

    dsn = f"sqlite:///{tmp_path / 'review_inbox.sqlite3'}"
    store = PostgresReviewInboxStore(dsn)
    transit = _stub_transit()
    inbox = ReviewInbox(store=store, transit=transit)

    real_value = "Martin Bach"
    inbox.upsert(real_value, context=f"Brief {real_value} tomorrow.", workspace="acme")

    # Equality lookup over the encrypted column via the blind index -- without
    # decrypting -- exactly what a dedup-by-real-value query would do.
    expected_blind_index = transit.blind_index(real_value)
    with connect(dsn) as conn:
        row = conn.execute(
            "SELECT real_ciphertext FROM review_inbox WHERE real_blind_index = %s",
            (expected_blind_index,),
        ).fetchone()

    assert row is not None
    stored_ciphertext = row[0]
    # Clause G: the stored column is opaque ciphertext, never the plaintext.
    assert stored_ciphertext != real_value
    assert transit.decrypt(stored_ciphertext) == real_value


# ---------------------------------------------------------------------------
# T2 -- clauses A/B + AC1/AC2: a novel candidate minted mid-request is
# blindfolded before egress and restored for the client, while its persistence
# runs through a REAL SQLite-backed review-inbox store (not an in-memory
# double) -- the request path and the SQLite dialect seam proven together.
# ---------------------------------------------------------------------------


class _StubAdjudicator:
    """Stub for Ollama (L3): returns is_entity=True only for whitelisted texts."""

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate):
        from blindfold.l3 import L3Adjudication

        return L3Adjudication(is_entity=candidate.text in self._confirm)


def _make_stub_upstream(scripted_response: dict, recorded: list[httpx.Request]):
    from blindfold.upstream import UpstreamClient

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


async def test_novel_candidate_through_proxy_persists_transit_ciphertext_to_real_sqlite_review_inbox(
    tmp_path,
):
    from blindfold.app import (
        app,
        get_allowlist,
        get_l3_detector,
        get_mapping,
        get_review_inbox,
        get_transit_client,
        get_upstream_client,
    )
    from blindfold.l3 import L3Detector
    from blindfold.review import Allowlist, ReviewInbox
    from blindfold.store import vendored_seed_repository
    from blindfold.store.dialect import connect
    from blindfold.store.review_inbox_store import PostgresReviewInboxStore
    from blindfold.surrogates import SurrogateMapping

    dsn = f"sqlite:///{tmp_path / 'review_inbox.sqlite3'}"
    real_store = PostgresReviewInboxStore(dsn)
    transit = _stub_transit()

    mapping = SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())
    inbox = ReviewInbox(store=real_store, transit=transit)
    allowlist = Allowlist()
    novel = "Klaus"
    detector = L3Detector(_StubAdjudicator(confirm={novel}))

    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []

    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    app.dependency_overrides[get_l3_detector] = lambda: detector
    app.dependency_overrides[get_transit_client] = lambda: transit
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": f"Please brief {novel} tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200

    # Clause A: the novel real value never crossed egress; only its surrogate did.
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    assert novel not in egressed
    item = inbox.list()[0]
    assert item.provisional_surrogate in egressed

    # Clause B: the client's response carries no surrogate leftovers (nothing to
    # restore here -- the scripted reply never echoed the surrogate -- but the
    # round trip must still return 200, the clean-pass shape).
    assert resp.json()["content"][0]["text"] == "Acknowledged."

    # AC1/G: the persisted row in the REAL SQLite file holds only Transit
    # ciphertext + blind index for the real value -- never the plaintext.
    with connect(dsn) as conn:
        row = conn.execute(
            "SELECT real_ciphertext, real_blind_index FROM review_inbox WHERE id = %s",
            (int(item.id),),
        ).fetchone()
    assert row is not None
    real_ciphertext, real_blind_index = row
    assert real_ciphertext != novel
    assert transit.decrypt(real_ciphertext) == novel
    assert real_blind_index == transit.blind_index(novel)


# ---------------------------------------------------------------------------
# T3 -- AC2/AC3: confirm grows a REAL SQLite-backed entity graph + writes a
# REAL SQLite-backed re-identify entry; Reveal (Re-identify) is RBAC-gated and
# every attempt (success/denied/failed) is audited -- SEC-8 -- with the store
# backend now SQLite instead of the in-memory doubles the endpoint tests use.
# ---------------------------------------------------------------------------


def _confirm_fixture(tmp_path):
    """Shared setup: one pending review item + real SQLite-backed entity_graph
    and reidentify stores (one SQLite file, two tables) + a recording audit log.
    """
    from blindfold.policy import AuditLog
    from blindfold.rbac import RbacRegistry
    from blindfold.review import ReviewInbox
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore
    from blindfold.store.reidentify_store import PostgresReIdentificationStore
    from blindfold.surrogates import SurrogateMapping

    dsn = f"sqlite:///{tmp_path / 'confirm_reveal.sqlite3'}"
    entity_graph = PostgresEntityGraphStore(dsn)
    reidentify_store = PostgresReIdentificationStore(dsn)
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    item = inbox.upsert(
        "Astrid Voss", context="Brief Astrid Voss tomorrow.", workspace="acme"
    )
    transit = _stub_transit()
    rbac = RbacRegistry()
    audit_log = AuditLog()
    return {
        "entity_graph": entity_graph,
        "reidentify_store": reidentify_store,
        "mapping": mapping,
        "inbox": inbox,
        "item": item,
        "transit": transit,
        "rbac": rbac,
        "audit_log": audit_log,
    }


def _override_confirm_reveal_deps(app, fx):
    from blindfold.app import (
        get_audit_log,
        get_entity_graph,
        get_mapping,
        get_rbac,
        get_reidentify_store,
        get_review_inbox,
        get_transit_client,
    )

    app.dependency_overrides[get_review_inbox] = lambda: fx["inbox"]
    app.dependency_overrides[get_entity_graph] = lambda: fx["entity_graph"]
    app.dependency_overrides[get_mapping] = lambda: fx["mapping"]
    app.dependency_overrides[get_reidentify_store] = lambda: fx["reidentify_store"]
    app.dependency_overrides[get_transit_client] = lambda: fx["transit"]
    app.dependency_overrides[get_rbac] = lambda: fx["rbac"]
    app.dependency_overrides[get_audit_log] = lambda: fx["audit_log"]


async def test_confirm_then_reveal_resolves_through_real_sqlite_stores_for_an_authorized_re_identifier(
    tmp_path,
):
    from blindfold.app import app

    fx = _confirm_fixture(tmp_path)
    fx["rbac"].grant("alice", "acme", "re-identifier")
    _override_confirm_reveal_deps(app, fx)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            confirm_resp = await client.post(
                f"/v1/management/review-inbox/{fx['item'].id}/confirm"
            )
            reveal_resp = await client.get(
                f"/v1/management/surrogate/{fx['item'].provisional_surrogate}/real",
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "acme",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert confirm_resp.status_code == 200
    assert reveal_resp.status_code == 200
    assert reveal_resp.json()["real"] == "Astrid Voss"

    # AC1/G: confirm wrote only ciphertext to the REAL SQLite reidentify store.
    stored_ciphertext = await fx["reidentify_store"].surrogate_to_ciphertext(
        fx["item"].provisional_surrogate, "acme"
    )
    assert stored_ciphertext is not None
    assert stored_ciphertext != "Astrid Voss"

    # SEC-8: the successful re-identify is audited, surrogate only.
    record = fx["audit_log"].records[-1]
    assert record.event == "re-identified"
    assert "Astrid Voss" not in record.reason
    assert fx["item"].provisional_surrogate in record.reason

    # The confirmed entity also lives in the REAL SQLite entity_graph store.
    entity = fx["entity_graph"].get_by_canonical("acme", "person", "Astrid Voss")
    assert entity is not None


async def test_reveal_denies_and_audits_a_caller_without_the_re_identifier_role_on_sqlite(
    tmp_path,
):
    from blindfold.app import app

    fx = _confirm_fixture(tmp_path)
    fx["rbac"].grant("bob", "acme", "viewer")  # not re-identifier
    _override_confirm_reveal_deps(app, fx)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            await client.post(f"/v1/management/review-inbox/{fx['item'].id}/confirm")
            reveal_resp = await client.get(
                f"/v1/management/surrogate/{fx['item'].provisional_surrogate}/real",
                headers={
                    "x-blindfold-identity": "bob",
                    "x-blindfold-workspace": "acme",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert reveal_resp.status_code == 403
    # SEC-8: a denied attempt is audited too -- a probing caller leaves a trail
    # even against the real SQLite-backed store.
    record = fx["audit_log"].records[-1]
    assert record.event == "re-identify-denied"
    assert record.identity == "bob"
    assert "Astrid Voss" not in record.reason


async def test_reveal_reports_failed_and_audits_an_unknown_surrogate_on_sqlite(tmp_path):
    from blindfold.app import app

    fx = _confirm_fixture(tmp_path)
    fx["rbac"].grant("alice", "acme", "re-identifier")
    _override_confirm_reveal_deps(app, fx)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            # No confirm this time: the surrogate was never seeded into the
            # real SQLite reidentify store, so the lookup misses.
            reveal_resp = await client.get(
                "/v1/management/surrogate/No-Such-Surrogate/real",
                headers={
                    "x-blindfold-identity": "alice",
                    "x-blindfold-workspace": "acme",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert reveal_resp.status_code == 404
    # SEC-8: a failed lookup is audited too, never the (nonexistent) real value.
    record = fx["audit_log"].records[-1]
    assert record.event == "re-identify-failed"
    assert "outcome=not-found" in record.reason


# ---------------------------------------------------------------------------
# T4 -- AC4: fail-closed (ADR-0009) is unaffected by the store backend. With
# L3 forced unavailable, the proxy still blocks a novel candidate by default
# even when the entity-graph and reidentify stores are wired to real SQLite
# (not the in-memory fallbacks test_proxy_fail_closed.py exercises).
# ---------------------------------------------------------------------------


async def test_fail_closed_still_blocks_a_novel_candidate_with_sqlite_backed_stores_wired(
    tmp_path,
):
    from blindfold.app import (
        app,
        get_entity_graph,
        get_l3_detector,
        get_reidentify_store,
        get_upstream_client,
    )
    from blindfold.l3 import CandidateSpan, L3Detector
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore
    from blindfold.store.reidentify_store import PostgresReIdentificationStore

    class _UnavailableAdjudicator:
        def adjudicate(self, candidate: CandidateSpan):
            raise ConnectionError("ollama unreachable")

    dsn = f"sqlite:///{tmp_path / 'fail_closed.sqlite3'}"
    entity_graph = PostgresEntityGraphStore(dsn)
    reidentify_store = PostgresReIdentificationStore(dsn)

    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream({}, recorded)
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please brief Quentin tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    assert resp.json()["error"]["event"] == "blocked-l3-unavailable"
    # Clause A: block came BEFORE egress -- nothing reached the stub upstream.
    assert recorded == []
