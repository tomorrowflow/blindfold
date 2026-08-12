"""Captures listing (ADR-0047 §6 selection, issue #257).

``blindfold captures`` is a printed table over the capture directory: id, time,
endpoint, hop count, detected count, outcome, and a truncated excerpt of the
first user hop. A footer-less capture (no completion marker) shows as
``in-flight`` rather than erroring or being silently skipped -- the listing's
whole point is to surface exactly the exchange that is still running.

Leak-audit clauses: N/A -- this module never touches the request path; it
only reads already-captured/witnessed data off disk (mirrors #256's own
N/A stance for capture_comparison/leak_check).
"""

from blindfold_devtools.capture import (
    SECTION_OBSERVED,
    CaptureWriter,
    FooterRecord,
    HeaderRecord,
)
from blindfold_devtools.capture_listing import list_captures


def _write_header_only(path, *, capture_id: str, endpoint: str = "messages") -> None:
    writer = CaptureWriter(path)
    writer.write(
        HeaderRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00+00:00",
            capture_id=capture_id,
            endpoint=endpoint,
            streamed=False,
            workspace="default",
            inbound_payload={"messages": [{"role": "user", "content": "Hi Martin Bach"}]},
        )
    )
    writer.close()


def test_a_footerless_capture_is_listed_as_in_flight(tmp_path):
    _write_header_only(tmp_path / "20260812T000000000000Z-aaaa.jsonl", capture_id="20260812T000000000000Z-aaaa")

    summaries = list_captures(tmp_path)

    assert len(summaries) == 1
    assert summaries[0].id == "20260812T000000000000Z-aaaa"
    assert summaries[0].outcome == "in-flight"


def test_a_complete_capture_lists_endpoint_hop_count_detected_count_and_excerpt(tmp_path):
    path = tmp_path / "20260812T000000000000Z-bbbb.jsonl"
    writer = CaptureWriter(path)
    writer.write(
        HeaderRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00+00:00",
            capture_id="20260812T000000000000Z-bbbb",
            endpoint="messages",
            streamed=False,
            workspace="default",
            inbound_payload={
                "system": "You are a helpful assistant.",
                "messages": [
                    {"role": "user", "content": "Hi, I'm Martin Bach, nice to meet you."},
                    {"role": "assistant", "content": "Hello Martin Bach!"},
                ],
            },
        )
    )
    writer.write(
        FooterRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:01+00:00",
            outcome="passed",
            reason=None,
            duration_ms=42.0,
            upstream_duration_ms=30.0,
            injected={"Bernhard Vogt": "Martin Bach"},
        )
    )
    writer.close()

    summaries = list_captures(tmp_path)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.endpoint == "messages"
    assert summary.hop_count == 3  # system + 2 messages
    assert summary.detected_count == 1
    assert summary.outcome == "passed"
    assert summary.excerpt.startswith("Hi, I'm Martin Bach")


def test_captures_are_listed_in_chronological_filename_order(tmp_path):
    _write_header_only(tmp_path / "20260812T000000000000Z-bbbb.jsonl", capture_id="20260812T000000000000Z-bbbb")
    _write_header_only(tmp_path / "20260812T000000000000Z-aaaa.jsonl", capture_id="20260812T000000000000Z-aaaa")

    summaries = list_captures(tmp_path)

    assert [s.id for s in summaries] == [
        "20260812T000000000000Z-aaaa",
        "20260812T000000000000Z-bbbb",
    ]
