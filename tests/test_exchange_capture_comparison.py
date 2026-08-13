"""Exchange capture comparison: severity ladder, derived classification (ADR-0047 §9,
issue #256). Grill outcome #247 settled the comparable set -- which real values were
replaced, per exchange, never surrogate identity (surrogate identity is process-
dependent for anything not already in the entity graph, so comparing it would
manufacture noise on nearly every exchange).

Classification is derived, never a curated list, from two structural facts:
1. Is the real value in the entity graph? If yes its surrogate must be stable --
   compare strictly (any divergence is a ``defect``). If no, it is novel and
   divergence is ``expected``.
2. Does the surrogate have reserved-namespace shape? That identifies an L1 PII mint
   from the string alone -- a divergence there is ``expected`` (PII counter-position),
   never a ``defect``, even though PII isn't part of the curated entity graph either.

A surrogate present on neither side of fact 1 or 2 is ``unknown`` -- the tool
admitting it cannot classify, rather than guessing.

Leak-audit clauses: N/A -- this module never touches the request path; it reads
already-captured/witnessed data (ADR-0047 §9's own N/A stance).
"""

from blindfold.detection import Entity
from blindfold_devtools.capture import (
    SECTION_OBSERVED,
    SECTION_RECONSTRUCTED,
    DetectionRecord,
    FooterRecord,
)
from blindfold.surrogates import SurrogateMapping
from blindfold_devtools.capture_comparison import (
    SEVERITY_DEFECT,
    SEVERITY_EXPECTED,
    SEVERITY_UNKNOWN,
    compare,
)


def _footer(injected: dict) -> FooterRecord:
    return FooterRecord(
        section=SECTION_OBSERVED,
        ts="2026-08-12T00:00:01+00:00",
        outcome="passed",
        reason=None,
        duration_ms=42.0,
        upstream_duration_ms=30.0,
        injected=injected,
    )


def _reconstructed_detection(surrogates: tuple[str, ...]) -> DetectionRecord:
    return DetectionRecord(
        section=SECTION_RECONSTRUCTED,
        ts="2026-08-12T00:00:02+00:00",
        hop_index=0,
        hop_kind="user",
        l1_counts={},
        l1_duration_ms=0.0,
        l2_count=0,
        l2_duration_ms=0.0,
        l3_confirmed=0,
        l3_dismissed=0,
        l3_suppressed=0,
        l3_provider=None,
        l3_duration_ms=None,
        surrogates=surrogates,
    )


def test_a_graph_known_entity_replaced_live_but_missed_on_replay_is_a_defect():
    graph = [Entity(canonical="Martin Bach", variations=(), surrogate="Bernhard Vogt")]
    records = [
        _footer({"Bernhard Vogt": "Martin Bach"}),
        # Replay's reconstructed section never reproduces this surrogate.
        _reconstructed_detection(()),
    ]

    result = compare(records, graph_entities=graph)

    assert result.comparable
    assert len(result.divergences) == 1
    assert result.divergences[0].severity == SEVERITY_DEFECT
    assert result.divergences[0].ref == "Bernhard Vogt"


def test_a_novel_entity_divergence_is_expected_not_defect():
    # The graph doesn't know this referent at all -- L3 confirmed it live (a
    # provisional surrogate), but replay's own L3 call is inherently unstable
    # (ADR-0047 §8) and this run didn't reproduce the confirmation.
    records = [
        _footer({"Provisional Person": "Someone Novel"}),
        _reconstructed_detection(()),
    ]

    result = compare(records, graph_entities=[])

    assert result.comparable
    assert len(result.divergences) == 1
    assert result.divergences[0].severity == SEVERITY_EXPECTED
    assert result.divergences[0].ref == "Provisional Person"


def test_a_pii_counter_position_divergence_is_expected_not_defect():
    # mint_pii's per-kind counter is in-process: the same real phone number
    # mints a different reserved-namespace surrogate on live vs. a fresh replay
    # mapping, even though both sides correctly detected and replaced it.
    mapping = SurrogateMapping()
    surrogate = mapping.mint_pii("phone", "+1-202-555-0001")
    records = [
        _footer({surrogate: "+1-202-555-0001"}),
        # Replay's own fresh mapping assigned a different counter position for
        # the same kind, so a *different* reserved-namespace surrogate appears
        # in its reconstructed detection.
        _reconstructed_detection(("+1-555-0107",)),
    ]

    result = compare(records, graph_entities=[])

    assert result.comparable
    assert {d.ref for d in result.divergences} == {surrogate, "+1-555-0107"}
    assert all(d.severity == SEVERITY_EXPECTED for d in result.divergences)


def test_a_surrogate_unattributable_to_the_graph_or_a_reserved_shape_is_unknown():
    records = [
        # Present only in reconstructed; doesn't match any graph surrogate and
        # isn't reserved-namespace shaped -- the tool cannot say whether this
        # is a real defect or expected novelty, and must admit it rather than
        # guess.
        _reconstructed_detection(("Mystery Surrogate",)),
    ]

    result = compare(records, graph_entities=[])

    assert result.comparable
    assert len(result.divergences) == 1
    assert result.divergences[0].severity == SEVERITY_UNKNOWN
    assert result.divergences[0].ref == "Mystery Surrogate"


def test_matching_surrogates_on_both_sides_produce_no_divergence():
    graph = [Entity(canonical="Martin Bach", variations=(), surrogate="Bernhard Vogt")]
    records = [
        _footer({"Bernhard Vogt": "Martin Bach"}),
        _reconstructed_detection(("Bernhard Vogt",)),
    ]

    result = compare(records, graph_entities=graph)
    assert result.comparable
    assert result.divergences == ()


def test_a_capture_with_no_reconstructed_records_at_all_is_not_comparable():
    # A live-only capture (or one whose replay hasn't run yet, #269) carries no
    # `reconstructed` detection records at all -- that must be distinguishable
    # from a replay that genuinely produced zero reconstructed surrogates.
    graph = [Entity(canonical="Martin Bach", variations=(), surrogate="Bernhard Vogt")]
    records = [_footer({"Bernhard Vogt": "Martin Bach"})]

    result = compare(records, graph_entities=graph)

    assert result.comparable is False
    assert result.divergences == ()
