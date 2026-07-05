"""Surrogate mapping seam: stable lookups (leak-audit clause E-stable).

Scope note: this slice covers only E-stable. E reserved-namespace and
coherent-world are N/A here (no PII/relationship surrogates yet); G mapping-secrecy
is N/A (in-memory plaintext mapping, real values plaintext, Transit deferred to #10 /
ADR-0008 — an intentional, ADR-backed deferral, not a leak).
"""

from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


def test_seeded_entity_has_stable_surrogate_across_lookups():
    mapping = _seeded_mapping()
    real = "Martin Bach"

    first = mapping.surrogate_for(real)
    second = mapping.surrogate_for(real)

    assert first is not None
    assert first == second
    # The surrogate must not be the real entity value.
    assert first != real
