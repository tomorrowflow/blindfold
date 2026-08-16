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


def test_l1_email_detection_yields_one_span_per_occurrence_not_per_value():
    """Regression guard (issue #317): adopting presidio's offline-validated
    ``EmailRecognizer`` alongside L1's own anchored regex must narrow (validate),
    never duplicate, detection. ``detect_pii``'s one-PiiSpan-per-occurrence
    contract is relied on by ``blindfold_devtools.replay``'s offset re-derivation
    (test_devtools_replay.py) -- a naive "run both, concatenate" merge would
    double-flag every genuine email occurrence, not just the invalid ones.
    """
    spans = detect_pii(
        "Reach me at alice@example.org, or again alice@example.org if that fails."
    )

    emails = [s for s in spans if s.kind == "email"]
    assert len(emails) == 2
    assert all(e.value == "alice@example.org" for e in emails)


def test_l1_does_not_flag_an_email_lookalike_with_an_invalid_suffix():
    """Validator precision via presidio's offline-pinned tldextract FQDN check
    (issue #317): a syntactically email-shaped string whose domain has no valid
    public suffix is not contactable PII."""
    spans = detect_pii("Reach me at alice@nota.realtld for review.")

    emails = [s for s in spans if s.kind == "email"]
    assert emails == []


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


def test_l1_does_not_flag_a_checksum_broken_iban_lookalike():
    """Adopted presidio-analyzer IbanRecognizer (mod-97), issue #317.

    An IBAN-shaped string that fails the mod-97 checksum is not a real IBAN --
    flagging it would blindfold a value that was never contactable PII, and
    validator precision is exactly what mounting presidio's pattern layer buys.
    The last group's final digit is bumped (00 -> 01), breaking the checksum
    while keeping every structural feature (length, letter/digit layout) intact.
    """
    spans = detect_pii(
        "Please transfer the deposit to DE89 3704 0044 0532 0130 01 by Friday."
    )

    ibans = [s for s in spans if s.kind == "iban"]
    assert ibans == []


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


def test_l1_detects_check_digit_valid_german_steuer_id_nr():
    """Adopted presidio-analyzer's DeTaxIdRecognizer (ISO 7064 Mod 11-10), issue #317.

    ADR-0003 names the German `DE_*` recognizer set as part of L1's discharged
    Presidio debt; Steuer-IdNr is one of the four members enabled (the ones with
    a real check-digit algorithm -- PLZ/KFZ/BSNR stay off, high-false-positive
    and contextless).
    """
    spans = detect_pii("Steuer-IdNr 86095742719 is on file.")

    ids = [s for s in spans if s.kind == "de_tax_id"]
    assert len(ids) == 1
    assert ids[0].value == "86095742719"


def test_l1_does_not_flag_a_check_digit_invalid_steuer_id_nr_lookalike():
    """Validator precision, mirroring the IBAN case: an 11-digit lookalike whose
    final (check) digit is wrong is not a real Steuer-IdNr."""
    spans = detect_pii("Steuer-IdNr 86095742718 is on file.")

    ids = [s for s in spans if s.kind == "de_tax_id"]
    assert ids == []


def test_l1_detects_check_digit_valid_german_rvnr():
    """DE_SOCIAL_SECURITY (Rentenversicherungsnummer) -- the second of the four
    check-digit-validated `DE_*` recognizers ADR-0003 names."""
    spans = detect_pii("RVNR 15070649C103 on file.")

    ids = [s for s in spans if s.kind == "de_social_security"]
    assert len(ids) == 1
    assert ids[0].value == "15070649C103"


def test_l1_does_not_flag_a_check_digit_invalid_rvnr_lookalike():
    spans = detect_pii("RVNR 15070649C104 on file.")

    ids = [s for s in spans if s.kind == "de_social_security"]
    assert ids == []


def test_l1_detects_check_digit_valid_german_kvnr():
    """DE_HEALTH_INSURANCE (Krankenversichertennummer) -- the third of the four."""
    spans = detect_pii("KVNR A000500015 noted.")

    ids = [s for s in spans if s.kind == "de_health_insurance"]
    assert len(ids) == 1
    assert ids[0].value == "A000500015"


def test_l1_does_not_flag_a_check_digit_invalid_kvnr_lookalike():
    spans = detect_pii("KVNR A000500016 noted.")

    ids = [s for s in spans if s.kind == "de_health_insurance"]
    assert ids == []


def test_l1_detects_check_digit_valid_german_lanr():
    """DE_LANR (Lebenslange Arztnummer) -- the fourth of the four."""
    spans = detect_pii("LANR 123456601 assigned.")

    ids = [s for s in spans if s.kind == "de_lanr"]
    assert len(ids) == 1
    assert ids[0].value == "123456601"


def test_l1_does_not_flag_a_check_digit_invalid_lanr_lookalike():
    spans = detect_pii("LANR 123456501 assigned.")

    ids = [s for s in spans if s.kind == "de_lanr"]
    assert ids == []


def test_l1_detects_luhn_valid_credit_card():
    """CreditCardRecognizer (Luhn) -- the other checksum-backed global recognizer
    ADR-0003 names alongside IBAN."""
    spans = detect_pii("My card is 4111 1111 1111 1111 for this order.")

    cards = [s for s in spans if s.kind == "credit_card"]
    assert len(cards) == 1
    assert cards[0].value == "4111 1111 1111 1111"


def test_l1_does_not_flag_a_luhn_invalid_credit_card_lookalike():
    spans = detect_pii("My card is 4111 1111 1111 1112 for this order.")

    cards = [s for s in spans if s.kind == "credit_card"]
    assert cards == []


def test_l1_does_not_mount_de_plz_kfz_or_bsnr():
    """ADR-0003 / issue #317: the contextless, no-check-digit `DE_*` recognizers
    (PLZ, KFZ, BSNR) stay off -- high false-positive rate without spaCy context
    scoring, which this L1 layer deliberately never runs (no NER, ever)."""
    spans = detect_pii(
        "PLZ 10115 Berlin, KFZ B AB 1234, BSNR 021234568 on the letterhead."
    )

    assert [s for s in spans if s.kind in ("de_plz", "de_kfz", "de_bsnr")] == []


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


def test_presidio_detected_german_id_never_egresses_and_stays_stable_across_hops():
    """Leak-audit clauses A + E-stable for a presidio-mounted kind (issue #317):
    the same proof :func:`test_l1_pii_surrogate_is_stable_across_hops` runs for
    the pre-existing regex-backed kinds, run once for a checksum-validated
    German-ID kind to show the new pattern-recognizer path feeds the same
    request-path pipeline, not a special-cased one.
    """
    mapping = SurrogateMapping()
    real_tax_id = "86095742719"
    payload = {
        "model": "claude-3-5-sonnet",
        "system": f"Steuer-IdNr {real_tax_id} on file.",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Confirm Steuer-IdNr {real_tax_id}."}
                ],
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    surrogate = mapping.surrogate_for(real_tax_id)
    assert surrogate is not None
    system_text = blinded["system"]
    user_text = blinded["messages"][0]["content"][0]["text"]

    # Clause A: the real value never survives in any hop.
    assert real_tax_id not in system_text
    assert real_tax_id not in user_text
    # Clause E-stable: the SAME canonical surrogate egresses in both hops.
    assert surrogate in system_text
    assert surrogate in user_text
    # L1 does not re-detect its own reserved-namespace surrogate as fresh PII.
    assert mapping.surrogate_for(surrogate) is None
