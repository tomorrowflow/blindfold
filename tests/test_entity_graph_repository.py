"""Entity-graph repository seam (in-process, no DB): the proxy's source of seeded
(real entity value -> surrogate) pairs.

This is the swappable seam the proxy depends on to build its SurrogateMapping from the
entity graph instead of a hardcoded dict. The vendored-seed implementation reads the
canonical cold-start dataset (derived from voice-diary, ADR-0012) WITHOUT a database, so
the fast request-path test stays hermetic.

Leak-audit clauses exercised here:
- E-stable / idempotent mint: a seeded referent maps to the SAME surrogate every time.
- A (precondition): the surrogate is never the real entity value.
N/A this slice: G mapping-secrecy (real values are plaintext, Transit deferred to #10 /
ADR-0008 — an intentional, ADR-backed deferral, not a leak); E reserved-namespace /
coherent-world and F fail-closed (no PII/relationship surrogate engine, no L3 yet).
"""

from blindfold.store import vendored_seed_repository


def test_vendored_repo_yields_stable_surrogate_for_a_seeded_person_canonical_value():
    repo = vendored_seed_repository()

    pairs = dict(repo.seeded_pairs())

    real = "Martin Bach"
    assert real in pairs
    surrogate = pairs[real]
    # The surrogate must never be the real entity value (clause A precondition).
    assert surrogate != real
    assert surrogate

    # E-stable: building the repository again yields the SAME surrogate.
    again = dict(vendored_seed_repository().seeded_pairs())
    assert again[real] == surrogate


def test_every_person_variation_is_paired_with_its_referents_surrogate():
    # Coreference (ADR-0004): "Martin" and "Bach" are the same referent as
    # "Martin Bach", so they all map to that referent's single surrogate.
    pairs = dict(vendored_seed_repository().seeded_pairs())

    canonical_surrogate = pairs["Martin Bach"]
    for variation in ("Martin", "Bach"):
        assert variation in pairs, f"variation not seeded: {variation!r}"
        assert pairs[variation] == canonical_surrogate
        # Clause A precondition: a variation's surrogate is never the real value.
        assert pairs[variation] != variation


def test_terms_and_their_variations_are_seeded_with_a_stable_surrogate():
    pairs = dict(vendored_seed_repository().seeded_pairs())

    term = "Enervia"
    assert term in pairs
    term_surrogate = pairs[term]
    assert term_surrogate
    assert term_surrogate != term
    # An ASR/spelling variation of the term shares the term's surrogate (coreference).
    assert pairs["Enerva"] == term_surrogate
