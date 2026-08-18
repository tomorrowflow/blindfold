"""Symmetric mint-time disjointness (issue #333, ADR-0052 amendment #328/decision 4):
mint-time ``known_values`` in ``engine.py`` consults the leak gate's own value set
via :func:`blindfold.engine._provisional_pair_map`, not just the inbox's provisional
*surrogates*.

ADR-0051's invariant is written about two sets that must be equal: what the
deterministic blinder rewrites and what the leak gate checks. #328 found a third set
that must equal them and did not -- the set consulted when a **provisional surrogate**
is minted. Mint-time ``known_values`` was ``mapping.real_values()`` plus the inbox's
provisional *surrogates*; the inbox's provisional *reals* were absent, even though
``leak_gate`` has checked them since #287. So Blindfold could issue a surrogate
containing a value the gate would then block on -- every subsequent request failing
closed until a human clears the row.

#330 (opaque reserved fallback) and #331 (bounded fail-closed walk) are hard
prerequisites, already merged: #330 makes the *previously* natural-language fallback
label opaque so widening the collision set here can terminate at all, and #331 turns
any residual non-termination into a diagnosable ``ProvisionalPoolExhaustedError``
instead of a hang.

#332 (word-boundary ``_real_value_pattern`` matching for
``store._mint.collides_with_known_entity``) has **not** merged as of this slice
(trusted-maintainer comment on #333). This slice therefore widens *what* is checked
(known_values now includes the inbox's provisional reals, drawn from the shared
``_provisional_pair_map`` derivation) but leaves *how* it is checked
(``collides_with_known_entity``'s raw substring test) untouched -- deferred to #332.

Leak-audit clauses exercised:
- A: the stub upstream receives only surrogates -- a pool entry that would itself
  trip the leak gate (because it token-contains an already-live provisional real)
  is never issued to any referent in the first place.
- D: the verify pass (leak gate) stays clean on a request that mints several
  provisional referents across hops, where an early hop's provisional real
  collides with a later hop's plausible named-pool entry.
- F: fail-closed is honored -- a provisional real that collides with every fallback
  candidate reaches #331's bounded exhaustion and returns a diagnosable error rather
  than hanging.

N/A this slice: B/C (restore/closed-world semantics untouched); E (reserved-namespace
PII, unrelated mint path); G (mapping secrecy, unrelated).
"""

from __future__ import annotations

import time

import httpx
import pytest

from blindfold.app import (
    app,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.engine import blindfold_payload
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ProvisionalPoolExhaustedError, ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _ConfirmSet:
    """Confirms exactly the candidate texts named in ``confirm``."""

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=candidate.text in self._confirm)


def _hops(*tokens: str) -> list[dict]:
    return [{"role": "user", "content": f"Please brief {token}."} for token in tokens]


def test_a_provisional_real_minted_in_an_earlier_hop_blocks_a_colliding_named_pool_entry_in_a_later_hop():
    # AC (issue #333): #330 alone does not close this hole -- a provisional real
    # minted in an early hop ("Fink") is invisible to a later hop's corpus check,
    # because ``pool_entry_collides_with_corpus`` only ever sees the text of the
    # hop it is called on. The plausible named pool's position 4, "Emil Fink",
    # word-boundary-contains "Fink" -- issuing it to an unrelated referent five
    # hops later is exactly the surrogate-contains-a-known-value collision
    # ``leak_gate`` already blocks on (ADR-0051's third-set asymmetry). Mint-time
    # disjointness must see "Fink" as a known value regardless of which hop
    # minted it, and skip that pool entry rather than issue it.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    confirm = {"Fink", "Novala", "Novalb", "Novalc", "Novald"}
    detector = L3Detector(_ConfirmSet(confirm))

    payload = {
        "model": "m",
        "messages": _hops("Fink", "Novala", "Novalb", "Novalc", "Novald"),
    }

    blindfold_payload(payload, mapping, detector, inbox)

    surrogates = [item.provisional_surrogate for item in inbox.list()]
    assert "Emil Fink" not in surrogates


def _inbox_with_exhausted_person_pool_and_a_universal_collision() -> ReviewInbox:
    # Consume all 8 named "person" pool slots, then mint one more provisional
    # real ("BF") that -- under today's raw-substring ``collides_with_known_entity``
    # (issue #332 not yet merged, per the trusted-maintainer comment on #333) --
    # is a bare substring of every opaque ``BFX{NNNN}`` fallback candidate past
    # the named pool. This is the documented, expected residual of that ordering
    # (not a defect of this issue): a short provisional real that happens to be a
    # substring of the reserved prefix drives the walk to #331's bound. #332
    # (word-boundary matching) removes the cause; this test asserts the bounded,
    # loud fail-closed shape that stands until it merges.
    inbox = ReviewInbox()
    for i in range(8):
        inbox.upsert(real=f"Filler Real {i}", context=f"context {i}")
    inbox.upsert(real="BF", context="ctx")
    return inbox


def _make_stub_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_a_provisional_real_universally_colliding_with_the_fallback_fails_closed_instead_of_hanging():
    # AC (issue #333): termination is asserted, not assumed. Widening mint-time
    # known_values to include the inbox's provisional REALS (this issue's change)
    # must still terminate -- reaching #331's bounded
    # ``ProvisionalPoolExhaustedError`` -- rather than resurrecting ADR-0052's
    # run-8 hang, now via a provisional real established in the NEW direction
    # (real live first, colliding surrogate candidate second) instead of the
    # already-guarded old one.
    mapping = SurrogateMapping()
    inbox = _inbox_with_exhausted_person_pool_and_a_universal_collision()
    detector = L3Detector(_ConfirmSet({"Klaus"}))

    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            start = time.monotonic()
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please brief Klaus tomorrow."}
                    ],
                },
            )
            elapsed = time.monotonic() - start
    finally:
        app.dependency_overrides.clear()

    # The measured ADR-0052 run-8 hang never returned after 3 seconds -- a
    # bounded walk must return well inside that.
    assert elapsed < 3
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "blindfold_fail_closed"
    assert body["error"]["sub_reason"] == "provisional_pool_exhausted"
    assert "Klaus" not in resp.text
    # Clause A: blocked before any egress -- the stub upstream saw nothing.
    assert recorded == []


@pytest.mark.anyio
async def test_symmetric_refusal_for_the_same_real_surrogate_collision_pair_regardless_of_establishment_order():
    # AC (issue #333): "The two orderings are now symmetric: ... minting is
    # refused regardless of which of the two was established first." Uses the
    # SAME collision pair -- real "Fink" / surrogate "Emil Fink" (named-pool
    # position 4, which word-boundary-contains "Fink") -- established both ways:
    #
    # Order A (this issue's fix, new direction): "Fink" is live as a
    # provisional real FIRST; a later referent's mint must never be assigned
    # the colliding pool entry "Emil Fink" at all -- so it never even reaches
    # egress. Exercised above by
    # ``test_a_provisional_real_minted_in_an_earlier_hop_blocks_a_colliding_named_pool_entry_in_a_later_hop``
    # (a shorter same-request variant is inlined below for direct comparison).
    #
    # Order B (already guarded before this issue, ADR-0052's table row 1):
    # "Emil Fink" is already live as a CONFIRMED entity's surrogate FIRST; a
    # later, genuinely novel real "Fink" is then L3-confirmed for a *different*
    # referent. Blindfold cannot refuse an L3-confirmed real outright (that is
    # the fail-open trade ADR-0050 already rejected) -- but the request must
    # still fail closed rather than let "Fink" egress as a plaintext
    # word-boundary substring of the already-live "Emil Fink". This half needs
    # no new code: ``leak_gate`` has checked confirmed entities' surrogates
    # against provisional reals since #287/ADR-0051; it is asserted here,
    # alongside order A, so the symmetry claim is checked with one literal
    # pair rather than taken on faith from two separately-scoped test files.
    # "Fink" established first, then three unrelated referents advance the
    # pool cursor up to position 4 -- exactly the scenario the earlier test in
    # this module pins; repeated here as one HTTP round trip so the whole
    # request round-trips 200 with a clean egress, not just an inbox-state
    # assertion.
    mapping_a = SurrogateMapping()
    inbox_a = ReviewInbox()
    detector_a = L3Detector(
        _ConfirmSet({"Fink", "Novala", "Novalb", "Novalc", "Novald"})
    )

    recorded_a: list[httpx.Request] = []
    app.dependency_overrides[get_mapping] = lambda: mapping_a
    app.dependency_overrides[get_review_inbox] = lambda: inbox_a
    app.dependency_overrides[get_l3_detector] = lambda: detector_a
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded_a)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp_a = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": _hops("Fink", "Novala", "Novalb", "Novalc", "Novald"),
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp_a.status_code == 200, resp_a.text
    surrogates_a = [item.provisional_surrogate for item in inbox_a.list()]
    assert "Emil Fink" not in surrogates_a
    egress_a = "".join(r.content.decode("utf-8") for r in recorded_a)
    assert "Emil Fink" not in egress_a

    # Order B: "Emil Fink" already live as a confirmed entity's surrogate, then
    # "Fink" L3-confirmed as a different, genuinely novel referent in the SAME
    # request -- both referents mentioned so the collision can actually reach
    # egressable text.
    mapping_b = SurrogateMapping.from_pairs([("Someone Else", "Emil Fink")])
    detector_b = L3Detector(_ConfirmSet({"Fink"}))

    recorded_b: list[httpx.Request] = []
    app.dependency_overrides[get_mapping] = lambda: mapping_b
    app.dependency_overrides[get_l3_detector] = lambda: detector_b
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded_b)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp_b = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Please brief Someone Else and separately "
                                "notify Fink tomorrow."
                            ),
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp_b.status_code == 503, resp_b.text
    assert resp_b.json()["error"]["sub_reason"] == "leak_detected"
    assert recorded_b == []
