"""Pre-egress leak gate coverage for provisional entities (issue #287).

A **provisional** entity -- L3-confirmed and minted into the review inbox, but not
yet human-confirmed into the entity graph -- lives only in ``inbox.list()``, never
in ``mapping.real_values()`` (that set grows on confirm only). Restore still puts
the real value in front of the client on the turn it is discovered; if the client's
own transcript carries that real value back on a later turn and detection misses it
(a real risk with an LLM-backed L3 adjudicator, which is not guaranteed consistent
call to call), the pre-egress leak gate is the last backstop before egress.

Drives the proxy seam end-to-end with a stub upstream (the egress oracle) across
two turns:
- Turn 1: a novel candidate is L3-confirmed, auto-blindfolded with a provisional
  surrogate, and the response restores the real value back to the client.
- Turn 2: the client's own transcript carries the restored real value straight
  back (the exposure path the issue names); this time detection misses it (the
  stub adjudicator no longer confirms it -- modelling an inconsistent judgement,
  not a deterministic-only opt-in). The request must still be BLOCKED, not sent.

Leak-audit clauses:
- A: asserted directly -- the stub upstream records zero requests for turn 2 (the
  block happens pre-egress; nothing is ever written to the provider).
- B: asserted directly -- turn 1's echoing stub reply carries the provisional
  surrogate, and the client-visible response has it restored to the real value.
- F: fail-closed (ADR-0009) -- a detection miss on a provisional entity still
  blocks by default rather than egressing.
- C/D: covered by the adjacent round-trip / learning-loop suites; unweakened here.
- E: N/A -- no re-mint on turn 2 (the request never reaches the mint pass's
  observable surrogate-reuse behavior; it is blocked before egress).
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
async def test_a_provisional_entitys_real_value_replayed_on_a_later_turn_is_blocked_not_sent():
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
            # confirmed real_values(). The pre-egress leak gate must catch it anyway.
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

    # Fail-closed (ADR-0009): blocked, not a 200.
    assert second.status_code == 503
    # Clause A: nothing new reached the stub upstream -- still exactly the one
    # recorded request from turn 1.
    assert len(recorded) == 1
    body = second.json()
    assert novel not in body["error"]["reason"]
    assert novel not in body["error"]["message"]
