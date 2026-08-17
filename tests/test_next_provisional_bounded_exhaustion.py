"""Issue #331: the mint-time disjointness walk in ``_next_provisional`` was
unbounded past the named pool. ADR-0052's run-8 deadlock was the original
concrete shape: a live provisional real ("Surrogate") was contained in every
generated fallback label ("Provisional Surrogate {N}"), so the walk collided
with it at every position and never returned -- measured as no result after 3
seconds on 3b245f0.

Issue #330 closed that particular collision by making the fallback an opaque
``BFX{position:04d}`` token with no natural-language word to collide on --
but the walk itself is only bounded, not immune to every possible universal
collision: a known real literally containing the reserved prefix ("BFX")
still collides with every fallback candidate, since they all share that
prefix. This asserts the walk is bounded and fails closed on exhaustion with
its own scrubbed reason (ADR-0009), instead of hanging. Leak-audit: the
exhaustion error carries no plaintext real value -- only the pool ``kind``.
"""

from __future__ import annotations

import time

import pytest

from blindfold.review import ProvisionalPoolExhaustedError, _next_provisional


def test_next_provisional_fails_closed_instead_of_hanging_on_universal_collision():
    # Every fallback candidate past the named "person" pool (position >= 8) is
    # "BFX{position:04d}" -- "BFX" live as a known provisional real collides
    # with every one of them, forever, unless the walk is bounded.
    known_values = ["BFX"]

    start = time.monotonic()
    with pytest.raises(ProvisionalPoolExhaustedError):
        _next_provisional("person", 8, known_values, "")
    elapsed = time.monotonic() - start

    # The measured hang (issue #331) never returned after 3 seconds. A bounded
    # walk must return well inside that.
    assert elapsed < 3


def test_provisional_pool_exhausted_error_names_only_the_pool_kind():
    known_values = ["BFX"]
    try:
        _next_provisional("person", 8, known_values, "")
    except ProvisionalPoolExhaustedError as exc:
        assert "BFX" not in str(exc)
        assert "person" in str(exc)
    else:
        pytest.fail("expected ProvisionalPoolExhaustedError")
