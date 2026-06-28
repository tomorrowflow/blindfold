"""L1 detection seam (ADR-0003): deterministic regex detection over the full payload.

L1 catches contactable PII — emails, phones, IBANs, IDs — on every hop before egress.
Detection-seam tests assert *what L1 flags* (the in-process oracle), independent of the
proxy round trip; the round-trip test asserts the network-boundary outcome.

PII surrogates are drawn from **reserved/non-routable namespaces** (ADR-0005 + leak-audit
clause E reserved-namespace): `.invalid` / `.example` domains, NANPA `555-01XX` fictional
range, unassigned ISO 3166 country code for IBANs, an explicit `RESERVED` ID prefix.
That way Blindfold never mints a routable lookalike of a real third party's contact value.
"""

from blindfold.detection import detect_pii
from blindfold.engine import blindfold_payload
from blindfold.surrogates import SurrogateMapping


def test_l1_detects_email_address_in_text():
    spans = detect_pii("Reach me at alice@example.org for review.")

    emails = [s for s in spans if s.kind == "email"]
    assert len(emails) == 1
    assert emails[0].value == "alice@example.org"


def test_email_surrogate_lives_in_a_reserved_non_routable_namespace():
    mapping = SurrogateMapping()

    surrogate = mapping.mint_pii("email", "alice@example.org")

    # RFC 2606 reserves `.invalid` and `.example`: never routable, never deliverable,
    # so blindfolded mail can't accidentally reach a real third party.
    domain = surrogate.split("@", 1)[1]
    assert domain.endswith(".invalid") or domain.endswith(".example")
    # And the surrogate isn't the real address itself.
    assert surrogate != "alice@example.org"


def test_email_surrogate_is_stable_per_value_and_idempotent():
    mapping = SurrogateMapping()
    real = "bob@example.com"

    first = mapping.mint_pii("email", real)
    second = mapping.mint_pii("email", real)
    other = mapping.mint_pii("email", "carol@example.com")

    assert first == second  # idempotent for the same value
    assert other != first  # distinct values get distinct surrogates


def test_l1_detects_international_phone_number():
    spans = detect_pii("Call me on +49 30 1234 5678 tomorrow.")

    phones = [s for s in spans if s.kind == "phone"]
    assert len(phones) == 1
    assert phones[0].value == "+49 30 1234 5678"


def test_phone_surrogate_lives_in_a_reserved_fictional_range():
    mapping = SurrogateMapping()

    surrogate = mapping.mint_pii("phone", "+49 30 1234 5678")

    # NANPA reserves `555-0100` through `555-0199` for fictional use; nothing in that
    # range routes to a real subscriber, so blindfolded calls can't reach a third party.
    assert surrogate.startswith("+1-555-01")
    assert surrogate != "+49 30 1234 5678"


def test_l1_detects_iban():
    spans = detect_pii(
        "Please transfer the deposit to DE89 3704 0044 0532 0130 00 by Friday."
    )

    ibans = [s for s in spans if s.kind == "iban"]
    assert len(ibans) == 1
    assert ibans[0].value == "DE89 3704 0044 0532 0130 00"


def test_iban_surrogate_uses_unassigned_country_code():
    mapping = SurrogateMapping()

    surrogate = mapping.mint_pii("iban", "DE89 3704 0044 0532 0130 00")

    # `XX` is unassigned in ISO 3166-1 alpha-2 — no real bank routes IBANs prefixed
    # with it, so the surrogate cannot collide with a real account anywhere.
    assert surrogate.startswith("XX")
    assert surrogate != "DE89 3704 0044 0532 0130 00"


def test_l1_detects_id_number_with_explicit_prefix():
    # IDs are too varied for a single safe regex; L1 keys on an explicit, structured
    # marker (`ID:` / `ID-`) plus a digit run, leaving free-form numbers to L2/L3.
    spans = detect_pii("Customer record ID: 1234567890 is open.")

    ids = [s for s in spans if s.kind == "id"]
    assert len(ids) == 1
    assert ids[0].value == "ID: 1234567890"


def test_id_surrogate_uses_an_explicit_reserved_prefix():
    mapping = SurrogateMapping()

    surrogate = mapping.mint_pii("id", "ID: 1234567890")

    # `ID-RESERVED-...` makes the synthetic nature unmistakable on inspection and
    # cannot collide with a real customer / national ID format.
    assert surrogate.startswith("ID-RESERVED-")
    assert surrogate != "ID: 1234567890"


def test_l1_pii_surrogate_is_stable_across_hops():
    """Leak-audit clause E-stable: same PII across hops keeps one canonical surrogate.

    Reserved-namespace PII surrogates are themselves PII-shaped (an `.invalid` email is
    still an email, a `+1-555-01XX` phone is still a phone, an `XX99 …` IBAN is still
    an IBAN). When the dict pass on hop 2 replaces a real value with its surrogate from
    hop 1, L1 must NOT then re-blindfold that surrogate as if it were fresh PII —
    doing so would mint a second surrogate for the same entity and break clause
    E-stable ("the same entity maps to the same surrogate everywhere").
    """
    mapping = SurrogateMapping()
    real_email = "contractor@third-party.com"
    payload = {
        "model": "claude-3-5-sonnet",
        "system": f"Contact {real_email} for review.",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Forward to {real_email} please."}
                ],
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    surrogate = mapping.surrogate_for(real_email)
    assert surrogate is not None
    system_text = blinded["system"]
    user_text = blinded["messages"][0]["content"][0]["text"]

    # The real value never survives in any hop.
    assert real_email not in system_text
    assert real_email not in user_text
    # The SAME canonical surrogate is what egresses in both hops.
    assert surrogate in system_text
    assert surrogate in user_text
    # And the engine has not registered the surrogate itself as a second "real" value
    # (which would happen if L1 re-detected its own output).
    assert mapping.surrogate_for(surrogate) is None
