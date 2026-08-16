"""presidio-analyzer as L1's pattern layer (ADR-0003, issue #317).

Mounts presidio-analyzer's deterministic *pattern* recognizers only -- checksum
and check-digit validated global recognizers (IBAN mod-97, credit card Luhn) plus
the four check-digit-validated members of the German ``DE_*`` recognizer set
(Steuer-IdNr, RVNR, KVNR, LANR). ``DE_PLZ``/``DE_KFZ``/``DE_BSNR`` stay unmounted:
they have no check-digit algorithm and are high-false-positive without spaCy
context scoring, which ADR-0003 rejects (see ``PRESIDIO_RECOGNIZERS`` below).

**No NER, ever.** ``presidio_analyzer.AnalyzerEngine`` /
``RecognizerRegistry.load_predefined_recognizers`` both attach a spaCy NLP
recognizer for every supported language even when ``nlp_engine=None`` is passed
(``RecognizerRegistry.add_nlp_recognizer``), so this module never touches either
-- it hand-instantiates only the whitelisted ``PatternRecognizer`` subclasses in
``PRESIDIO_RECOGNIZERS`` and calls each directly with ``nlp_artifacts=None``.
Checksum-backed recognizers score 1.0/0.0 from the validator alone; they were
never designed to need spaCy's context-word enhancement.

**No network egress from L1.** presidio's ``EmailRecognizer.validate_result``
calls the module-level ``tldextract.extract``, which attempts a live
public-suffix-list fetch on first cache miss. ``_OfflineEmailRecognizer``
overrides it to use a ``TLDExtract`` instance constructed with
``suffix_list_urls=()`` -- tldextract's own documented way to disable the fetch
entirely and fall back to its bundled snapshot (see
``tldextract.suffix_list.find_first_response``): with an empty URL tuple, the
fetch loop has nothing to iterate, so no HTTP request is ever attempted,
structurally, not merely "unlikely."

Deliberately NOT mounted this slice: ``PhoneRecognizer`` (the global phone
recognizer). Its ``phonenumbers``-backed matcher (default regions US/GB/DE/FR/
IL/IN/CA/BR, ``leniency=1``) matches unprefixed national-format digit runs --
e.g. a bare structured-ID digit run ("ID: 1234567890") also parses as a
plausible NANP number. Unlike IBAN/credit-card/German-ID, there is no checksum
gain to offset that widened match surface, and L1's own anchored ``+``-prefixed
phone regex (``detection.py``) already covers the checksum-free case precisely
-- mounting it would be exactly the regression ADR-0003's "keep existing L1
regexes ... where behavior would otherwise regress" clause is for.
"""

from __future__ import annotations

import tldextract
from presidio_analyzer import PatternRecognizer
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    DeHealthInsuranceRecognizer,
    DeLanrRecognizer,
    DeSocialSecurityRecognizer,
    DeTaxIdRecognizer,
    EmailRecognizer,
    IbanRecognizer,
)

# tldextract's documented offline pin (see module docstring): an empty
# suffix_list_urls tuple means `find_first_response` never has a URL to fetch,
# so it falls straight to the bundled snapshot every time, never the network.
_OFFLINE_TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())


class _OfflineEmailRecognizer(EmailRecognizer):
    """presidio's EmailRecognizer, pinned to tldextract's bundled PSL snapshot."""

    def validate_result(self, pattern_text: str) -> bool:  # noqa: D102
        return _OFFLINE_TLD_EXTRACTOR(pattern_text).fqdn != ""


# Issue #327 (LEAK, #74 run 8): this recognizer used to back is_valid_email_domain,
# a gate that narrowed detection.py's own regex to FQDN-valid domains only --
# which silently dropped every email on a reserved (RFC 2606) or internal
# (RFC 6762/8375, `.corp`/`.lan`) TLD. That gate is removed; L1's anchored
# `_EMAIL_RE` (detection.py) is the sole, unconditional email detector again.
# Kept mounted here, unused for detection, purely so the NER-exclusion test
# below covers every presidio class this module touches (including the one
# most tempting to wire back in as a "precision" filter).
_OFFLINE_EMAIL_RECOGNIZER = _OfflineEmailRecognizer()

# The whitelist itself: every entry is checksum/check-digit backed and
# structurally excludes NER (see the module docstring and
# test_l1_presidio_registry.py, which pins this against accidental drift).
PRESIDIO_RECOGNIZERS: tuple[PatternRecognizer, ...] = (
    IbanRecognizer(),
    CreditCardRecognizer(),
    _OFFLINE_EMAIL_RECOGNIZER,
    DeTaxIdRecognizer(),
    DeSocialSecurityRecognizer(),
    DeHealthInsuranceRecognizer(),
    DeLanrRecognizer(),
)

# The recognizers actually used to *detect* candidate spans, one PiiSpan per
# occurrence (see detect_presidio_pii) -- everything in PRESIDIO_RECOGNIZERS
# except the email recognizer, which is mounted only for NER-exclusion test
# coverage (see above) and must never independently detect: running it
# alongside detection.py's own regex would double-count every genuine email
# occurrence, breaking detect_pii()'s one-PiiSpan-per-occurrence contract that
# blindfold_devtools/replay.py's offset re-derivation depends on.
_DETECTING_RECOGNIZERS: tuple[PatternRecognizer, ...] = tuple(
    r for r in PRESIDIO_RECOGNIZERS if r is not _OFFLINE_EMAIL_RECOGNIZER
)

# presidio's own entity-type name -> Blindfold's L1 PII `kind` (surrogates.mint_pii).
_ENTITY_TYPE_TO_KIND: dict[str, str] = {
    "IBAN_CODE": "iban",
    "CREDIT_CARD": "credit_card",
    "DE_TAX_ID": "de_tax_id",
    "DE_SOCIAL_SECURITY": "de_social_security",
    "DE_HEALTH_INSURANCE": "de_health_insurance",
    "DE_LANR": "de_lanr",
}


def detect_presidio_pii(text: str) -> list[tuple[str, str]]:
    """Return (kind, value) pairs the checksum-backed presidio recognizers flag
    in ``text``, one pair per occurrence (matching :func:`detect_pii`'s own
    per-occurrence contract).

    Each recognizer is called directly (no ``AnalyzerEngine``/registry seam) so
    no NLP engine is ever constructed -- ``nlp_artifacts=None`` is exactly what
    every mounted recognizer expects, since none of them uses context-word
    enhancement to raise an already-checksum-backed score.
    """
    return [
        (_ENTITY_TYPE_TO_KIND[entity_type], text[result.start : result.end])
        for recognizer in _DETECTING_RECOGNIZERS
        for entity_type in recognizer.supported_entities
        for result in recognizer.analyze(text, [entity_type], nlp_artifacts=None)
    ]
