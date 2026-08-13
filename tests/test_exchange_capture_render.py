"""``blindfold explain``'s render (ADR-0047 §3/§9/§10, issue #257): a mismatch
banner first (only when the offline leak check or the comparison's severity
ladder found a ``leak``/``defect`` -- a banner that fires on every exchange is
one nobody reads), then hop-by-hop annotated text with each replaced span
marked inline and its surrogate shown, then the summary table.

Leak-audit clauses: N/A -- this module never touches the request path; like
its siblings ``capture_comparison``/``leak_check`` (issue #256) it only reads
already-captured/witnessed data after the fact.
"""

from blindfold.detection import Entity
from blindfold.surrogates import SurrogateMapping
from blindfold_devtools.capture import (
    SECTION_OBSERVED,
    FooterRecord,
    HeaderRecord,
    OutboundRecord,
)
from blindfold_devtools.capture_render import render_capture


def _header(inbound_payload: dict) -> HeaderRecord:
    return HeaderRecord(
        section=SECTION_OBSERVED,
        ts="2026-08-12T00:00:00+00:00",
        capture_id="20260812T000000000000Z-aaaa",
        endpoint="messages",
        streamed=False,
        workspace="default",
        inbound_payload=inbound_payload,
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


def test_an_in_flight_capture_states_what_is_missing_rather_than_erroring():
    records = [_header({"messages": [{"role": "user", "content": "Hi Martin Bach"}]})]

    rendered = render_capture(records, graph_entities=[], mapping=SurrogateMapping())

    assert "in-flight" in rendered
    assert "no footer" in rendered or "missing" in rendered


def test_a_leak_renders_the_mismatch_banner_before_anything_else():
    mapping = SurrogateMapping()
    mapping.seed("Martin Bach", "Bernhard Vogt")
    records = [
        _header({"messages": [{"role": "user", "content": "Hi Martin Bach"}]}),
        # A blindfold-engine miss: the real value crossed egress unblindfolded.
        OutboundRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00.5+00:00",
            payload={"messages": [{"role": "user", "content": "Hi Martin Bach"}]},
        ),
        _footer({"Bernhard Vogt": "Martin Bach"}),
    ]

    rendered = render_capture(records, graph_entities=[], mapping=mapping)

    assert rendered.strip().startswith("MISMATCH")
    assert "leak" in rendered.lower()
    # The scrubbed-reason rule (SEC-3): the real value must never appear in the render.
    assert "Martin Bach" not in rendered.split("\n")[0]


def test_a_defect_with_no_leak_also_renders_the_mismatch_banner():
    # A graph-known entity replaced live but missed on replay (no leak at all --
    # the offline leak check finds nothing) still counts as a `defect`, the
    # severity ladder's other banner-worthy tier (ADR-0047 §9).
    records = [
        _header({"messages": [{"role": "user", "content": "Hi Martin Bach"}]}),
        OutboundRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00.5+00:00",
            payload={"messages": [{"role": "user", "content": "Hi Bernhard Vogt"}]},
        ),
        _footer({"Bernhard Vogt": "Martin Bach"}),
    ]

    rendered = render_capture(
        records,
        graph_entities=[Entity(canonical="Martin Bach", variations=(), surrogate="Bernhard Vogt")],
        mapping=SurrogateMapping(),
    )

    assert rendered.strip().startswith("MISMATCH")
    assert "defect" in rendered.lower()


def test_only_expected_divergences_render_no_banner():
    # A novel entity, never in the graph -- compare() classifies its own
    # divergence as `expected`, never `defect` (ADR-0047 §9). No leak either.
    records = [
        _header({"messages": [{"role": "user", "content": "Hi Someone Novel"}]}),
        OutboundRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00.5+00:00",
            payload={"messages": [{"role": "user", "content": "Hi Provisional Person"}]},
        ),
        _footer({"Provisional Person": "Someone Novel"}),
    ]

    rendered = render_capture(records, graph_entities=[], mapping=SurrogateMapping())

    assert "MISMATCH" not in rendered


def test_a_replaced_span_is_marked_inline_with_its_surrogate():
    from blindfold_devtools.capture import SECTION_RECONSTRUCTED, DetectionRecord

    records = [
        _header({"messages": [{"role": "user", "content": "Hi Martin Bach, it's Martin Bach again"}]}),
        OutboundRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00.5+00:00",
            payload={
                "messages": [
                    {"role": "user", "content": "Hi Bernhard Vogt, it's Bernhard Vogt again"}
                ]
            },
        ),
        # Replay reproduces the same surrogate for this hop -- no divergence,
        # so this test isolates the hop-annotation behavior from the banner.
        DetectionRecord(
            section=SECTION_RECONSTRUCTED,
            ts="2026-08-12T00:00:00.7+00:00",
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
            surrogates=("Bernhard Vogt",),
        ),
        _footer({"Bernhard Vogt": "Martin Bach"}),
    ]

    rendered = render_capture(
        records,
        graph_entities=[Entity(canonical="Martin Bach", variations=(), surrogate="Bernhard Vogt")],
        mapping=SurrogateMapping(),
    )

    assert "MISMATCH" not in rendered
    # Both occurrences of the real value are marked, each showing its surrogate.
    assert rendered.count("[Martin Bach -> Bernhard Vogt]") == 2


def test_reconstructed_detection_fields_are_visibly_distinguished_from_observed():
    from blindfold_devtools.capture import SECTION_RECONSTRUCTED, DetectionRecord

    records = [
        _header({"messages": [{"role": "user", "content": "Hi Martin Bach"}]}),
        OutboundRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00.5+00:00",
            payload={"messages": [{"role": "user", "content": "Hi Bernhard Vogt"}]},
        ),
        DetectionRecord(
            section=SECTION_RECONSTRUCTED,
            ts="2026-08-12T00:00:00.7+00:00",
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
            surrogates=("Bernhard Vogt",),
            pass_name="l2_dict",
            offsets=((3, 14),),
        ),
        _footer({"Bernhard Vogt": "Martin Bach"}),
    ]

    rendered = render_capture(
        records,
        graph_entities=[Entity(canonical="Martin Bach", variations=(), surrogate="Bernhard Vogt")],
        mapping=SurrogateMapping(),
    )

    assert "reconstructed" in rendered
    assert "l2_dict" in rendered
    # The reconstructed line is visibly its own line, distinct from the observed
    # hop line above it.
    lines = rendered.split("\n")
    hop_line = next(line for line in lines if line.startswith("hop 0"))
    reconstructed_line = next(line for line in lines if "reconstructed" in line)
    assert hop_line != reconstructed_line


def test_an_unwired_l3_replay_notes_l1_l2_only_in_the_summary():
    from blindfold_devtools.capture import SECTION_RECONSTRUCTED, DetectionRecord

    records = [
        _header({"messages": [{"role": "user", "content": "Hi Martin Bach"}]}),
        OutboundRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00.5+00:00",
            payload={"messages": [{"role": "user", "content": "Hi Bernhard Vogt"}]},
        ),
        DetectionRecord(
            section=SECTION_RECONSTRUCTED,
            ts="2026-08-12T00:00:00.7+00:00",
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
            surrogates=("Bernhard Vogt",),
            l3_wired=False,
        ),
        _footer({"Bernhard Vogt": "Martin Bach"}),
    ]

    rendered = render_capture(
        records,
        graph_entities=[Entity(canonical="Martin Bach", variations=(), surrogate="Bernhard Vogt")],
        mapping=SurrogateMapping(),
    )

    assert "L1/L2-only" in rendered


def test_a_wired_l3_replay_does_not_note_l1_l2_only():
    from blindfold_devtools.capture import SECTION_RECONSTRUCTED, DetectionRecord

    records = [
        _header({"messages": [{"role": "user", "content": "Hi Martin Bach"}]}),
        OutboundRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00.5+00:00",
            payload={"messages": [{"role": "user", "content": "Hi Bernhard Vogt"}]},
        ),
        DetectionRecord(
            section=SECTION_RECONSTRUCTED,
            ts="2026-08-12T00:00:00.7+00:00",
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
            surrogates=("Bernhard Vogt",),
            l3_wired=True,
        ),
        _footer({"Bernhard Vogt": "Martin Bach"}),
    ]

    rendered = render_capture(
        records,
        graph_entities=[Entity(canonical="Martin Bach", variations=(), surrogate="Bernhard Vogt")],
        mapping=SurrogateMapping(),
    )

    assert "L1/L2-only" not in rendered
