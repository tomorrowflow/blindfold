"""Learn-time disjointness (issue #81, parent #79): retire active surrogates
invalidated by newly learned entities.

Issue #80 (mint-time disjointness) rejects a *candidate* surrogate that contains a
token of an entity already known at mint time. This closes the same collision from
the other direction: an entity (or Variation) learned **later** -- via the learning
loop's confirm (ADR-0010) or a curation edit that folds a canonical name/Variation
into the closed-world set (e.g. merge, ADR-0016) -- can token-overlap a surrogate
that is already active for a *different* referent. Left alone, the next time that
stale surrogate is injected, the pre-egress leak gate (``engine.leak_gate``) would
see the newly-known real value as a substring of the outbound text and fail-close a
request that carries nothing but known entities and their own surrogates.

Leak-audit clauses exercised:
- A: the stub upstream receives only surrogates -- the affected referent's next
  exchange must not egress the stale, now-colliding surrogate.
- B/ADR-0005: the retired surrogate still restores to the same real referent for
  exchanges that already injected it (closed-world restore, session-scoped).
- D: the verify pass (leak gate) stays clean on the affected referent's next request.

N/A this slice: C (closed-world restore semantics themselves are untouched); E
(reserved-namespace PII, unrelated mint path); F (fail-closed policy, unrelated);
G (mapping secrecy, unrelated -- issue #3/#10 deferral stands).
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_entity_graph,
    get_l3_detector,
    get_mapping,
    get_rbac,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.engine import ExchangeSession, restore_response
from blindfold.entity_graph import EntityGraph
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.policy import AuditLog
from blindfold.rbac import RbacRegistry
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


def test_learning_a_new_real_value_retires_a_colliding_active_surrogate():
    # "Ingrid Falke" was already active with surrogate "Klaus Berger" -- fine,
    # until "Klaus" is learned as a real value in its own right (e.g. a
    # different referent confirmed via the learning loop). "Klaus Berger" now
    # contains a known-entity token and must be retired and replaced.
    mapping = SurrogateMapping()
    mapping.seed("Ingrid Falke", "Klaus Berger")

    mapping.seed("Klaus", "Provisional Surrogate 0")

    new_surrogate = mapping.surrogate_for("Ingrid Falke")
    assert new_surrogate != "Klaus Berger"
    assert new_surrogate is not None
    assert "Klaus" not in new_surrogate

    # The retired surrogate stays recognized (ADR-0005): the engine must not
    # re-blindfold it as a fresh novel candidate if it's ever encountered again
    # (e.g. carried over from a past exchange transcript).
    assert mapping.is_known_surrogate("Klaus Berger")


def test_retirement_does_not_disturb_restore_of_a_past_exchange_that_already_injected_the_stale_surrogate():
    # ADR-0005 restorability of past exchanges: closed-world restore (ADR-0006)
    # keys off the exchange's OWN ``ExchangeSession.injected`` snapshot, never the
    # live mapping -- so retiring "Klaus Berger" for future blindfold passes must
    # not disturb a response that had already injected it for a past exchange.
    mapping = SurrogateMapping()
    mapping.seed("Ingrid Falke", "Klaus Berger")

    # A past exchange injected the (soon-to-be-stale) surrogate for its own hop.
    past_session = ExchangeSession()
    past_session.record("Klaus Berger", "Ingrid Falke")

    # Learning "Klaus" retires "Klaus Berger" and re-mints for "Ingrid Falke".
    mapping.seed("Klaus", "Provisional Surrogate 0")
    assert mapping.surrogate_for("Ingrid Falke") != "Klaus Berger"

    response = {"content": [{"type": "text", "text": "Notified Klaus Berger."}]}
    restored = restore_response(response, past_session)

    assert restored["content"][0]["text"] == "Notified Ingrid Falke."


class _StubAdjudicator:
    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=candidate.text in self._confirm)


def _make_stub_upstream(responses: list[dict], recorded: list[httpx.Request]):
    # ``responses`` is read by call index so the test can append a later turn's
    # scripted reply (e.g. echoing back a surrogate only known once minted)
    # after earlier turns have already been sent.
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=responses[len(recorded) - 1])

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_confirming_a_novel_entity_retires_a_stale_colliding_surrogate_so_the_affected_referent_round_trips_200():
    # AC (issue #81): "Ingrid Falke" is already active with surrogate "Klaus
    # Berger" -- fine, until the learning loop confirms a *different* referent,
    # "Klaus", as a real entity. "Klaus Berger" now token-overlaps a known
    # entity and would trip the pre-egress leak gate the next time it's
    # injected for "Ingrid Falke". Confirming must retire it immediately, and
    # the next exchange mentioning "Ingrid Falke" must round-trip 200 with the
    # replacement surrogate -- no spurious leak_detected from the stale one.
    mapping = SurrogateMapping()
    mapping.seed("Ingrid Falke", "Klaus Berger")
    inbox = ReviewInbox()
    adjudicator = _StubAdjudicator(confirm={"Klaus"})
    detector = L3Detector(adjudicator)

    recorded: list[httpx.Request] = []
    responses: list[dict] = [
        {"content": [{"type": "text", "text": "Acknowledged."}]}
    ]
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        responses, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            # Turn 1: novel "Klaus" is L3-confirmed and lands in the inbox.
            turn_one = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "Notify Klaus."}],
                },
            )
            assert turn_one.status_code == 200

            item_id = inbox.list()[0].id
            confirm_resp = await client.post(
                f"/v1/management/review-inbox/{item_id}/confirm"
            )
            assert confirm_resp.status_code == 200

            # "Ingrid Falke"'s active surrogate must have been retired + replaced.
            replacement = mapping.surrogate_for("Ingrid Falke")
            assert replacement != "Klaus Berger"

            # The provider echoes back the surrogate it egressed, so restore can
            # be asserted below (mirrors the "echo upstream" oracle in #79/#80's
            # own tests).
            responses.append(
                {"content": [{"type": "text", "text": f"Notified {replacement}."}]}
            )

            # Turn 2: a request mentioning ONLY the affected referent must not
            # fail-close on the stale collision.
            turn_two = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please notify Ingrid Falke."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert turn_two.status_code == 200, turn_two.text

    # Clause A: only the (new) surrogate egressed -- never the real value, and
    # never the stale surrogate that would leak "Klaus".
    turn_two_egress = recorded[-1].content.decode("utf-8")
    assert "Ingrid Falke" not in turn_two_egress
    assert "Klaus Berger" not in turn_two_egress
    assert "Klaus" not in turn_two_egress
    assert replacement in turn_two_egress

    # Clause B: the client still sees the real value restored.
    body = turn_two.json()
    assert "Ingrid Falke" in body["content"][0]["text"]


@pytest.mark.anyio
async def test_merge_folding_a_variation_into_the_closed_world_retires_a_stale_colliding_surrogate():
    # AC (issue #81), the "curation edit" entry point: a merge folds the
    # winner's own Variation ("Klaus", added at curation time via add_entity,
    # never separately seeded into the mapping) into the closed-world set for
    # the first time. That Variation happens to token-overlap a DIFFERENT,
    # already-active surrogate ("Klaus Berger", for the unrelated "Ingrid
    # Falke") -- the merge-sync seed() call must retire and replace it.
    mapping = SurrogateMapping()
    mapping.seed("Ingrid Falke", "Klaus Berger")

    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    graph = EntityGraph()
    graph.add_entity(
        kind="person",
        workspace="acme",
        canonical_name="Bertram Klaus",
        variations=["Klaus"],
        surrogate="Winner Surrogate",
    )
    graph.add_entity(
        kind="person",
        workspace="acme",
        canonical_name="Norbert Fischer",
        surrogate="Loser Surrogate",
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/management/entities/merge",
                json={
                    "workspace": "acme",
                    "winner": {"kind": "person", "canonical_name": "Bertram Klaus"},
                    "loser": {"kind": "person", "canonical_name": "Norbert Fischer"},
                },
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text

    replacement = mapping.surrogate_for("Ingrid Falke")
    assert replacement != "Klaus Berger"
    assert "Klaus" not in replacement
    assert mapping.is_known_surrogate("Klaus Berger")
