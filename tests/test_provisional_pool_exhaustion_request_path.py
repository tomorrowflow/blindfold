"""HTTP proxy seam for issue #331: the provisional-surrogate mint-time
disjointness walk (``review._next_provisional``) used to hang forever once
its named pool was exhausted and a known real value collided with every
numbered fallback label -- ADR-0052's run-8 deadlock ("Surrogate" live as a
known real, "Provisional Surrogate {N}" the fallback shape). Issue #330 closed
that specific collision by making the fallback an opaque ``BFX{N:04d}`` token.

Issue #332 then aligned the mint-time collision check to the leak gate's
word-boundary rule, which makes a *bare prefix* real like "BFX" immune to
every fallback candidate (no word boundary before the digits of "BFX0008") --
the intended immunity ADR-0052 describes. Forcing exhaustion at this seam now
requires a known real for every candidate the walk will try, so this patches
the walk's bound down to a small number and seeds exact-match known reals for
that reduced range. Mirrors the phone reserved-namespace pool exhaustion
shape (test_pii_phone_pool_exhaustion.py): a stable ``blindfold_fail_closed``
code, a distinct ``sub_reason``, and the real value never appears anywhere in
the 503 body -- SEC-3/SEC-7 -- and the stub upstream never sees the request
at all (leak-audit clause A).
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold import review
from blindfold.app import (
    app,
    get_audit_log,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import _PROVISIONAL_POOL, ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient

_BOUND = 3
_POOL_SIZE = len(_PROVISIONAL_POOL)  # issue #338: enlarged 8 -> 32, position-stable


class _StubAdjudicator:
    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=candidate.text in self._confirm)


def _make_stub_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


def _inbox_with_exhausted_person_pool() -> ReviewInbox:
    # Consume all named "person" pool slots (review._PROVISIONAL_POOL) so the
    # cursor for the very next candidate lands on the numbered fallback.
    inbox = ReviewInbox()
    for i in range(_POOL_SIZE):
        inbox.upsert(real=f"Filler Real {i}", context=f"context {i}")
    return inbox


@pytest.mark.anyio
async def test_provisional_pool_exhaustion_blocks_the_request_fail_closed_and_scrubbed(
    monkeypatch,
):
    monkeypatch.setattr(review, "_MAX_FALLBACK_ATTEMPTS", _BOUND)
    # A known real must now exact-match a fallback candidate to collide with it
    # (#332's word-boundary alignment) -- so exhaust every position the
    # patched-down bound will try, rather than relying on a shared "BFX" prefix.
    known_reals = [
        (f"BFX{position:04d}", f"Someone Else {position}")
        for position in range(_POOL_SIZE, _POOL_SIZE + _BOUND)
    ]
    mapping = SurrogateMapping.from_pairs(known_reals)
    inbox = _inbox_with_exhausted_person_pool()
    detector = L3Detector(_StubAdjudicator(confirm={"Klaus"}))

    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
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
                        {"role": "user", "content": "Please brief Klaus tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "blindfold_fail_closed"
    assert body["error"]["sub_reason"] == "provisional_pool_exhausted"
    assert "Klaus" not in json.dumps(body)
    assert "BFX" not in json.dumps(body)
    # Clause A: blocked before any egress -- the stub upstream saw nothing.
    assert recorded == []
    assert any(
        record.event == "blocked-provisional-pool-exhausted" for record in audit_log.records
    )
    assert not any(
        "Klaus" in record.reason for record in audit_log.records if record.reason
    )
