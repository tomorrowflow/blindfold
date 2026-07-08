"""ADR-0022 / issue #68: L3 must never re-adjudicate an already-injected surrogate.

Root cause: ``_blindfold_text`` (engine.py) runs the L3 candidate loop over ``result``,
which already carries the L2 dictionary-pass (and L1 PII-pass) surrogates for this
exchange. ``select_candidate_spans`` only filters by ``known_entities`` + the allowlist
-- it has no notion of "this capitalized token is a fragment of a surrogate someone
already minted". With a real adjudicator (any model that says "yes" to a name-shaped
token, not just a hand-scripted one), the L2-injected surrogate "Bernhard Vogt" for
seeded entity "Martin Bach" gets treated as a fresh novel candidate and re-blindfolded
to a **second** surrogate from the provisional pool. Restore then only un-nests the L3
layer, leaving the L2 surrogate "Bernhard Vogt" stranded in the client-visible response
-- the resolution gate (SEC-6) correctly fail-closes on it, but the *previously working*
seeded/deterministic path now 503s.

The fix is a known-surrogate guard in the L3 candidate loop mirroring the L1 PII guard
at engine.py:218 (``mapping.is_known_surrogate``) -- generalized to *every* surrogate
namespace: seed surrogates, cold-start ``store/_mint.py`` pools, the review inbox's
``_PROVISIONAL_POOL``, and anything already recorded in ``session.injected`` this
exchange.

Leak-audit clauses exercised:
- A: the stub upstream receives only surrogates -- never a real entity, never a
  double-surrogated fragment.
- B: the client receives fully restored real values (closed-world restore).
- The resolution gate (SEC-6) does not fire: no injected surrogate is left unresolved.

N/A this slice: C/D/E/F/G -- unrelated to this guard; covered by adjacent suites.
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _ConfirmAnyNameShapedTokenAdjudicator:
    """Stub for a real local model: confirms EVERY candidate span as an entity.

    A hand-scripted stub that only confirms specific novel tokens (e.g. "Klaus")
    can't reproduce this bug -- it never says "yes" to a surrogate fragment like
    "Bernhard" because that string was never on its confirm-list. A real model has
    no such list: it says "yes, this looks like a name" to *any* name-shaped token,
    surrogate fragment included. This stub reproduces that class of adjudicator.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        return L3Adjudication(is_entity=True)


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


def _make_echo_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    """Stub upstream that echoes the (blindfolded) user text back verbatim.

    Mirrors the trusted-maintainer live-verify repro's "local echo upstream" --
    lets the test assert both what crossed egress (clause A) and what restore
    hands back to the client (clause B) from one exchange.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        payload = json.loads(request.content.decode("utf-8"))
        echoed_text = payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": echoed_text}],
                "model": "claude-3-5-sonnet",
                "stop_reason": "end_turn",
            },
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_seeded_only_prompt_round_trips_without_l3_reblindfolding_the_l2_surrogate():
    # "Martin Bach" / "Enervia" are the first seeded person/term (vendored_seed.json),
    # so their surrogates are "Bernhard Vogt" / "Projekt Polarstern" (store/_mint.py
    # pool order 0). No novel entity is present -- this is the seeded-only repro from
    # the issue, not the separate novel-discovery follow-up.
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    adjudicator = _ConfirmAnyNameShapedTokenAdjudicator()
    detector = L3Detector(adjudicator)

    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_echo_upstream(recorded)
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
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
                        {"role": "user", "content": "Martin Bach works at Enervia."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # No fail-closed 503 -- the previously-working deterministic seeded path must
    # keep working now that a real (any-name-confirming) L3 adjudicator is wired.
    assert resp.status_code == 200

    # Clause A: egress carries the L2 surrogates only -- no real value, and no
    # *second*-level surrogate from the provisional pool (that would mean L3
    # re-blindfolded the surrogate L2 just injected).
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    assert "Martin Bach" not in egressed
    assert "Enervia" not in egressed
    assert "Bernhard Vogt" in egressed
    assert "Projekt Polarstern" in egressed
    for provisional in (
        "Alex Brenner", "Berta Falke", "Carla Distel", "Doris Engler",
        "Emil Fink", "Fritz Graf", "Greta Henning", "Hugo Imhoff",
    ):
        assert provisional not in egressed

    # Clause B: closed-world restore hands the real values back to the client.
    body = resp.json()
    restored_text = body["content"][0]["text"]
    assert "Martin Bach" in restored_text
    assert "Enervia" in restored_text
    assert "Bernhard Vogt" not in restored_text
    assert "Projekt Polarstern" not in restored_text

    # The review inbox holds no surrogate-fragment items -- L3 never confirmed
    # "Bernhard"/"Vogt"/"Projekt"/"Polarstern" as novel candidates.
    inbox_reals = {item.real for item in inbox.list()}
    assert inbox_reals.isdisjoint({"Bernhard", "Vogt", "Projekt", "Polarstern"})


@pytest.mark.anyio
async def test_novel_only_prompt_round_trips_with_no_seeded_entity_present():
    # Companion case from the trusted-maintainer follow-up: a prompt that mentions
    # NO seeded entity at all must still round-trip cleanly once a real (any-name-
    # confirming) adjudicator is wired -- the guard must not regress the pure
    # novel-discovery path while fixing the seeded-surrogate re-adjudication bug.
    # (The separate lowercase/offset-corruption failure mode noted in the issue's
    # live-verify follow-up is explicitly out of scope for this guard.)
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    adjudicator = _ConfirmAnyNameShapedTokenAdjudicator()
    detector = L3Detector(adjudicator)

    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_echo_upstream(recorded)
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
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
                        {
                            "role": "user",
                            "content": "Priya Nadkarni and Ravi Deshmukh met on Tuesday.",
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200

    # Clause A: only surrogates egress -- never the real novel names.
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    assert "Priya" not in egressed
    assert "Nadkarni" not in egressed
    assert "Ravi" not in egressed
    assert "Deshmukh" not in egressed

    # Clause B: closed-world restore hands the real names back.
    body = resp.json()
    restored_text = body["content"][0]["text"]
    assert "Priya Nadkarni" in restored_text
    assert "Ravi Deshmukh" in restored_text

    # The mint pass produced provisional inbox entries for the real novel names --
    # not surrogate fragments of each other (a re-adjudication chain would show up
    # here as garbage entries with no relation to "Priya Nadkarni"/"Ravi Deshmukh").
    inbox_reals = {item.real for item in inbox.list()}
    assert inbox_reals == {"Priya", "Nadkarni", "Ravi", "Deshmukh", "Tuesday"}
