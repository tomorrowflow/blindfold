"""Offline leak check (ADR-0047 §10, issue #256): the offline twin of the pre-egress
leak gate (``blindfold.engine.leak_gate``) -- exhaustive where the inline gate must be
fast, scanning an Exchange capture's ``observed`` outbound payload for every real value
in ``session.injected`` (the footer's pair table) and every real value the entity graph
knows (``mapping.real_values()``). This validates the inline gate rather than trusting
it, and is the highest-value, do-first piece of this issue -- distinct from #61 (inline
structural re-check) and #78 (residual-content leakage).

Leak-audit clauses: this module IS the leak-audit's own offline verification tool, so
the scrubbed-reason rule (SEC-3) applies to its own output -- a finding must reference
the offending value by surrogate or hash, never plaintext (asserted below).
"""

from blindfold.surrogates import SurrogateMapping
from blindfold_devtools.capture import (
    SECTION_OBSERVED,
    FooterRecord,
    HeaderRecord,
    OutboundRecord,
)
from blindfold_devtools.leak_check import leak_check


def _header() -> HeaderRecord:
    return HeaderRecord(
        section=SECTION_OBSERVED,
        ts="2026-08-12T00:00:00+00:00",
        capture_id="20260812T000000000000Z-abcd",
        endpoint="messages",
        streamed=False,
        workspace="default",
        inbound_payload={"messages": [{"role": "user", "content": "Hi Martin Bach"}]},
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


def test_a_clean_capture_reports_no_leak_and_the_checked_count():
    mapping = SurrogateMapping()
    mapping.seed("Martin Bach", "Bernhard Vogt")
    records = [
        _header(),
        OutboundRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00.5+00:00",
            payload={"messages": [{"role": "user", "content": "Hi Bernhard Vogt"}]},
        ),
        _footer({"Bernhard Vogt": "Martin Bach"}),
    ]

    result = leak_check(records, mapping)

    assert result.findings == ()
    assert result.checked_count == 1
    assert result.summary() == "leak check: no real value found, 1 value(s) checked"


def test_a_real_value_in_the_outbound_payload_is_classified_leak_referenced_only_by_surrogate():
    mapping = SurrogateMapping()
    mapping.seed("Martin Bach", "Bernhard Vogt")
    records = [
        _header(),
        OutboundRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00.5+00:00",
            # A blindfold-engine miss: the real value crossed egress unblindfolded.
            payload={"messages": [{"role": "user", "content": "Hi Martin Bach"}]},
        ),
        _footer({"Bernhard Vogt": "Martin Bach"}),
    ]

    result = leak_check(records, mapping)

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.severity == "leak"
    assert finding.ref == "Bernhard Vogt"
    # The scrubbed-reason rule: the real value must never appear in the finding
    # or its rendered summary.
    assert "Martin Bach" not in finding.ref
    assert "Martin Bach" not in result.summary()


def test_a_graph_known_value_absent_from_this_exchanges_footer_is_still_checked():
    # "every real value the entity graph knows", not only this exchange's own
    # session.injected pair table (ADR-0047 §10) -- a value the graph knows but
    # this particular exchange never happened to inject must still be scanned.
    mapping = SurrogateMapping()
    mapping.seed("Claudia Reinhardt", "Dieter Sommer")
    records = [
        _header(),
        OutboundRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00.5+00:00",
            payload={"messages": [{"role": "user", "content": "cc Claudia Reinhardt"}]},
        ),
        _footer({}),
    ]

    result = leak_check(records, mapping)

    assert len(result.findings) == 1
    assert result.findings[0].ref == "Dieter Sommer"
