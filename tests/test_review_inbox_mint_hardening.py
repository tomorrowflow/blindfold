"""Mint-time disjointness hardening against already-issued provisional
surrogates (ADR-0037 / issue #169).

The review inbox's per-pool mint cursor (``_pool_positions``, issue #80) is
what normally keeps a provisional surrogate from being issued twice. ADR-0037
persists that cursor explicitly so a process restart resumes it correctly --
but as defensive hardening against a stale/reset cursor (a bug, not the
common path), the engine's mint pass must ALSO refuse to reissue a
provisional surrogate that is already active in the inbox, exactly like it
already refuses to reissue a known real value (issue #80).

Leak-audit clauses: A/B/C/D/F N/A -- this is the mint-time disjointness check
only; nothing about egress, restore, or fail-closed changes. E (stable /
idempotent mint) is what this test strengthens directly.
"""

from __future__ import annotations

from blindfold.engine import blindfold_payload
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _StubAdjudicator:
    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=candidate.text in self._confirm)


def test_mint_never_reissues_a_surrogate_already_active_in_the_inbox():
    # Simulates the exact restart-cursor-bug ADR-0037 defends against: a fresh
    # inbox whose _pool_positions cursor was NOT correctly restored (reset to
    # 0) even though an item already holds the pool's first entry. Without the
    # hardening, minting a genuinely novel candidate would reissue that same
    # surrogate for a different real value -- the #80 collision.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    existing = inbox.upsert("Existing Real", context="...Existing Real...")
    inbox._pool_positions["person"] = 0  # simulated stale/reset cursor

    detector = L3Detector(_StubAdjudicator(confirm={"Klaus"}))
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Please brief Klaus tomorrow."}],
    }

    blindfold_payload(payload, mapping, detector, inbox)

    reals = {item.real: item.provisional_surrogate for item in inbox.list()}
    assert reals["Klaus"] != existing.provisional_surrogate
