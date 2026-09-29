"""Workspace-scoped allowlist suppression through the real request path
(ADR-0010 #423 amendment, issue #442).

A unit test on `Allowlist.contains` alone would prove the set's own scoping
logic, but not that the engine actually threads the request's workspace down
to it -- this is the "engine-level test through the real request path" the
issue's acceptance criteria call for.

Leak-audit: a learned reject scoped to workspace A must not suppress
candidacy in workspace B -- a genuine novel entity sharing that token in B
still reaches L3, gets a provisional surrogate, and never crosses egress in
the clear. Asserted below via the stub upstream client, the same seam every
other leak-audit test in this suite stubs.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.engine import blindfold_payload
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import Allowlist, ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _ConfirmAllAdjudicator:
    """Confirms every candidate as an entity; records every call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        return L3Adjudication(is_entity=True)


def _payload(text: str) -> dict:
    return {
        "model": "m",
        "messages": [{"role": "user", "content": text}],
    }


def test_workspace_scoped_reject_suppresses_candidacy_only_in_its_own_workspace():
    allowlist = Allowlist()
    allowlist.add("Helga", workspace="workspace-a")
    adjudicator = _ConfirmAllAdjudicator()
    detector = L3Detector(adjudicator, allowlist=allowlist)

    blinded_a, _ = blindfold_payload(
        _payload("Please mention Helga again."),
        SurrogateMapping.from_pairs([]),
        detector,
        ReviewInbox(),
        workspace="workspace-a",
    )
    assert "Helga" in blinded_a["messages"][0]["content"]
    assert "Helga" not in adjudicator.calls  # never even reached L3

    blinded_b, _ = blindfold_payload(
        _payload("Please mention Helga again."),
        SurrogateMapping.from_pairs([]),
        detector,
        ReviewInbox(),
        workspace="workspace-b",
    )
    assert "Helga" not in blinded_b["messages"][0]["content"]  # blindfolded
    assert "Helga" in adjudicator.calls  # reached L3 in the other workspace


def _make_stub_upstream(scripted_response: dict, recorded: list[httpx.Request]):
    from blindfold.upstream import UpstreamClient

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_workspace_b_entity_sharing_a_workspace_a_learned_reject_token_never_egresses_in_the_clear():
    from blindfold.app import (
        app,
        get_allowlist,
        get_l3_detector,
        get_mapping,
        get_review_inbox,
        get_upstream_client,
    )

    allowlist = Allowlist()
    allowlist.add("Helga", workspace="workspace-a")
    adjudicator = _ConfirmAllAdjudicator()
    detector = L3Detector(adjudicator, allowlist=allowlist)
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
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
    app.dependency_overrides[get_l3_detector] = lambda: detector
    app.dependency_overrides[get_allowlist] = lambda: allowlist

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client_http:
        resp = await client_http.post(
            "/v1/messages",
            json=_payload("Please mention Helga again."),
            headers={"x-blindfold-workspace": "workspace-b"},
        )

    assert resp.status_code == 200
    assert "Helga" in adjudicator.calls
    # Clause A: the real value never egressed in the clear -- L3 confirmed it
    # (a genuine entity in this workspace), so it got a provisional surrogate.
    egressed = recorded[0].content.decode("utf-8")
    assert "Helga" not in egressed
