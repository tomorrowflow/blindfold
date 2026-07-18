"""Processing trace: local, ephemeral, scrubbed per-exchange ring buffer (ADR-0035).

Leak-audit clause analysis:
- This slice's request-path capture is exercised in test_processing_trace_capture.py.
- This file covers the buffer primitive in isolation: in-memory, count-bounded
  (~200, oldest evicted), never persisted -- ADR-0035 decisions 1-3, 6.
"""

from __future__ import annotations

from blindfold.processing_trace import ProcessingTraceBuffer


def test_buffer_evicts_oldest_once_over_the_bound():
    buffer = ProcessingTraceBuffer(maxlen=3)
    for i in range(5):
        buffer.record(
            workspace="ws-a",
            endpoint="messages",
            streamed=False,
            outcome="passed",
            detected=i,
            duration_ms=1.0,
        )
    recent = buffer.recent()
    assert len(recent) == 3
    # oldest two (detected=0, detected=1) were evicted; only the last 3 remain.
    assert [r.detected for r in recent] == [2, 3, 4]


def test_record_carries_scrubbed_hop_detail_and_l3_rollup():
    # Issue #153 (ADR-0035 per-hop expansion): the ring-buffer record extends with
    # per-hop detail (already-scrubbed dicts -- the request path is responsible for
    # serializing session.hops before recording) plus an exchange-level L3
    # provider/timing rollup for the collapsed row's new L3 column.
    buffer = ProcessingTraceBuffer(maxlen=3)
    hop = {
        "hop_index": 0,
        "hop_kind": "system",
        "l1_counts": {"email": 1},
        "l1_duration_ms": 0.5,
        "l2_count": 2,
        "l2_duration_ms": 0.3,
        "l3_confirmed": 1,
        "l3_dismissed": 0,
        "l3_suppressed": 1,
        "l3_provider": "ollama",
        "l3_duration_ms": 12.0,
        "surrogates": ["Berta Vogel"],
    }

    buffer.record(
        workspace="ws-a",
        endpoint="messages",
        streamed=False,
        outcome="passed",
        detected=2,
        duration_ms=15.0,
        hops=[hop],
        l3_provider="ollama",
        l3_duration_ms=12.0,
    )

    record = buffer.recent()[0]
    assert record.hops == (hop,)
    assert record.l3_provider == "ollama"
    assert record.l3_duration_ms == 12.0
    serialized = record.to_dict()
    assert serialized["hops"] == [hop]
    assert serialized["l3_provider"] == "ollama"
    assert serialized["l3_duration_ms"] == 12.0


def test_record_defaults_to_no_hops_and_no_l3_rollup():
    buffer = ProcessingTraceBuffer(maxlen=3)

    buffer.record(
        workspace="ws-a",
        endpoint="messages",
        streamed=False,
        outcome="blocked",
        detected=0,
        duration_ms=1.0,
    )

    record = buffer.recent()[0]
    assert record.hops == ()
    assert record.l3_provider is None
    assert record.l3_duration_ms is None
    assert record.to_dict()["hops"] == []
