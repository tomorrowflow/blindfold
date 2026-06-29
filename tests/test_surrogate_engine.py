"""Surrogate-engine seam tests (ADR-0005, issue #9).

Asserts: coherent surrogate world (person email domain = org domain), reserved-namespace
membership (.invalid domains), date-shift stability and interval preservation, idempotence.

Leak-audit clauses for this slice:
- A/B/C/D/F — N/A: this is a pure seam module, not in the HTTP request path.
- E (reserved-namespace): YES — email domains must be .invalid (never routable). Tested here.
- G (mapping secrecy): N/A — in-memory plaintext, Transit deferred to #10 (ADR-0008).
"""

from datetime import date

import pytest

from blindfold.surrogate_engine import SurrogateEngine


def test_org_mint_produces_reserved_namespace_email_domain():
    engine = SurrogateEngine()
    org = engine.mint("Enervia")
    assert org.email_domain.endswith(".invalid"), (
        f"email_domain {org.email_domain!r} must be in the .invalid reserved namespace"
    )


def test_person_with_employer_shares_employers_email_domain():
    engine = SurrogateEngine()
    org = engine.mint("Enervia")
    person = engine.mint("Martin Bach", relationships={"employer": "Enervia"})

    # Coherent surrogate world (ADR-0005): person's email domain == employer's domain.
    assert person.email_domain == org.email_domain
    assert person.email is not None
    assert person.email.endswith(f"@{org.email_domain}")


def test_mint_is_idempotent_for_same_canonical():
    engine = SurrogateEngine()
    first = engine.mint("Stefan Wegner", relationships={"employer": "Voltwerk"})
    second = engine.mint("Stefan Wegner", relationships={"employer": "Voltwerk"})

    assert first is second  # identical object — no re-minting


def test_date_shift_offset_is_stable_for_same_canonical():
    engine = SurrogateEngine()
    offset_a = engine.date_shift_offset("Martin Bach")
    offset_b = engine.date_shift_offset("Martin Bach")

    assert offset_a == offset_b
    assert -180 <= offset_a <= 180  # within the ±180-day range


def test_date_shift_preserves_intervals_between_events():
    engine = SurrogateEngine()
    d1 = date(2025, 3, 15)
    d2 = date(2025, 6, 20)  # 97 days after d1
    original_gap = (d2 - d1).days

    shifted_d1 = engine.date_shift("Sophie Maier", d1)
    shifted_d2 = engine.date_shift("Sophie Maier", d2)
    shifted_gap = (shifted_d2 - shifted_d1).days

    # Same entity offset applied to both dates → interval is preserved.
    assert shifted_gap == original_gap


def test_two_persons_at_same_org_share_email_domain():
    engine = SurrogateEngine()
    _ = engine.mint("Enervia")
    alice = engine.mint("Andreas Ritter", relationships={"employer": "Enervia"})
    bob = engine.mint("Henning Albers", relationships={"employer": "Enervia"})

    # Both members share the org's fake domain — coherent surrogate world.
    assert alice.email_domain == bob.email_domain
    assert alice.email_domain.endswith(".invalid")


def test_surrogate_name_is_never_the_real_canonical():
    engine = SurrogateEngine()
    real_person = "Martin Bach"
    real_org = "Enervia"

    person = engine.mint(real_person, relationships={"employer": real_org})
    org = engine.mint(real_org)

    assert person.name != real_person
    assert org.name != real_org


def test_employer_minted_implicitly_when_person_is_minted_first():
    """mint(person, {employer}) auto-mints the org if it wasn't minted first."""
    engine = SurrogateEngine()
    person = engine.mint("Sophie Maier", relationships={"employer": "Enervia"})
    org = engine.mint("Enervia")

    # Even though Enervia was minted implicitly, the domain is consistent.
    assert person.email_domain == org.email_domain
