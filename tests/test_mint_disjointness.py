"""Mint-time disjointness (issue #80, parent #79): no surrogate may contain a
known-entity variation token.

Root cause (found by the #68/#69/#75/#76 live-verify, 2026-07-09): the seed-pool mint
(``store/_mint.py``) and the provisional-pool mint (``review.py``, L3 learning loop)
draw plausible-name surrogates from fixed pools by raw position, with nothing checking
a candidate against the known-entity graph (canonical names + variations) -- the same
closed-world set the pre-egress leak gate (``engine.leak_gate``) consults via
``mapping.real_values()``. A surrogate that happens to contain a known variation as a
substring is un-egressable by construction: the (correctly stricter) leak gate always
fires on it, fail-closing a request that carries nothing but seeded/confirmed real
entities and their own surrogates.

This module pins the invariant at the two mint seams named in the issue, plus the
regression this shipped to fix: a seeded-only prompt mentioning ``Henning Albers``
(whose vendored-seed surrogate is pool-position 4, "Stefan Kaiser" -- which contains
"Stefan", a Variation of seeded "Stefan Wegner").

Leak-audit clauses exercised:
- A: the stub upstream receives only surrogates -- a minted surrogate that would
  itself trip the leak gate never gets minted in the first place.
- D: the verify pass (leak gate + resolution gate) stays clean on a request that
  carries only known entities.

N/A this slice: B/C (untouched -- restore/closed-world semantics don't change), E
(reserved-namespace PII, unrelated mint path), F (fail-closed policy, unrelated),
G (mapping secrecy, unrelated -- issue #3/#10 deferral stands).
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_upstream_client, get_workspace_policies
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.review import ReviewInbox
from blindfold.store import vendored_seed_repository
from blindfold.store._mint import mint_surrogates
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


def _deterministic_only_policies() -> WorkspacePolicies:
    # No L3 wired in this module -- opt the default workspace into the documented
    # deterministic-only degrade (ADR-0009) so SEC-7's fail-closed-by-default gate
    # doesn't block on the incidental capitalized words the deterministic passes
    # already handle correctly (mirrors test_proxy_round_trip.py).
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


def _make_stub_upstream(scripted_response, recorded):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_seeded_only_prompt_mentioning_henning_albers_round_trips_200():
    # Regression (issue #79 repro, AC1): "Henning Albers" is seeded-only -- no L3
    # candidate, no novel mint -- yet today it 503s leak_detected because its
    # vendored-seed surrogate ("Stefan Kaiser") contains "Stefan", a Variation of
    # the separately-seeded "Stefan Wegner". A mint-time-disjoint pool must never
    # assign that colliding entry, so this must round-trip 200 with surrogates-only
    # egress like any other seeded entity.
    mapping = _seeded_mapping()
    real = "Henning Albers"
    surrogate = mapping.surrogate_for(real)
    assert surrogate is not None

    scripted_response = {
        "content": [{"type": "text", "text": f"Notified {surrogate}."}]
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": f"Please brief {real}."}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text

    # Clause A: only the surrogate egressed -- no real value, and no leftover
    # collision with a DIFFERENT referent's Variation ("Stefan").
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    assert real not in egressed
    assert "Stefan" not in egressed
    assert surrogate in egressed

    # Client still sees the real value back (closed-world restore, unaffected).
    body = resp.json()
    assert real in body["content"][0]["text"]


def test_no_seed_pool_surrogate_contains_a_known_entity_variation_token():
    # AC2 (seed-pool half): pinned over the FULL vendored seed, not just the one
    # regression case -- every surrogate the repository assigns must be disjoint
    # from every known canonical name / Variation in the closed-world set (the
    # exact set the pre-egress leak gate consults via ``mapping.real_values()``).
    pairs = vendored_seed_repository().seeded_pairs()
    known_values = {real for real, _surrogate in pairs}
    surrogates = {surrogate for _real, surrogate in pairs}

    for surrogate in surrogates:
        for known in known_values:
            assert known not in surrogate, (
                f"surrogate {surrogate!r} contains known-entity token {known!r}"
            )


def test_provisional_pool_never_mints_a_surrogate_colliding_with_a_known_entity_variation():
    # AC2 (provisional-pool half): the issue's own observed collision -- pool position
    # 6 of ``_PROVISIONAL_POOL`` is "Greta Henning", which contains "Henning", a
    # Variation of seeded "Henning Albers". Minting enough novel L3-confirmed
    # candidates to reach that position must skip it, never inject it.
    inbox = ReviewInbox()
    known_values = ["Henning Albers", "Henning", "Albers"]
    novels = [f"Novel{i}" for i in range(7)]  # 7th reaches raw pool position 6

    items = [
        inbox.upsert(novel, context="ctx", known_values=known_values) for novel in novels
    ]

    surrogates = [item.provisional_surrogate for item in items]
    assert "Greta Henning" not in surrogates
    for surrogate in surrogates:
        assert "Henning" not in surrogate


def test_colliding_pool_entry_is_skipped_not_reused_and_non_colliding_entries_stay_e_stable():
    # AC3: forcing a collision at the real ``_PERSON_POOL`` position 4 ("Stefan
    # Kaiser", which contains "Stefan") must (a) never assign it to ANY referent,
    # (b) leave every referent before the collision untouched (E-stable), and
    # (c) shift every referent from the collision onward by exactly one position,
    # extending into the numbered fallback rather than reusing the skipped entry.
    baseline = mint_surrogates("person", 8)  # no known_values -> no collisions
    assert baseline[4] == "Stefan Kaiser"

    minted = mint_surrogates("person", 8, known_values=["Stefan"])

    assert "Stefan Kaiser" not in minted
    assert len(minted) == len(set(minted)) == 8  # no duplicate reuse of any entry
    # E-stable: referents before the collision are unaffected.
    assert minted[:4] == baseline[:4]
    # The referent that would have collided, and everyone after, shift by one --
    # drawing from the pool's remaining entries and then the numbered fallback.
    assert minted[4:] == baseline[5:] + ["Person Surrogate 8"]
