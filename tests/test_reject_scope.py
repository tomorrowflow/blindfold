"""Reject takes a scope, defaulting to the item's own workspace (ADR-0010 #423
amendment decisions 3/4, issue #443).

The #442 slice built the scoped-Allowlist machinery but left ``reject_review_item``
writing an all-workspaces entry unconditionally ("the next slice's job" -- issue
#442's own commit message). This slice flips that: ``POST …/reject`` takes an
optional ``{"scope": "workspace" | "all"}`` body. Omitted (or ``"workspace"``)
means ``item.workspace``; ``"all"`` is the explicit, disclosed all-workspaces
choice. Any other value is a 422 that changes nothing.

Leak-audit: a workspace-A reject must not un-blindfold the value in workspace B
-- proven end to end through the stub upstream provider (clause A), not merely
against the in-memory ``Allowlist`` (already covered unit/engine-level by
``test_allowlist_workspace_scope*.py``, issue #442).
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_allowlist,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import Allowlist, ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _ConfirmAllAdjudicator:
    """Confirms every candidate as an entity; records every call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        return L3Adjudication(is_entity=True)


def _make_stub_upstream(scripted_response: dict, recorded: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


_SCRIPTED_RESPONSE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "Acknowledged."}],
    "model": "claude-3-5-sonnet",
    "stop_reason": "end_turn",
}


def _payload(text: str) -> dict:
    return {"model": "m", "messages": [{"role": "user", "content": text}]}


@pytest.mark.anyio
async def test_reject_with_an_invalid_scope_returns_422_and_changes_nothing():
    inbox = ReviewInbox()
    allowlist = Allowlist()
    item = inbox.upsert("Helga", context="Please brief Helga tomorrow.")

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                f"/v1/management/review-inbox/{item.id}/reject",
                json={"scope": "bogus"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    assert not allowlist.contains("Helga", item.workspace)
    assert inbox.get(item.id) is not None


@pytest.mark.anyio
async def test_reject_with_no_body_scopes_to_the_items_own_workspace_end_to_end():
    # AC1: suppressed in that workspace, still discovered (reaches L3, mints a
    # fresh provisional surrogate, never egresses in the clear) in another --
    # proven through the real request path, not the in-memory Allowlist alone.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _ConfirmAllAdjudicator()
    detector = L3Detector(adjudicator, allowlist=allowlist)
    recorded: list[httpx.Request] = []

    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        _SCRIPTED_RESPONSE, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            # Turn 1, workspace A: novel candidate auto-blindfolded.
            await client.post(
                "/v1/messages",
                json=_payload("Please mention Helga again."),
                headers={"x-blindfold-workspace": "workspace-a"},
            )
            item_id = inbox.list()[0].id

            # Reject with no body -- defaults to the item's own workspace (A).
            reject_resp = await client.post(
                f"/v1/management/review-inbox/{item_id}/reject"
            )
            assert reject_resp.status_code == 200
            assert reject_resp.json()["scope"] == "workspace"

            calls_after_reject = len(adjudicator.calls)

            # Turn 2, workspace A: the token is suppressed -- egresses in the
            # clear, deliberately (the user rejected it in this workspace).
            await client.post(
                "/v1/messages",
                json=_payload("Mention Helga again."),
                headers={"x-blindfold-workspace": "workspace-a"},
            )
            assert "Helga" not in adjudicator.calls[calls_after_reject:]

            # Turn 3, workspace B: still a novel candidate -- L3 adjudicates
            # it fresh and it never crosses egress in the clear.
            resp_b = await client.post(
                "/v1/messages",
                json=_payload("Please mention Helga again."),
                headers={"x-blindfold-workspace": "workspace-b"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp_b.status_code == 200
    assert "Helga" in adjudicator.calls
    egressed_b = recorded[-1].content.decode("utf-8")
    assert "Helga" not in egressed_b


@pytest.mark.anyio
async def test_reject_with_all_scope_suppresses_in_every_workspace():
    # AC2: {"scope": "all"} is the explicit, disclosed all-workspaces choice.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _ConfirmAllAdjudicator()
    detector = L3Detector(adjudicator, allowlist=allowlist)
    recorded: list[httpx.Request] = []

    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        _SCRIPTED_RESPONSE, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            await client.post(
                "/v1/messages",
                json=_payload("Please mention Helga again."),
                headers={"x-blindfold-workspace": "workspace-a"},
            )
            item_id = inbox.list()[0].id

            reject_resp = await client.post(
                f"/v1/management/review-inbox/{item_id}/reject",
                json={"scope": "all"},
            )
            assert reject_resp.status_code == 200
            assert reject_resp.json()["scope"] == "all"

            calls_after_reject = len(adjudicator.calls)

            resp_b = await client.post(
                "/v1/messages",
                json=_payload("Mention Helga again."),
                headers={"x-blindfold-workspace": "workspace-b"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp_b.status_code == 200
    assert "Helga" not in adjudicator.calls[calls_after_reject:]
    egressed = recorded[-1].content.decode("utf-8")
    assert "Helga" in egressed  # deliberately in the clear -- an all-workspaces reject
