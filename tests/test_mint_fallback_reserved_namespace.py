"""Issue #335: apply ADR-0052's invariant to ``store/_mint.py``'s two sibling
fallback paths -- ``next_replacement_surrogate`` (learn-time re-mints, #81) and
``mint_surrogates``' ``_pool_entry`` (kind-pool exhaustion) -- which still
minted natural-language, corpus-unchecked labels (``"Replacement Surrogate
{N}"`` / ``"{Kind} Surrogate {N}"``) after #330/#331/#332 closed the identical
defect on the review-inbox provisional path (``BFX{NNNN}``).

Root cause mirrors #328/#330 exactly: both words -- "Replacement", "Surrogate",
a kind's own title-cased name -- are ordinary project vocabulary, so a known
real containing one collides with every fallback candidate, and
``next_replacement_surrogate``'s walk was a bare ``while True`` with no bound
at all -- the exact #331 hang, one path over.

Leak-audit clauses:
- A: a fallback surrogate that could itself trip the leak gate is never
  minted -- proven here by construction (opaque, whitespace-free, no
  natural-language word) rather than by adjudicating a collision after mint.
- D: N/A directly (no request-path round trip in this module), but the fixed
  shape is what keeps a future request-path round trip leak-gate-clean.
- F (fail-closed): both walks are bounded and raise a scrubbed exhaustion
  error instead of hanging.
N/A this slice: B/C (restore semantics unchanged -- opaque tokens are
whitespace-free, so they already decompose into no surrogate components, the
same property #330's BFX shape has), G (mapping secrecy, unrelated).
"""

from __future__ import annotations

import inspect
import time

import pytest

from blindfold.review import ReviewInbox, is_reserved_provisional_surrogate_form
from blindfold.store import _mint
from blindfold.store._mint import mint_surrogates, next_replacement_surrogate

_BOUND = 3


def _exact_match_known_values(prefix: str, start_position: int, attempts: int) -> list[str]:
    # Mirrors tests/test_next_provisional_bounded_exhaustion.py's helper: since
    # mint-time collision matching is word-boundary (issue #293/#332), a known
    # real can only collide with a fallback candidate's own exact text -- so
    # forcing a universal collision across the bound means naming every
    # candidate in range explicitly, not relying on a shared prefix.
    return [
        f"{prefix}{position:04d}" for position in range(start_position, start_position + attempts)
    ]


def test_next_replacement_surrogate_past_pool_exhaustion_is_opaque_not_natural_language():
    # AC1: past _REPLACEMENT_POOL's 8 entries, the fallback must carry no
    # natural-language word ("Replacement", "Surrogate") and no free-standing
    # integer -- a single opaque ASCII token, mirroring BFX{NNNN}'s shape.
    surrogate, _next_position = next_replacement_surrogate(8, known_values=[])

    assert surrogate != "Replacement Surrogate 8"
    assert "Replacement" not in surrogate
    assert "Surrogate" not in surrogate
    assert " " not in surrogate


def test_mint_surrogates_past_kind_pool_exhaustion_is_opaque_not_natural_language():
    # AC2: past a kind's named pool (8 entries for "person"), the fallback
    # must likewise carry no natural-language word and no whitespace.
    minted = mint_surrogates("person", 9)  # 9th entry is past the 8-entry pool
    fallback = minted[8]

    assert fallback != "Person Surrogate 8"
    assert "Person" not in fallback
    assert "Surrogate" not in fallback
    assert " " not in fallback


def test_no_two_paths_or_kinds_render_an_identical_fallback_token_at_the_same_position():
    # AC3: uniqueness across families/kinds must hold BY CONSTRUCTION (a
    # distinct marker per path/kind), never by cursor luck -- the issue's own
    # warning that today person-cursor-8 and org-cursor-8 both render
    # "BFX0008" on the review path. Mint past exhaustion on every store._mint
    # path/kind at the SAME raw position and assert they are pairwise distinct.
    position = 8
    replacement_token, _ = next_replacement_surrogate(position, known_values=[])
    person_token = mint_surrogates("person", position + 1)[position]
    term_token = mint_surrogates("term", position + 1)[position]
    org_unit_token = mint_surrogates("org_unit", position + 1)[position]

    tokens = [replacement_token, person_token, term_token, org_unit_token]
    assert len(tokens) == len(set(tokens))


def test_next_replacement_surrogate_fails_closed_instead_of_hanging_on_universal_collision(
    monkeypatch,
):
    # AC1 regression (the issue's own headline defect): before this fix,
    # `next_replacement_surrogate`'s walk was a bare `while True` -- a known
    # real colliding with every fallback candidate hung the proxy forever,
    # the exact #331 defect one path over. Bound the walk (mirroring
    # review._MAX_FALLBACK_ATTEMPTS) and assert it fails closed well inside
    # the 3s window #331's own measured hang never returned within.
    monkeypatch.setattr(_mint, "_MAX_FALLBACK_ATTEMPTS", _BOUND)
    known_values = _exact_match_known_values(_mint.REPLACEMENT_FALLBACK_PREFIX, 8, _BOUND)

    start = time.monotonic()
    with pytest.raises(_mint.FallbackSurrogatePoolExhaustedError):
        next_replacement_surrogate(8, known_values=known_values)
    elapsed = time.monotonic() - start

    assert elapsed < 3


def test_mint_surrogates_fails_closed_instead_of_hanging_on_universal_collision(monkeypatch):
    # AC2 regression: `mint_surrogates`' internal walk for a single referent
    # (once its kind pool is exhausted) had the same unbounded shape --
    # `while len(result) < count` never advances `result` if every candidate
    # from the current position onward collides.
    monkeypatch.setattr(_mint, "_MAX_FALLBACK_ATTEMPTS", _BOUND)
    known_values = _exact_match_known_values(_mint.POOL_FALLBACK_PREFIXES["person"], 8, _BOUND)

    start = time.monotonic()
    with pytest.raises(_mint.FallbackSurrogatePoolExhaustedError):
        mint_surrogates("person", 9, known_values=known_values)
    elapsed = time.monotonic() - start

    assert elapsed < 3


def test_fallback_pool_exhausted_error_names_only_the_path_never_a_value():
    # Fail-closed message discipline (ADR-0009): the exhaustion error must
    # name only the exhausted path/kind, never a real value or a candidate
    # surrogate -- mirroring review.ProvisionalPoolExhaustedError.
    exc = _mint.FallbackSurrogatePoolExhaustedError("replacement")
    assert "replacement" in str(exc)

    exc = _mint.FallbackSurrogatePoolExhaustedError("person")
    assert "person" in str(exc)


@pytest.mark.parametrize(
    "prefix",
    [
        _mint.REPLACEMENT_FALLBACK_PREFIX,
        _mint.POOL_FALLBACK_PREFIXES["person"],
        _mint.POOL_FALLBACK_PREFIXES["term"],
        _mint.POOL_FALLBACK_PREFIXES["org_unit"],
    ],
)
def test_recognizer_covers_the_whole_reserved_family_not_only_review_pys_bfx(prefix):
    # ADR-0052 decision 2 / this issue's AC4: the single-source-of-truth
    # recognizer must be extended so the WHOLE family is syntactically closed
    # against minting everywhere BFX already is -- not just the review-inbox
    # path's own prefix.
    candidate = f"{prefix}0008"
    assert is_reserved_provisional_surrogate_form(candidate)


@pytest.mark.parametrize(
    "prefix",
    [
        _mint.REPLACEMENT_FALLBACK_PREFIX,
        _mint.POOL_FALLBACK_PREFIXES["person"],
        _mint.POOL_FALLBACK_PREFIXES["term"],
        _mint.POOL_FALLBACK_PREFIXES["org_unit"],
    ],
)
def test_review_inbox_upsert_refuses_a_candidate_matching_any_reserved_family_member(prefix):
    # AC4 end-to-end: a candidate real matching ANY reserved-family shape
    # (not just review.py's own BFX) must be refused by ReviewInbox.upsert --
    # mirroring test_opaque_reserved_surrogate.py's existing BFX regression.
    inbox = ReviewInbox()
    candidate = f"{prefix}0008"

    item = inbox.upsert(candidate, context=f"...{candidate} appears in the doc...")

    assert item is None
    assert inbox.list() == []


def test_mint_surrogates_falls_back_to_the_default_reserved_prefix_for_an_unlisted_kind():
    # Defensive: a kind absent from _POOLS/POOL_FALLBACK_PREFIXES (empty pool,
    # so every position is immediately past-pool) must still mint an opaque
    # reserved-family token via DEFAULT_POOL_FALLBACK_PREFIX, not crash and
    # not fall back to a natural-language label.
    minted = mint_surrogates("unlisted_kind", 1)

    assert minted[0] == f"{_mint.DEFAULT_POOL_FALLBACK_PREFIX}0000"
    assert is_reserved_provisional_surrogate_form(minted[0])


def test_legacy_natural_language_fallback_shapes_are_never_minted_anywhere():
    # AC5, grep-level: the two natural-language shapes this issue replaces
    # ("Replacement Surrogate {N}" / "{Kind} Surrogate {N}") must not appear
    # as literal string templates in store._mint's source anymore.
    source = inspect.getsource(_mint)
    assert "Replacement Surrogate" not in source
    assert "Surrogate {position}" not in source
    assert "Surrogate {" not in source
