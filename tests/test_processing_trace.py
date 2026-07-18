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
