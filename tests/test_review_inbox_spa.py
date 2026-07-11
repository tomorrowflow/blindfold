"""Review-inbox management-API seam (ADR-0011 / ADR-0010): the JSON boundary
the review-inbox frontend consumes.

The frontend used to be a single-file Vue 3 page served by FastAPI at
``/ui/review-inbox`` (issue #14); it's now the React view in the unified shell
at ``/ui/inbox`` (issue #99). Neither frontend owns any behavior of its own —
both are thin reactive UIs over the same JSON management endpoints
(``/v1/management/review-inbox`` + ``…/{id}/confirm`` + ``…/{id}/reject``).
This suite asserts that seam directly: confirm/reject calls produce the
documented graph/allowlist effects, and a triaged item reactively drops out of
the next list call — the same JSON behavior either frontend renders.

Leak-audit clauses for this slice:
- A/B/C/D/E/F/G N/A: this seam never reaches the proxy request path. It only
  reads inbox metadata (real value + provisional surrogate the user owns) and
  writes confirm/reject; no provider egress, no surrogate restore, no L3.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_allowlist,
    get_mapping,
    get_review_inbox,
)
from blindfold.review import Allowlist, ReviewInbox
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


@pytest.mark.anyio
async def test_post_action_removes_item_from_subsequent_list_so_ui_can_reactively_drop_it():
    # The frontend is reactive: after a successful confirm/reject POST, the
    # triaged item is no longer in the inbox. The next list call (the frontend's
    # source of truth when refreshing, or what an external observer would see)
    # returns the inbox without it. That's the seam either frontend re-renders off.
    inbox = ReviewInbox()
    mapping = _seeded_mapping()
    allowlist = Allowlist()
    inbox.upsert("Klaus", context="Please brief Klaus tomorrow.")
    inbox.upsert("Yasmin", context="Email Yasmin the deck.")

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            listed = (await client.get("/v1/management/review-inbox")).json()["items"]
            assert {item["real"] for item in listed} == {"Klaus", "Yasmin"}

            # Reactive remove on confirm.
            klaus_id = next(item["id"] for item in listed if item["real"] == "Klaus")
            confirmed = await client.post(
                f"/v1/management/review-inbox/{klaus_id}/confirm"
            )
            assert confirmed.status_code == 200
            after_confirm = (
                await client.get("/v1/management/review-inbox")
            ).json()["items"]
            assert {item["real"] for item in after_confirm} == {"Yasmin"}

            # Reactive remove on reject.
            yasmin_id = next(item["id"] for item in after_confirm if item["real"] == "Yasmin")
            rejected = await client.post(
                f"/v1/management/review-inbox/{yasmin_id}/reject"
            )
            assert rejected.status_code == 200
            after_reject = (
                await client.get("/v1/management/review-inbox")
            ).json()["items"]
            assert after_reject == []
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_confirm_reject_calls_grow_entity_graph_and_allowlist_at_api_seam():
    # Acceptance criterion #3: actions produce the same graph/allowlist effects
    # verified at the API seam. The frontend does nothing more than fire the
    # same confirm/reject POSTs covered by the learning-loop tests; this asserts
    # that the management seam — as the frontend hits it — grows the
    # SurrogateMapping on confirm and the Allowlist on reject.
    inbox = ReviewInbox()
    mapping = _seeded_mapping()
    allowlist = Allowlist()
    confirmable = inbox.upsert("Astrid", context="Brief Astrid tomorrow.")
    rejectable = inbox.upsert("BUFGRP", context="The BUFGRP buffer overflowed.")

    # Pre-conditions: neither token is in the entity graph or allowlist yet.
    assert mapping.surrogate_for("Astrid") is None
    assert not allowlist.contains("BUFGRP")

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            confirm_resp = await client.post(
                f"/v1/management/review-inbox/{confirmable.id}/confirm"
            )
            reject_resp = await client.post(
                f"/v1/management/review-inbox/{rejectable.id}/reject"
            )
    finally:
        app.dependency_overrides.clear()

    # API responses use the documented action verbs the frontend shows the user.
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["action"] == "confirmed"
    assert reject_resp.status_code == 200
    assert reject_resp.json()["action"] == "rejected"

    # Confirm grew the entity graph: same real value → its provisional surrogate.
    assert mapping.surrogate_for("Astrid") == confirmable.provisional_surrogate
    # Reject grew the allowlist: the token is recorded.
    assert allowlist.contains("BUFGRP")
    # Both items are gone from the inbox (already covered above, but reasserted
    # so this seam test stands alone as the parity guard).
    assert inbox.list() == []
