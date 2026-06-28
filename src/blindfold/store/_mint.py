"""Deterministic surrogate minting for the cold-start seed.

A surrogate is the fake stand-in assigned to a real referent. For this slice minting is
deterministic by (kind, position-in-seed) so that:

- E-stable: the same referent always mints the SAME surrogate (across repository rebuilds
  and across ETL re-runs), with no randomness or wall-clock dependency.
- the in-process vendored repository and the Postgres ETL compute identical surrogates,
  so the fast (hermetic) round-trip and the DB-backed graph agree.

Coherent surrogate world / reserved-namespace PII (ADR-0005, leak-audit clause E) is out
of scope this slice; these are merely plausible, collision-free stand-ins.
"""

from __future__ import annotations

_PERSON_POOL: tuple[str, ...] = (
    "Bernhard Vogt",
    "Claudia Reinhardt",
    "Dieter Sommer",
    "Elena Fuchs",
    "Stefan Kaiser",
    "Gabriele Wirth",
    "Heinz Lorenz",
    "Iris Hartmann",
)

_TERM_POOL: tuple[str, ...] = (
    "Projekt Polarstern",
    "Vorgang Silberpfeil",
    "Initiative Tannwald",
    "Vorhaben Eichberg",
    "Programm Nordlicht",
    "Projekt Steinadler",
    "Verfahren Lindenhof",
    "Vorhaben Rabenstein",
    "Initiative Falkenberg",
)

_ORG_POOL: tuple[str, ...] = (
    "Brunnen Technik AG",
    "Abteilung Entwicklung Nord",
    "Team Atlas",
    "Gruppe Meridian",
    "Sparte Hofgarten",
    "Bereich Talblick",
)

_POOLS: dict[str, tuple[str, ...]] = {
    "person": _PERSON_POOL,
    "term": _TERM_POOL,
    "org_unit": _ORG_POOL,
}


def mint_surrogate(kind: str, index: int) -> str:
    """Return the stable surrogate for the ``index``-th referent of ``kind``.

    Deterministic: same (kind, index) always yields the same value. Falls back to a
    numbered surrogate once the plausible-name pool is exhausted, so it never collides.
    """
    pool = _POOLS.get(kind, ())
    if index < len(pool):
        return pool[index]
    return f"{kind.title()} Surrogate {index}"
