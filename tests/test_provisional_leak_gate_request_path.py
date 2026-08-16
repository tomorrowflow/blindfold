"""Pre-egress leak gate coverage for provisional entities (issue #287), updated for
ADR-0051 stage 2 (issue #300).

A **provisional** entity -- L3-confirmed and minted into the review inbox, but not
yet human-confirmed into the entity graph -- lives only in ``inbox.list()``, never
in ``mapping.real_values()`` (that set grows on confirm only). Restore still puts
the real value in front of the client on the turn it is discovered; if the client's
own transcript carries that real value back on a later turn and detection misses it
(a real risk with an LLM-backed L3 adjudicator, which is not guaranteed consistent
call to call), turn 2 is exactly the run-6-shaped deadlock ADR-0051 names -- a
message hop carrying a replayed provisional real, on a request with no tool array to
point at (#299 fixed the tool-description instance of this same gap; #300 fixes it
here).

Before #300: the deterministic blinding pass only matched the entity graph
(``mapping.entities()``, grows on confirm only), so turn 2's replayed real value
reached the pre-egress leak gate un-blindfolded and the request was fail-closed
(503) -- protection depended on L3 happening to re-confirm the value, which this
test deliberately models as NOT happening. After #300: the deterministic pass also
applies every already-minted provisional pair to every message hop, so turn 2's
replayed real value is blindfolded before the leak gate ever runs, and the request
succeeds normally -- no re-confirmation dependency, no deadlock.

Drives the proxy seam end-to-end with a stub upstream (the egress oracle) across
two turns:
- Turn 1: a novel candidate is L3-confirmed, auto-blindfolded with a provisional
  surrogate, and the response restores the real value back to the client.
- Turn 2: the client's own transcript carries the restored real value straight
  back (the exposure path the issue names); this time detection misses it (the
  stub adjudicator no longer confirms it -- modelling an inconsistent judgement,
  not a deterministic-only opt-in). The request must succeed, PROTECTED rather
  than blocked: the deterministic provisional-pair pass (#300) catches it.

Leak-audit clauses:
- A: asserted directly -- the stub upstream's turn-2 request body carries zero
  occurrences of the real value; only the reused provisional surrogate crosses
  egress.
- B: asserted directly -- both turns' echoing stub replies carry the provisional
  surrogate, and the client-visible responses have it restored to the real value.
- F: fail-closed (ADR-0009) is unexercised by this test post-#300 (there is no
  longer a detection-miss gap for this scenario to fall back on); the mechanism
  itself is untouched and still covered by the adjacent fail-closed suites.
- C/D: covered by the adjacent round-trip / learning-loop suites; unweakened here.
- E: turn 2 reuses turn 1's existing provisional surrogate -- no second mint for
  the same referent (ADR-0051's own invariant), asserted via ``inbox.list()``.
- G: N/A -- mapping/inbox secrecy is out of scope for this slice (no store wired).
"""

import json

import httpx
import pytest

from blindfold.app import (
    app,
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


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


class _StubAdjudicator:
    """Stub for Ollama: returns is_entity=True only for whitelisted candidate texts."""

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text in self._confirm:
            return L3Adjudication(is_entity=True)
        return L3Adjudication(is_entity=False)


def _make_echoing_stub_upstream(recorded: list[httpx.Request]):
    """A stub upstream whose reply quotes the blinded user text verbatim.

    Since the request the provider sees carries only the provisional surrogate
    (never the real value), echoing it back and letting restore run over it is
    exactly how a real assistant reply carrying a surrogate token would behave --
    this is what actually exercises clause B (restore) for this regression,
    rather than a scripted response that never contains the surrogate at all.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        sent = json.loads(request.content)
        blinded_text = sent["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": f"Contacting {blinded_text} now."}
                ],
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
async def test_a_provisional_entitys_real_value_replayed_on_a_later_turn_is_blindfolded_not_blocked():
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    novel = "Kestrel Dynamics"

    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_echoing_stub_upstream(
        recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            # Turn 1: L3 confirms the novel org, it's auto-blindfolded and minted
            # into the review inbox as a provisional entity.
            app.dependency_overrides[get_l3_detector] = lambda: L3Detector(
                _StubAdjudicator(confirm={"Kestrel", "Dynamics"})
            )
            first = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": f"Please brief {novel} tomorrow."}
                    ],
                },
            )
            assert first.status_code == 200
            assert len(recorded) == 1
            assert novel not in recorded[0].content.decode("utf-8")
            items = inbox.list()
            assert len(items) == 1
            assert items[0].real == novel
            # Clause B: restore puts the real value back in front of the client --
            # this is the discovery turn the issue names, step 1 of the exposure path.
            assert novel in first.json()["content"][0]["text"]

            # Turn 2: the client's transcript carries the restored real value back.
            # Detection misses it this time (the adjudicator no longer confirms it) --
            # the provisional entity is still only in the inbox, never the mapping's
            # confirmed real_values(). Pre-#300, the deterministic pass couldn't reach
            # it either (entity-graph-only), so this fell through to the leak gate and
            # was blocked. Post-#300, the deterministic pass itself blindfolds the
            # replayed real value from the inbox's own provisional pair, so the
            # request reaches (and passes) the leak gate normally.
            app.dependency_overrides[get_l3_detector] = lambda: L3Detector(
                _StubAdjudicator(confirm=set())
            )
            second = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {
                            "role": "user",
                            "content": f"Following up with {novel} again.",
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # ADR-0051 stage 2 (issue #300): protected, not fail-closed -- the request
    # succeeds because the deterministic pass caught the replayed real value.
    assert second.status_code == 200
    # Clause A: turn 2's request reached the stub upstream (recorded), but never
    # carrying the real value -- only the reused provisional surrogate.
    assert len(recorded) == 2
    assert novel not in recorded[1].content.decode("utf-8")
    # ADR-0051's own invariant: the same referent reuses turn 1's existing
    # provisional surrogate -- no second inbox row minted for it.
    assert len(inbox.list()) == 1
    assert inbox.list()[0].real == novel
    # Clause B: restore puts the real value back in front of the client on turn 2
    # too, exactly as it did on the discovery turn.
    assert novel in second.json()["content"][0]["text"]
