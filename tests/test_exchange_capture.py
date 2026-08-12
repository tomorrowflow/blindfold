"""Exchange capture: JSONL schema, incremental writer, reader (ADR-0047 §3/§5, issue #253).

A Diagnostic session's own artifact -- lives in blindfold_devtools, never in
blindfold.*. One file per exchange, records appended as they occur (header, per-hop
detection, outbound, provider/restored chunks, footer), footer-as-completion-marker,
size-capped with a marked truncated record, count-bounded eviction that never touches
the capture currently being written.
"""

import dataclasses

from blindfold_devtools.capture import (
    SECTION_OBSERVED,
    SECTION_RECONSTRUCTED,
    CaptureWriter,
    DetectionRecord,
    FooterRecord,
    HeaderRecord,
    OutboundRecord,
    ProviderChunkRecord,
    RestoredChunkRecord,
    TruncatedRecord,
    read_capture,
)
from blindfold.engine import HopDetail
from blindfold.processing_trace import ProcessingTraceRecord

# ADR-0047 §3: field vocabulary the Exchange capture schema deliberately reuses
# verbatim from the Processing trace -- separate schema, shared words, so the
# operator never learns e.g. `hop_kind` twice under two names.
SHARED_FIELD_VOCABULARY = (
    "hop_index",
    "hop_kind",
    "endpoint",
    "streamed",
    "outcome",
    "reason",
    "duration_ms",
    "upstream_duration_ms",
    "l3_provider",
)


def _field_names(*dataclass_types) -> set[str]:
    names = set()
    for dc in dataclass_types:
        names.update(f.name for f in dataclasses.fields(dc))
    return names


def test_shared_field_vocabulary_is_spelled_identically_with_processing_trace():
    processing_trace_fields = _field_names(HopDetail, ProcessingTraceRecord)
    capture_fields = _field_names(
        HeaderRecord,
        DetectionRecord,
        OutboundRecord,
        ProviderChunkRecord,
        RestoredChunkRecord,
        FooterRecord,
        TruncatedRecord,
    )

    for name in SHARED_FIELD_VOCABULARY:
        assert name in processing_trace_fields, f"{name!r} missing from the Processing trace schema"
        assert name in capture_fields, f"{name!r} missing from the Exchange capture schema"


def test_header_and_footer_round_trip(tmp_path):
    path = tmp_path / "capture.jsonl"
    writer = CaptureWriter(path)
    writer.write(
        HeaderRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00+00:00",
            capture_id="20260812T000000000000Z-abcd",
            endpoint="messages",
            streamed=False,
            workspace="default",
            inbound_payload={"hello": "world"},
        )
    )
    writer.write(
        FooterRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:01+00:00",
            outcome="passed",
            reason=None,
            duration_ms=12.5,
            upstream_duration_ms=10.0,
            injected={"SURR-1": "Alice"},
        )
    )
    writer.close()

    capture = read_capture(path)

    assert len(capture.records) == 2
    header, footer = capture.records
    assert isinstance(header, HeaderRecord)
    assert header.endpoint == "messages"
    assert header.inbound_payload == {"hello": "world"}
    assert isinstance(footer, FooterRecord)
    assert footer.injected == {"SURR-1": "Alice"}
    assert capture.status == "complete"


def test_every_section_round_trips(tmp_path):
    """Acceptance: a capture round-trips through the writer and reader with
    every section preserved -- one of every record kind, plus a reconstructed
    detection record (as replay, issue #255, would append) alongside the
    observed one for the same hop."""
    path = tmp_path / "capture.jsonl"
    writer = CaptureWriter(path)
    writer.write(
        HeaderRecord(
            section=SECTION_OBSERVED,
            ts="t0",
            capture_id="20260812T000000000000Z-abcd",
            endpoint="messages",
            streamed=True,
            workspace="default",
            inbound_payload={"messages": [{"role": "user", "content": "hi Alice"}]},
        )
    )
    writer.write(
        DetectionRecord(
            section=SECTION_OBSERVED,
            ts="t1",
            hop_index=0,
            hop_kind="user",
            l1_counts={"PERSON": 1},
            l1_duration_ms=1.0,
            l2_count=0,
            l2_duration_ms=0.0,
            l3_confirmed=0,
            l3_dismissed=0,
            l3_suppressed=0,
            l3_provider=None,
            l3_duration_ms=None,
            surrogates=("SURR-1",),
        )
    )
    writer.write(
        DetectionRecord(
            section=SECTION_RECONSTRUCTED,
            ts="t1b",
            hop_index=0,
            hop_kind="user",
            l1_counts={"PERSON": 1},
            l1_duration_ms=1.0,
            l2_count=0,
            l2_duration_ms=0.0,
            l3_confirmed=0,
            l3_dismissed=0,
            l3_suppressed=0,
            l3_provider=None,
            l3_duration_ms=None,
            surrogates=("SURR-1",),
            pass_name="l1_pii",
            offsets=((6, 11),),
        )
    )
    writer.write(
        OutboundRecord(
            section=SECTION_OBSERVED,
            ts="t2",
            payload={"messages": [{"role": "user", "content": "hi SURR-1"}]},
        )
    )
    writer.write(
        ProviderChunkRecord(section=SECTION_OBSERVED, ts="t3", sequence=0, chunk="Hello ")
    )
    writer.write(
        ProviderChunkRecord(section=SECTION_OBSERVED, ts="t4", sequence=1, chunk="SURR-1!")
    )
    writer.write(
        RestoredChunkRecord(section=SECTION_OBSERVED, ts="t5", sequence=0, chunk="Hello ")
    )
    writer.write(
        RestoredChunkRecord(section=SECTION_OBSERVED, ts="t6", sequence=1, chunk="Alice!")
    )
    writer.write(
        FooterRecord(
            section=SECTION_OBSERVED,
            ts="t7",
            outcome="passed",
            reason=None,
            duration_ms=42.0,
            upstream_duration_ms=30.0,
            injected={"SURR-1": "Alice"},
        )
    )
    writer.close()

    capture = read_capture(path)

    assert capture.status == "complete"
    kinds = [r.record for r in capture.records]
    assert kinds == [
        "header",
        "detection",
        "detection",
        "outbound",
        "provider_chunk",
        "provider_chunk",
        "restored_chunk",
        "restored_chunk",
        "footer",
    ]
    observed_detection, reconstructed_detection = capture.records[1], capture.records[2]
    assert observed_detection.section == SECTION_OBSERVED
    assert observed_detection.pass_name is None
    assert reconstructed_detection.section == SECTION_RECONSTRUCTED
    assert reconstructed_detection.pass_name == "l1_pii"
    assert reconstructed_detection.offsets == ((6, 11),)


def test_footerless_file_reads_as_in_flight(tmp_path):
    path = tmp_path / "capture.jsonl"
    writer = CaptureWriter(path)
    writer.write(
        HeaderRecord(
            section=SECTION_OBSERVED,
            ts="t0",
            capture_id="cap-1",
            endpoint="messages",
            streamed=False,
            workspace="default",
            inbound_payload={},
        )
    )
    writer.close()

    capture = read_capture(path)

    assert capture.status == "in-flight"
    assert len(capture.records) == 1


def test_trailing_incomplete_line_reads_up_to_last_complete_record(tmp_path):
    """A file truncated mid-line (e.g. the process died mid-write) reads back
    as everything up to the last complete record, classified in-flight."""
    path = tmp_path / "capture.jsonl"
    writer = CaptureWriter(path)
    writer.write(
        HeaderRecord(
            section=SECTION_OBSERVED,
            ts="t0",
            capture_id="cap-1",
            endpoint="messages",
            streamed=False,
            workspace="default",
            inbound_payload={},
        )
    )
    writer.close()
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"record": "outbound", "section": "observed", "ts": "t1", "payl')

    capture = read_capture(path)

    assert capture.status == "in-flight"
    assert len(capture.records) == 1
    assert capture.records[0].record == "header"


def test_size_cap_produces_marked_truncated_record(tmp_path):
    path = tmp_path / "capture.jsonl"
    writer = CaptureWriter(path, max_bytes=1)
    writer.write(
        HeaderRecord(
            section=SECTION_OBSERVED,
            ts="t0",
            capture_id="cap-1",
            endpoint="messages",
            streamed=False,
            workspace="default",
            inbound_payload={"big": "x" * 100},
        )
    )
    writer.write(
        FooterRecord(
            section=SECTION_OBSERVED,
            ts="t1",
            outcome="passed",
            reason=None,
            duration_ms=1.0,
            upstream_duration_ms=None,
            injected={},
        )
    )
    writer.close()

    capture = read_capture(path)

    assert capture.status == "truncated"
    assert isinstance(capture.records[-1], TruncatedRecord)
    # A completed capture (footer present) must never be mistaken for a truncated one.
    assert not any(r.record == "footer" for r in capture.records)
