"""Review-inbox SPA seam (ADR-0011 / issue #14): a thin reactive UI over the
JSON management API exposed by :mod:`blindfold.app`.

The SPA is a single-file Vue 3 page served by FastAPI. It lists the inbox of
**provisional**ly-blindfolded **candidate**s minted by the learning loop, and
exposes **confirm** (grows the entity graph) and **reject** (grows the
allowlist) actions wired to the existing JSON management endpoints
(``/v1/management/review-inbox`` + ``…/{id}/confirm`` + ``…/{id}/reject``).

The JSON API is the tested seam (ADR-0011): browser-side reactive behaviour is
verified observationally by ``browser-verify`` against the running SPA; this
suite asserts the SPA is served, names the right endpoints, embeds the
ubiquitous review-inbox language, and that the **same** confirm/reject calls
the SPA issues produce the documented graph/allowlist effects through the
FastAPI test client.

Leak-audit clauses for this slice:
- A/B/C/D/E/F/G N/A: the SPA never reaches the proxy request path. It only
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
from blindfold.spa import (
    REVIEW_INBOX_CONFIRM_ENDPOINT,
    REVIEW_INBOX_LIST_ENDPOINT,
    REVIEW_INBOX_REJECT_ENDPOINT,
)
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


@pytest.mark.anyio
async def test_review_inbox_spa_is_served_as_html_with_a_mount_point():
    # ADR-0011: a React/Vue SPA over the FastAPI JSON API. We serve the bundle
    # as a single HTML page so the user can open it in a browser without a
    # separate dev server. Asserts the route returns text/html and contains a
    # mount point the client-side framework attaches to.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://proxy.test"
    ) as client:
        resp = await client.get("/ui/review-inbox")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    body = resp.text
    assert "<!doctype html>" in body.lower()
    assert 'id="review-inbox-app"' in body


@pytest.mark.anyio
async def test_review_inbox_spa_references_the_management_json_endpoints_it_consumes():
    # ADR-0011: the JSON API is the clean boundary the SPA consumes — the SPA
    # bundle must actually call the documented list / confirm / reject endpoints.
    # If the routes are renamed and the SPA isn't updated, this catches it.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://proxy.test"
    ) as client:
        resp = await client.get("/ui/review-inbox")
    body = resp.text
    assert REVIEW_INBOX_LIST_ENDPOINT in body
    # confirm/reject endpoints are templated with an item id — the substring
    # the SPA composes around encodeURIComponent(id) is the stable prefix.
    confirm_prefix = REVIEW_INBOX_CONFIRM_ENDPOINT.split("{id}")[0]
    reject_prefix = REVIEW_INBOX_REJECT_ENDPOINT.split("{id}")[0]
    assert confirm_prefix in body
    assert reject_prefix in body
    # Project's ubiquitous language ("review inbox" / "confirm" / "reject" /
    # "provisional surrogate") surfaces in the UI, not "anonymize"/"mask"/etc.
    assert "review inbox" in body.lower()
    assert "provisional" in body.lower()
    for forbidden in ("anonymize", "anonymise", "mask", "redact", "de-anonymize"):
        assert forbidden not in body.lower(), f"{forbidden!r} is not project language"


@pytest.mark.anyio
async def test_spa_post_action_removes_item_from_subsequent_list_so_ui_can_reactively_drop_it():
    # The SPA is reactive: after a successful confirm/reject POST, the triaged
    # item is no longer in the inbox. The next list call (the SPA's source of
    # truth when refreshing, or what an external observer would see) returns
    # the inbox without it. That's the seam Vue's reactive list re-renders off.
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
async def test_spa_confirm_reject_calls_grow_entity_graph_and_allowlist_at_api_seam():
    # Acceptance criterion #3: actions produce the same graph/allowlist effects
    # verified at the API seam. The SPA does nothing more than fire the same
    # confirm/reject POSTs covered by the learning-loop tests; this asserts that
    # the management seam — as the SPA hits it — grows the SurrogateMapping on
    # confirm and the Allowlist on reject.
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

    # API responses use the documented action verbs the SPA shows the user.
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
