"""Issue #331: the mint-time disjointness walk in ``_next_provisional`` was
unbounded past the named pool. ADR-0052's run-8 deadlock was the original
concrete shape: a live provisional real ("Surrogate") was contained in every
generated fallback label ("Provisional Surrogate {N}"), so the walk collided
with it at every position and never returned -- measured as no result after 3
seconds on 3b245f0.

Issue #330 closed that particular collision by making the fallback an opaque
``BFX{position:04d}`` token with no natural-language word to collide on. At
the time, a known real literally containing the reserved prefix ("BFX") still
collided with every fallback candidate under substring matching, since they
all shared that prefix -- but issue #332 aligned the mint-time collision
check (``collides_with_known_entity``) to the leak gate's word-boundary rule,
and a bare prefix like "BFX" has no word boundary before the digits of
"BFX0008" (both are ``\\w`` characters), so it no longer collides with any
fallback candidate at all. That is the intended immunity ADR-0052 describes,
not a regression -- but it means a *single* known real can no longer force a
universal collision, so exhausting the walk here requires a known real for
every candidate the (patched-down) bound will try. This still asserts the
walk is bounded and fails closed on exhaustion with its own scrubbed reason
(ADR-0009), instead of hanging. Leak-audit: the exhaustion error carries no
plaintext real value -- only the pool ``kind``.
"""

from __future__ import annotations

import time

import pytest

from blindfold import review
from blindfold.review import ProvisionalPoolExhaustedError, _next_provisional

_BOUND = 3


def _exact_match_known_values(start_position: int, attempts: int) -> list[str]:
    # A known real that word-boundary-matches a fallback candidate can now
    # only be the candidate's own exact text (#332) -- so forcing a collision
    # at every position the bounded walk will try means naming every one of
    # them explicitly, not relying on a shared prefix.
    return [f"BFX{position:04d}" for position in range(start_position, start_position + attempts)]


def test_next_provisional_fails_closed_instead_of_hanging_on_universal_collision(monkeypatch):
    monkeypatch.setattr(review, "_MAX_FALLBACK_ATTEMPTS", _BOUND)
    known_values = _exact_match_known_values(8, _BOUND)

    start = time.monotonic()
    with pytest.raises(ProvisionalPoolExhaustedError):
        _next_provisional("person", 8, known_values, "")
    elapsed = time.monotonic() - start

    # The measured hang (issue #331) never returned after 3 seconds. A bounded
    # walk must return well inside that.
    assert elapsed < 3


def test_provisional_pool_exhausted_error_names_only_the_pool_kind(monkeypatch):
    monkeypatch.setattr(review, "_MAX_FALLBACK_ATTEMPTS", _BOUND)
    known_values = _exact_match_known_values(8, _BOUND)
    try:
        _next_provisional("person", 8, known_values, "")
    except ProvisionalPoolExhaustedError as exc:
        assert "BFX" not in str(exc)
        assert "person" in str(exc)
    else:
        pytest.fail("expected ProvisionalPoolExhaustedError")
