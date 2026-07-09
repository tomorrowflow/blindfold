"""Loader for the vendored seeded-allowlist artifact (ADR-0023, issue #71).

A curated data file shipped in the package, one token per line, loaded into the
process-global :class:`~blindfold.review.Allowlist` at startup with semantics
identical to a **learned** reject (ADR-0010): suppresses novelty discovery only,
never protection (see ``select_candidate_spans`` -- a known entity/Term always
wins before the allowlist is even consulted).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_SEED_PATH = Path(__file__).with_name("seeded_allowlist.txt")


@lru_cache(maxsize=1)
def load_seeded_allowlist_tokens() -> frozenset[str]:
    """Return the curated seed tokens (cached; the artifact is immutable at runtime)."""
    tokens: set[str] = set()
    for line in _SEED_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens.add(line)
    return frozenset(tokens)
