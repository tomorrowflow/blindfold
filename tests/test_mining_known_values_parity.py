"""Mining's mint-time known-values parity with the engine (issue #337).

``mining.py``'s out-of-band mint call site (ADR-0010) passed only
``mapping.real_values()`` as ``known_values`` to ``inbox.upsert`` -- the engine's
own mint call site (``engine.py``, ADR-0037's defense-in-depth pass, issue #328/
#333) additionally augments that set with every live inbox item's provisional
surrogate (and #306's real-word components via ``_provisional_pair_map``), so a
stale/reset pool cursor can't reissue a surrogate that already maps to a
different, live referent. Mining skipped that augmentation entirely: a mining
run against a workspace with live provisional rows could mint a fallback token
(or a named pool entry) that duplicates a live provisional surrogate from
another pool's cursor -- two different referents sharing one surrogate string,
which every downstream closed-world assumption (restore, ``_provisional_pair_map``
keys, the SPA's ``pending_by_surrogate`` classification) requires be unique.

Mining also omitted ``corpus_text`` (issue #292) from its ``upsert`` call, so a
named pool entry occurring verbatim in the mined transcript could be issued as
that very transcript's own surrogate -- the #292 collision class, out of band.

Leak-audit: N/A this slice -- mining never touches the request path (no
upstream, no restore, no streaming, no leak/resolution gate); the property
under test is the review inbox's mint-time surrogate-uniqueness invariant
itself, exercised directly through the inbox mining populates.
"""

from __future__ import annotations

from blindfold import engine, mining
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.mining import mine_transcripts
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _ConfirmSetOrg:
    def __init__(self, confirm: set[str], entity_type: str | None = "organization") -> None:
        self._confirm = confirm
        self._entity_type = entity_type

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text in self._confirm:
            return L3Adjudication(is_entity=True, entity_type=self._entity_type)
        return L3Adjudication(is_entity=False)


def _exhaust_pool(inbox: ReviewInbox, entity_type: str | None, count: int, label: str) -> None:
    for i in range(count):
        inbox.upsert(real=f"{label} {i}", context=f"context {i}", entity_type=entity_type)


def test_mining_does_not_duplicate_a_live_provisional_surrogate_across_pools():
    # Regression (issue #337): person and organization fallback tokens are
    # both ``BFX{position:04d}`` -- the position, not the pool key, is embedded
    # (review.py's ``_provisional_pool_entry``) -- so person-cursor-8 and
    # org-cursor-8 render the identical string "BFX0008". The engine's own
    # mint call site guards this by including every live inbox item's
    # provisional surrogate in ``known_values``; mining must do the same.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()

    # Exhaust the 8 named person-pool slots, then mint one more person referent
    # directly -- it falls through to the opaque numbered fallback, landing on
    # "BFX0008" (position starts at 0, exhausts through position 7, next is 8).
    _exhaust_pool(inbox, None, 8, "Person Filler")
    person_fallback_item = inbox.upsert(real="Established Person", context="ctx")
    assert person_fallback_item is not None
    assert person_fallback_item.provisional_surrogate == "BFX0008"

    # Exhaust the 8 named organization-pool slots -- its cursor is now also at
    # position 8, so its own next fallback candidate is the SAME "BFX0008".
    _exhaust_pool(inbox, "organization", 8, "Org Filler")

    detector = L3Detector(_ConfirmSetOrg({"Nordwind"}))
    report = mine_transcripts(
        ["Please route the invoice to Nordwind for approval."],
        detector,
        mapping,
        inbox,
    )

    assert len(report.proposed) == 1
    mined_item = report.proposed[0]
    assert mined_item.real == "Nordwind"
    # The bug: mining passed only ``mapping.real_values()`` (empty here) as
    # known_values, so it never saw the live person item's "BFX0008" surrogate
    # and would duplicate it. Fixed: mining must skip past the collision, the
    # same way the engine's own mint call site does.
    assert mined_item.provisional_surrogate != "BFX0008"


def test_mining_does_not_assign_a_named_pool_entry_occurring_verbatim_in_the_transcript():
    # Regression (issue #337, the #292 collision class out of band): mining's
    # ``upsert`` call omitted ``corpus_text`` entirely, so a named pool entry
    # already present as plain prose *elsewhere* in the mined transcript --
    # outside the candidate's own narrow L3 context window (40 chars either
    # side, ``l3._CONTEXT_WINDOW``, which ``upsert`` falls back to when no
    # explicit ``corpus_text`` is given) -- could still be assigned as that
    # transcript's own surrogate. The pool cursor starts at position 0, so
    # with the bug, the very first named entry is handed out unconditionally.
    from blindfold.review import _PROVISIONAL_POOL

    pool_entry = _PROVISIONAL_POOL[0]
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmSetOrg({"Priyanka"}, entity_type=None))

    filler = "x" * 60
    transcript = (
        f"Priyanka filed the report. {filler} Please also cc {pool_entry} "
        "for visibility."
    )
    report = mine_transcripts([transcript], detector, mapping, inbox)

    assert len(report.proposed) == 1
    mined_item = report.proposed[0]
    assert mined_item.real == "Priyanka"
    assert mined_item.provisional_surrogate != pool_entry


def test_engine_and_mining_share_one_known_values_derivation():
    # AC (issue #337): "The engine and mining known-values constructions are
    # one shared derivation (a helper both import)." Asserted directly, not
    # just by construction, so a future edit that gives mining its own copy
    # (the #329/#332 drift lesson this issue's own body names) fails loudly
    # here instead of silently re-diverging.
    assert mining.augmented_known_values is engine.augmented_known_values

    # And the derivation itself agrees with the engine's own mint call site
    # for identical store state -- confirmed reals plus every live inbox
    # item's provisional surrogate and #306 real-word components.
    mapping = SurrogateMapping.from_pairs([("Otto Falk", "Rudi Senner")])
    inbox = ReviewInbox()
    inbox.upsert(real="Nina Ostrowski", context="ctx")

    assert set(engine.augmented_known_values(mapping, inbox)) == set(
        mining.augmented_known_values(mapping, inbox)
    )
