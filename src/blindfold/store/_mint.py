"""Deterministic surrogate minting for the cold-start seed.

A surrogate is the fake stand-in assigned to a real referent. For this slice minting is
deterministic by (kind, position-in-seed) so that:

- E-stable: the same referent always mints the SAME surrogate (across repository rebuilds
  and across ETL re-runs), with no randomness or wall-clock dependency.
- the in-process vendored repository and the Postgres ETL compute identical surrogates,
  so the fast (hermetic) round-trip and the DB-backed graph agree.

Coherent surrogate world / reserved-namespace PII (ADR-0005, leak-audit clause E) is out
of scope this slice; these are merely plausible, collision-free stand-ins.

Mint-time disjointness (issue #80): a candidate pool/fallback entry is rejected if it
contains a known entity's canonical name or a Variation as a substring -- the same
closed-world set the pre-egress leak gate (``engine.leak_gate``) checks via
``mapping.real_values()``, so mint and gate can never disagree. A rejected entry is
skipped, never reused for a later referent (:func:`mint_surrogates`).
"""

from __future__ import annotations

from collections.abc import Iterable

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

# Pool for learn-time re-mints (issue #81): once a surrogate has been flattened into
# ``SurrogateMapping``'s real -> surrogate registry, the referent's original kind
# (person/term/org_unit) is no longer tracked there, so a replacement can't be drawn
# from a kind-specific pool. Kept disjoint (distinct given names/word tokens) from
# every other pool above and from review.py's ``_PROVISIONAL_POOL`` so a replacement
# never collides with an already-active surrogate from another pool.
_REPLACEMENT_POOL: tuple[str, ...] = (
    "Ruth Vollmer",
    "Wolfgang Ehrlich",
    "Sabine Krug",
    "Norbert Beckmann",
    "Ottilie Rathke",
    "Kurt Steinmetz",
    "Waltraud Nickel",
    "Lorenz Bruckner",
)


def _replacement_pool_entry(position: int) -> str:
    if position < len(_REPLACEMENT_POOL):
        return _REPLACEMENT_POOL[position]
    return f"Replacement Surrogate {position}"


def next_replacement_surrogate(
    start_position: int, known_values: Iterable[str]
) -> tuple[str, int]:
    """The first mint-time-disjoint replacement at or after ``start_position``, and
    the cursor position to resume from on the next call (issue #81).

    Mirrors :func:`_next_provisional` in ``review.py``: walks ``_REPLACEMENT_POOL``
    (falling back to a numbered surrogate once exhausted), skipping any entry that
    collides with ``known_values`` -- the same closed-world set the pre-egress leak
    gate consults -- so a re-minted replacement can never itself be stale on arrival.
    """
    known = list(known_values)
    position = start_position
    while True:
        candidate = _replacement_pool_entry(position)
        position += 1
        if not collides_with_known_entity(candidate, known):
            return candidate, position


def _pool_entry(kind: str, position: int) -> str:
    """The candidate surrogate at raw ``position`` in ``kind``'s pool (or its numbered
    fallback once the pool is exhausted). Pure function of (kind, position) -- no
    collision-skipping -- so :func:`mint_surrogates` can walk positions deterministically.
    """
    pool = _POOLS.get(kind, ())
    if position < len(pool):
        return pool[position]
    return f"{kind.title()} Surrogate {position}"


def collides_with_known_entity(candidate: str, known_values: Iterable[str]) -> bool:
    """True if ``candidate`` contains any known entity value as a substring.

    Mirrors ``engine.leak_gate``'s own check (``real in outbound_text``) so a
    candidate that passes here can never trip the leak gate once minted and
    injected. ``known_values`` is the closed-world set of canonical names and
    Variations -- the same set ``SurrogateMapping.real_values()`` exposes.
    """
    return any(known and known in candidate for known in known_values)


def mint_surrogates(kind: str, count: int, known_values: Iterable[str] = ()) -> list[str]:
    """Return ``count`` deterministic, mint-time-disjoint surrogates for ``kind``, in order.

    Walks the plausible-name pool (falling back to numbered surrogates once exhausted),
    skipping any entry that collides with ``known_values`` (issue #80). A skipped entry
    is never reused for a later referent, and every non-colliding entry keeps the exact
    position it would have had without collision-skipping -- so this is a strict
    superset-preserving refinement of the old positional ``mint_surrogate``: E-stable
    for every referent whose assigned entry never collides.
    """
    known = list(known_values)
    result: list[str] = []
    position = 0
    while len(result) < count:
        candidate = _pool_entry(kind, position)
        position += 1
        if collides_with_known_entity(candidate, known):
            continue
        result.append(candidate)
    return result


def mint_surrogate(kind: str, index: int, known_values: Iterable[str] = ()) -> str:
    """Return the stable, mint-time-disjoint surrogate for the ``index``-th referent
    of ``kind``, given the full ``known_values`` closed-world set (issue #80).

    A thin single-referent wrapper over :func:`mint_surrogates` for callers (the
    Postgres ETL) that mint one referent at a time rather than a whole kind's batch;
    it recomputes the same deterministic walk from position 0 each call, so it agrees
    with :func:`mint_surrogates` on every index.
    """
    return mint_surrogates(kind, index + 1, known_values)[index]
