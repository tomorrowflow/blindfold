"""Reserved-namespace surrogate shape (ADR-0047 §9, issue #256): identifying an L1 PII
mint from a surrogate string alone, with no lookup against the mapping that minted it.
This is the structural fact the offline capture comparison derives its ``expected``
(PII counter-position) classification from, instead of a curated exception list.

Leak-audit clauses: N/A -- pure string-shape predicate, no request-path code touched.
"""

from blindfold.surrogates import (
    SurrogateMapping,
    is_reserved_namespace_surrogate,
    is_reserved_phone_range,
)


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


def test_bare_reserved_line_number_is_a_reserved_phone_range():
    # Issue #369 (ADR-0055): the phone-shaped producer's own matcher never carries
    # an area code or the minted `+1-` prefix for a bare local number -- the
    # reserved-range predicate must still recognize it from the exchange-line
    # pair alone.
    assert is_reserved_phone_range("555-0142")


def test_area_coded_reserved_line_number_is_a_reserved_phone_range():
    # NANPA reserves the 0100..0199 line-number range for fiction under every
    # area code (ADR-0055 Decision 1) -- an area-coded variant is exactly as
    # reserved as the bare form Blindfold mints.
    assert is_reserved_phone_range("415-555-0142")
    assert is_reserved_phone_range("(415) 555-0142")


def test_a_non_reserved_line_number_is_not_a_reserved_phone_range():
    assert not is_reserved_phone_range("555-0242")
    assert not is_reserved_phone_range("415-555-0242")
