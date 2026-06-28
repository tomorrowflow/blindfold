"""Surrogate-engine seam: stable + idempotent mint (leak-audit clause E-stable).

Scope note: this slice covers only E-stable/idempotent. E reserved-namespace and
coherent-world are N/A here (no PII/relationship surrogates yet); G mapping-secrecy
is N/A (in-memory plaintext mapping, no persistence/crypto this slice).
"""

from blindfold.surrogates import seeded_mapping


def test_seeded_entity_has_stable_surrogate_across_lookups():
    mapping = seeded_mapping()
    real = "Stefan Wegner"

    first = mapping.surrogate_for(real)
    second = mapping.surrogate_for(real)

    assert first is not None
    assert first == second
    # The surrogate must not be the real entity value.
    assert first != real


def test_minting_an_existing_entity_returns_the_same_surrogate():
    mapping = seeded_mapping()
    real = "Markus Eberhardt"

    minted_once = mapping.mint(real)
    minted_again = mapping.mint(real)

    assert minted_once == minted_again
    assert minted_once == mapping.surrogate_for(real)


def test_minting_a_novel_entity_is_idempotent_and_stable():
    mapping = seeded_mapping()
    novel = "Wilhelm Brandt"

    first = mapping.mint(novel)
    second = mapping.mint(novel)

    assert first == second
    assert first != novel
