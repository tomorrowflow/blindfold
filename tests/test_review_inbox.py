"""Review inbox unit seam (ADR-0010): :class:`ReviewItem` / :class:`ReviewInbox`.

ADR-0035 decision 11 (issue #155): the review-inbox card highlights the
candidate span in place within its context window. ``context_offset`` is the
backend-derived start index of ``real`` inside ``context`` -- a fact of the
candidate's own positional span, never a frontend (or fragile backend)
``indexOf`` search that would mis-highlight on a repeated or inflected token.

Leak-audit clauses: A-G N/A -- this is the in-memory inbox data shape, never
the request path (mint/restore/leak_gate/resolution_gate untouched).
"""

from __future__ import annotations

from blindfold.review import ReviewInbox


def test_upsert_stores_the_explicitly_supplied_context_offset():
    inbox = ReviewInbox()

    item = inbox.upsert(
        "Klaus",
        context="Please tell Klaus that Klaus will call back.",
        context_offset=23,
    )

    assert item.context_offset == 23
    assert item.context[item.context_offset : item.context_offset + len("Klaus")] == "Klaus"


def test_upsert_mints_an_org_shaped_surrogate_for_an_organization_entity_type():
    # Issue #167 live evidence: "Nordwind Logistik" (organization, GLiNER score
    # 0.72) minted "Doris Engler" -- a person-shaped surrogate, from the
    # person-only _PROVISIONAL_POOL. entity_type="organization" must select a
    # distinct, org-shaped pool instead.
    from blindfold.review import _PROVISIONAL_POOL

    inbox = ReviewInbox()

    item = inbox.upsert(
        "Nordwind Logistik",
        context="...von Nordwind Logistik",
        entity_type="organization",
    )

    assert item.provisional_surrogate not in _PROVISIONAL_POOL


def test_upsert_keeps_the_default_person_pool_when_entity_type_is_unknown():
    from blindfold.review import _PROVISIONAL_POOL

    inbox = ReviewInbox()

    item = inbox.upsert("Klaus", context="Please brief Klaus tomorrow.")

    assert item.provisional_surrogate in _PROVISIONAL_POOL


def test_upsert_stores_the_entity_type_on_the_item():
    # Issue #169 / ADR-0037: a restart must reconstruct entity_type, so it has to
    # live on ReviewItem itself, not just steer pool selection at mint time.
    inbox = ReviewInbox()

    item = inbox.upsert(
        "Nordwind Logistik", context="...von Nordwind Logistik", entity_type="organization"
    )

    assert item.entity_type == "organization"


def test_upsert_defaults_entity_type_to_none():
    inbox = ReviewInbox()

    item = inbox.upsert("Klaus", context="Please brief Klaus tomorrow.")

    assert item.entity_type is None
