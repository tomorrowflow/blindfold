"""presidio-analyzer mount constraints (ADR-0003, issue #317).

L1 adopts presidio-analyzer as its pattern layer -- checksum/check-digit backed
recognizers only. Two hard constraints from the issue, each enforced structurally
here rather than by convention:

- **No NER, ever**: never a spaCy/GLiNER/transformers/stanza recognizer. ADR-0003
  deliberately rejected full-document ML detection; L1 stays deterministic.
- **No network egress from L1**: presidio's ``EmailRecognizer.validate_result``
  calls ``tldextract.extract``, which attempts a live public-suffix-list fetch on
  cache miss. ``_OfflineEmailRecognizer`` must never let that reach the network.
"""

from presidio_analyzer import PatternRecognizer
from presidio_analyzer.predefined_recognizers import NLP_RECOGNIZERS

from blindfold.l1_presidio import PRESIDIO_RECOGNIZERS


def test_presidio_mount_contains_no_ner_or_ml_recognizer():
    # Every checksum/check-digit recognizer presidio ships is a PatternRecognizer
    # subclass; every NER/ML recognizer (spaCy, stanza, transformers, GLiNER,
    # Azure/OpenAI/HuggingFace/LangExtract) is not -- so this isinstance check
    # alone structurally excludes the whole NER/ML family, not just the three
    # named in NLP_RECOGNIZERS.
    forbidden_classes = tuple(NLP_RECOGNIZERS.values())
    assert PRESIDIO_RECOGNIZERS, "the mount must not be empty"
    for recognizer in PRESIDIO_RECOGNIZERS:
        assert isinstance(recognizer, PatternRecognizer), (
            f"{recognizer!r} is not a PatternRecognizer -- NER/ML recognizers "
            "never are"
        )
        assert not isinstance(recognizer, forbidden_classes), (
            f"{recognizer!r} is one of presidio's NLP_RECOGNIZERS"
        )


def test_presidio_mount_excludes_de_plz_kfz_bsnr_by_construction():
    """The three contextless German recognizers are never even instantiated --
    not merely filtered out at detection time."""
    entity_types = {
        entity_type
        for recognizer in PRESIDIO_RECOGNIZERS
        for entity_type in recognizer.supported_entities
    }
    assert entity_types.isdisjoint({"DE_PLZ", "DE_KFZ", "DE_BSNR"})


def test_offline_email_recognizer_never_performs_a_live_public_suffix_list_fetch(
    monkeypatch,
):
    """The offline-pinned validator must never reach the network, even if a live
    fetch would otherwise be attempted -- proven by making any such attempt raise.
    """

    def _blow_up(*args, **kwargs):
        raise AssertionError("a live network fetch was attempted")

    monkeypatch.setattr("requests.Session.request", _blow_up)
    monkeypatch.setattr("requests.Session.get", _blow_up)

    email_recognizer = next(
        r for r in PRESIDIO_RECOGNIZERS if "EMAIL_ADDRESS" in r.supported_entities
    )
    result = email_recognizer.analyze(
        "Reach me at alice@example.org for review.",
        ["EMAIL_ADDRESS"],
        nlp_artifacts=None,
    )

    assert len(result) == 1
    assert result[0].score == 1.0


def test_offline_email_recognizer_still_rejects_an_invalid_suffix_lookalike():
    """Validator precision, mirroring IBAN/German-ID: an email-shaped string whose
    domain has no valid public suffix is not flagged -- proves the offline pin
    still validates against the real (bundled-snapshot) suffix list, not a stub
    that accepts everything."""
    email_recognizer = next(
        r for r in PRESIDIO_RECOGNIZERS if "EMAIL_ADDRESS" in r.supported_entities
    )
    result = email_recognizer.analyze(
        "Reach me at alice@nota.realtld for review.",
        ["EMAIL_ADDRESS"],
        nlp_artifacts=None,
    )

    assert result == []
