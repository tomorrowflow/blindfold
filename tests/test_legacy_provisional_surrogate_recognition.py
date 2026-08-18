"""Issue #336: recognize the legacy ``"Provisional Surrogate {N}"`` fallback
shape in ``_is_fallback_surrogate``, alongside ADR-0052's opaque ``BFX{NNNN}``
form.

#330 replaced the natural-language fallback with the opaque ``BFX`` token and
the reconcile commit (4c1d67a) pointed ``_is_fallback_surrogate`` at
``is_reserved_provisional_surrogate_form`` -- BFX-only. That silently stopped
recognizing the legacy shape anywhere it still exists in durable state: a
legacy label promoted into the entity graph on confirm (``mapping.seed``)
persists forever, independent of ADR-0052's "clear the inbox" consequence,
which only covers uncleared inbox *rows*. A legacy 3-word label paired with an
equal-word-count real re-admits "Provisional"/"Surrogate" as Pass-2 restore
keys via ``_component_restore_map`` -- exactly the corruption #329 fixed, just
via the old label shape instead of the new one.

``is_reserved_provisional_surrogate_form`` itself stays BFX-only (it defines
what may be *minted*); the legacy pattern is recognition-only, matched
directly by ``_is_fallback_surrogate``, and must never become mintable.

Leak-audit clauses: B (restore) is the direct subject -- a legacy-labeled pair
must not donate its own generic words as Pass-2 restore keys. A (egress) is
covered by the mirrored blinding-side guard. F (fail-closed) N/A -- no new
fail-closed path. C/D/E/G N/A -- no mapping-store, verify-pass, or mint-time
change beyond the upsert refusal (mint site itself, ``_provisional_pool_entry``,
is untouched and stays BFX-only).
"""

from __future__ import annotations

import dataclasses

from blindfold import engine
from blindfold.engine import ExchangeSession, restore_response
from blindfold.review import ReviewInbox


def _session_with(injected: dict[str, str]) -> ExchangeSession:
    session = ExchangeSession()
    for surrogate, real in injected.items():
        session.record(surrogate, real)
    return session


def test_legacy_labeled_pairs_own_words_are_not_registered_as_restore_keys():
    # A legacy label promoted on confirm is seeded directly into session.injected
    # via mapping/entity-graph state, never re-minted -- so this constructs the
    # session state directly rather than going through ReviewInbox.upsert (which
    # can no longer mint this shape at all, ADR-0052). Word counts are equal
    # (3 vs. 3) on purpose: without the whole-label skip, positional alignment
    # would register "Provisional" -> "Kestrel" and "Surrogate" -> "Dynamics" as
    # Pass-2 restore keys, corrupting any ordinary later use of those words.
    session = _session_with({"Provisional Surrogate 8": "Kestrel Dynamics Holdings"})

    upstream_response = {
        "content": [
            {
                "type": "text",
                "text": "This is a Provisional Surrogate for testing purposes.",
            }
        ]
    }
    restored = restore_response(upstream_response, session)
    assert (
        restored["content"][0]["text"]
        == "This is a Provisional Surrogate for testing purposes."
    )


def test_legacy_fallback_label_contributes_no_blinding_side_components():
    # Mirrors the restore-side guard above onto the blinding side
    # (_provisional_component_map). A legacy-labeled inbox item can only arise
    # from durable state predating #330 (upsert refuses to mint this shape), so
    # this constructs the item directly rather than through upsert.
    inbox = ReviewInbox()
    inbox.upsert(
        "Kestrel Dynamics Holdings",
        context="...Kestrel Dynamics Holdings reported record profits...",
        entity_type="person",
    )
    item = dataclasses.replace(
        inbox.list()[-1], provisional_surrogate="Provisional Surrogate 8"
    )

    assert engine._provisional_component_map([item]) == {}


def test_upsert_refuses_a_candidate_real_matching_the_legacy_fallback_shape():
    # Mirrors #330's own refusal of a BFX-shaped candidate real (ReviewInbox.upsert
    # returns None, minting nothing) -- a coalesced span could offer the literal
    # legacy template quoted in doc prose as a candidate real.
    inbox = ReviewInbox()

    item = inbox.upsert(
        "Provisional Surrogate 8",
        context="...the fallback template Provisional Surrogate 8...",
        entity_type="person",
    )

    assert item is None
    assert inbox.list() == []
