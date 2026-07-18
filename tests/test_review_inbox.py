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
