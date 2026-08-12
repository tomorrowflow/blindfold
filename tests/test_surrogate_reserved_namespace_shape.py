"""Reserved-namespace surrogate shape (ADR-0047 §9, issue #256): identifying an L1 PII
mint from a surrogate string alone, with no lookup against the mapping that minted it.
This is the structural fact the offline capture comparison derives its ``expected``
(PII counter-position) classification from, instead of a curated exception list.

Leak-audit clauses: N/A -- pure string-shape predicate, no request-path code touched.
"""

from blindfold.surrogates import SurrogateMapping, is_reserved_namespace_surrogate


def test_every_mint_pii_shape_is_recognized_as_reserved_namespace():
    mapping = SurrogateMapping()
    email = mapping.mint_pii("email", "person@example.com")
    phone = mapping.mint_pii("phone", "+1-202-555-0001")
    iban = mapping.mint_pii("iban", "DE89370400440532013000")
    an_id = mapping.mint_pii("id", "SSN-123-45-6789")
    other = mapping.mint_pii("passport", "P1234567")

    assert is_reserved_namespace_surrogate(email)
    assert is_reserved_namespace_surrogate(phone)
    assert is_reserved_namespace_surrogate(iban)
    assert is_reserved_namespace_surrogate(an_id)
    assert is_reserved_namespace_surrogate(other)


def test_a_seeded_entity_graph_surrogate_is_not_reserved_namespace():
    mapping = SurrogateMapping()
    mapping.seed("Martin Bach", "Bernhard Vogt")

    assert not is_reserved_namespace_surrogate(mapping.surrogate_for("Martin Bach"))
