"""Review inbox — viewer-gate GET /v1/management/review-inbox (ADR-0035, issue #152).

``GET /v1/management/review-inbox`` renders **real** plaintext (``real`` +
surrounding ``context``) for provisional candidates, the same sensitivity class
the audit log already ``viewer``-gates. This suite asserts the review-inbox list
endpoint now requires the same role, anchored the same way (a required
``workspace`` query param + ``_require_role``, mirroring
``test_audit_viewer_rbac.py``'s coverage of ``GET /v1/management/audit``).

Confirm/reject (POST) and the learning loop itself are untouched by this slice
— covered unchanged by ``test_review_inbox_learning_loop.py`` /
``test_review_inbox_spa.py``.

Leak-audit clause analysis: A-E/G N/A — this slice never touches the proxy
request path (mint/restore/leak_gate/resolution_gate untouched). F (fail-closed
/ access control) is the operative clause, covered by the 403-without-role and
200-with-role cases below.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_rbac, get_review_inbox
from blindfold.rbac import RbacRegistry
from blindfold.review import ReviewInbox


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


@pytest.mark.anyio
async def test_review_inbox_denied_without_viewer_role():
    rbac = RbacRegistry()  # alice has NO roles on ws-a
    inbox = ReviewInbox()
    inbox.upsert("Klaus", context="Please brief Klaus tomorrow.")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/review-inbox",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_review_inbox_lists_items_for_caller_with_viewer_role():
    # AC #1's "unchanged payload with it": the response shape is untouched by
    # the gate — same id/real/provisional_surrogate/context/context_offset
    # fields the SPA and test_review_inbox_learning_loop.py already assert on.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    inbox = ReviewInbox()
    item = inbox.upsert("Klaus", context="Please brief Klaus tomorrow.")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/review-inbox",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items == [
        {
            "id": item.id,
            "real": "Klaus",
            "provisional_surrogate": item.provisional_surrogate,
            "context": "Please brief Klaus tomorrow.",
            "context_offset": item.context_offset,
        }
    ]


@pytest.mark.anyio
async def test_review_inbox_denied_for_viewer_of_a_different_workspace():
    # Same isolation shape as test_audit_viewer_workspace_scoping_hides_other_
    # workspace_events: viewer on ws-a does not imply viewer on ws-b.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    inbox = ReviewInbox()
    inbox.upsert("Klaus", context="Please brief Klaus tomorrow.")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/review-inbox",
                params={"workspace": "ws-b"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_review_inbox_requires_workspace_query_param():
    # Same required-param contract as GET /v1/management/audit: `workspace` is
    # not optional, it's the RBAC anchor -- omitting it is a client error, not
    # an implicit "all workspaces" listing.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/review-inbox",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
