"""Surrogate mapping: the real <-> surrogate registry.

A surrogate is the fake stand-in assigned to an entity. Surrogates are *stable*
(a given entity maps to the same surrogate everywhere) and minting is *idempotent*
(minting an entity that already has a surrogate returns the existing one).

This slice keeps the mapping in-memory and in plaintext on purpose: persistence and
Transit-backed mapping secrecy (leak-audit clause G) are out of scope (issues #3/#10).
"""

from __future__ import annotations

# Hardcoded known person entities for the tracer-bullet slice, with deterministic
# surrogates so the round trip is predictable. Real detection (L1/L2/L3) is out of
# scope; here the entity set is fixed and matched by exact value.
_SEED: dict[str, str] = {
    "Anna Schmidt": "Berta Vogel",
    "Markus Wagner": "Tobias Lehmann",
}

# Plausible fake names used to mint surrogates for novel entities deterministically.
_SURROGATE_POOL: tuple[str, ...] = (
    "Clara Hoffmann",
    "Dieter Kaufmann",
    "Erika Sommer",
    "Felix Baumann",
    "Greta Neumann",
)


class SurrogateMapping:
    """In-memory registry of real -> surrogate assignments."""

    def __init__(self) -> None:
        self._by_real: dict[str, str] = {}

    def seed(self, real: str, surrogate: str) -> None:
        self._by_real[real] = surrogate

    def mint(self, real: str) -> str:
        """Return the surrogate for ``real``, minting a stable one if needed."""
        if real not in self._by_real:
            self._by_real[real] = self._next_surrogate()
        return self._by_real[real]

    def surrogate_for(self, real: str) -> str | None:
        return self._by_real.get(real)

    def pairs(self) -> list[tuple[str, str]]:
        """(real, surrogate) pairs, longest real first for safe exact replacement."""
        return sorted(
            self._by_real.items(), key=lambda kv: len(kv[0]), reverse=True
        )

    def real_values(self) -> list[str]:
        return list(self._by_real.keys())

    def _next_surrogate(self) -> str:
        index = len(self._by_real)
        if index < len(_SURROGATE_POOL):
            return _SURROGATE_POOL[index]
        return f"Surrogate Person {index}"


def seeded_mapping() -> SurrogateMapping:
    mapping = SurrogateMapping()
    for real, surrogate in _SEED.items():
        mapping.seed(real, surrogate)
    return mapping
