"""Exchange capture: JSONL schema, incremental writer, reader (ADR-0047 §3/§5).

One file per exchange, hops nested, covering the whole round trip. Records are
appended as they occur (``header`` -> per-hop ``detection`` -> ``outbound`` ->
``provider_chunk``/``restored_chunk`` -> ``footer``), so nothing accumulates in
memory and a process killed mid-stream still yields everything that had arrived.

Every record carries a ``section``: ``observed`` (witnessed) or ``reconstructed``
(produced later by replaying through ``blindfold explain``, issue #255) -- so a
reader can never mistake a replayed field for an observed one. This slice only
ever writes ``observed`` records; ``reconstructed`` detection records are
appended by the replay path in a later slice, correlated by ``hop_index``.

Shared field vocabulary with the Processing trace (ADR-0035) is spelled
identically on purpose: ``hop_index``, ``hop_kind``, ``endpoint``, ``streamed``,
``outcome``, ``reason``, ``duration_ms``, ``upstream_duration_ms``,
``l3_provider`` (see ``blindfold.engine.HopDetail`` /
``blindfold.processing_trace.ProcessingTraceRecord``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

SECTION_OBSERVED = "observed"
SECTION_RECONSTRUCTED = "reconstructed"

STATUS_COMPLETE = "complete"
STATUS_IN_FLIGHT = "in-flight"
STATUS_TRUNCATED = "truncated"


@dataclass(frozen=True)
class _CaptureRecord:
    """Shared JSON serialization for every record kind: the ``record`` tag plus
    every dataclass field. ``json.dumps`` serializes a tuple the same as a
    list, so a subclass never needs its own override just to hold one."""

    def to_dict(self) -> dict[str, Any]:
        return {"record": self.record, **asdict(self)}


@dataclass(frozen=True)
class HeaderRecord(_CaptureRecord):
    """Opens a capture: the real inbound payload, witnessed (ADR-0047 §3)."""

    record: ClassVar[str] = "header"

    section: str
    ts: str
    capture_id: str
    endpoint: str  # "messages" | "chat_completions"
    streamed: bool
    workspace: str
    inbound_payload: dict


@dataclass(frozen=True)
class DetectionRecord(_CaptureRecord):
    """One hop's detection detail.

    Observed fields (``l1_counts`` .. ``surrogates``) mirror
    ``blindfold.engine.HopDetail`` verbatim. ``pass_name`` and ``offsets`` are
    reconstructed-only (``section=SECTION_RECONSTRUCTED``), populated by replay
    (issue #255) -- ``None`` on every observed record this slice writes.
    """

    record: ClassVar[str] = "detection"

    section: str
    ts: str
    hop_index: int
    hop_kind: str
    l1_counts: dict
    l1_duration_ms: float
    l2_count: int
    l2_duration_ms: float
    l3_confirmed: int
    l3_dismissed: int
    l3_suppressed: int
    l3_provider: str | None
    l3_duration_ms: float | None
    surrogates: tuple[str, ...] = ()
    pass_name: str | None = None
    offsets: tuple[tuple[int, int], ...] | None = None


@dataclass(frozen=True)
class OutboundRecord(_CaptureRecord):
    """The blindfolded outbound payload actually sent upstream, witnessed."""

    record: ClassVar[str] = "outbound"

    section: str
    ts: str
    payload: dict


@dataclass(frozen=True)
class ProviderChunkRecord(_CaptureRecord):
    """One chunk of the provider response exactly as received, witnessed."""

    record: ClassVar[str] = "provider_chunk"

    section: str
    ts: str
    sequence: int
    chunk: str


@dataclass(frozen=True)
class RestoredChunkRecord(_CaptureRecord):
    """One chunk of the restored response as returned to the client, witnessed."""

    record: ClassVar[str] = "restored_chunk"

    section: str
    ts: str
    sequence: int
    chunk: str


@dataclass(frozen=True)
class FooterRecord(_CaptureRecord):
    """Closes a capture -- the completion marker (ADR-0047 §5).

    ``injected`` is ``ExchangeSession.injected``'s complete surrogate -> real
    pair table, witnessed. A file with no footer record is ``in-flight``.
    """

    record: ClassVar[str] = "footer"

    section: str
    ts: str
    outcome: str
    reason: str | None
    duration_ms: float
    upstream_duration_ms: float | None
    injected: dict


@dataclass(frozen=True)
class TruncatedRecord(_CaptureRecord):
    """Marks that the per-capture size cap was hit and appending stopped.

    An *unmarked* truncation is the failure to avoid: a truncated restore looks
    exactly like a restore failure. A reader must be able to tell a capture
    that hit the cap apart from one that finished cleanly (footer) or one that
    is merely in-flight (neither).
    """

    record: ClassVar[str] = "truncated"

    section: str
    ts: str
    reason: str
    bytes_written: int


_RECORD_TYPES = {
    cls.record: cls
    for cls in (
        HeaderRecord,
        DetectionRecord,
        OutboundRecord,
        ProviderChunkRecord,
        RestoredChunkRecord,
        FooterRecord,
        TruncatedRecord,
    )
}


def _record_from_dict(obj: dict) -> Any:
    kind = obj["record"]
    cls = _RECORD_TYPES[kind]
    fields = {k: v for k, v in obj.items() if k != "record"}
    if cls is DetectionRecord:
        fields["surrogates"] = tuple(fields.get("surrogates") or ())
        offsets = fields.get("offsets")
        fields["offsets"] = None if offsets is None else tuple(tuple(pair) for pair in offsets)
    return cls(**fields)


DEFAULT_MAX_BYTES = 10_000_000  # 10 MB per capture (ADR-0047 §5 size cap)


class CaptureWriter:
    """Appends records to a capture file as they occur -- nothing accumulates
    in memory. Stops appending once the size cap is hit, writing a marked
    ``truncated`` record instead of the record that would have exceeded it.
    """

    def __init__(self, path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.path = path
        self._max_bytes = max_bytes
        self._handle = open(path, "a", encoding="utf-8")
        self._bytes_written = 0
        self._truncated = False

    def write(self, record) -> None:
        if self._truncated:
            return
        line = json.dumps(record.to_dict()) + "\n"
        encoded = line.encode("utf-8")
        if self._bytes_written + len(encoded) > self._max_bytes:
            self._write_truncated(record)
            return
        self._handle.write(line)
        self._handle.flush()
        self._bytes_written += len(encoded)

    def _write_truncated(self, offending_record) -> None:
        marker = TruncatedRecord(
            section=SECTION_OBSERVED,
            ts=offending_record.to_dict().get("ts", ""),
            reason=f"size cap ({self._max_bytes} bytes) reached before a {offending_record.record} record",
            bytes_written=self._bytes_written,
        )
        line = json.dumps(marker.to_dict()) + "\n"
        self._handle.write(line)
        self._handle.flush()
        self._bytes_written += len(line.encode("utf-8"))
        self._truncated = True

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "CaptureWriter":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


@dataclass
class CaptureFile:
    records: list
    status: str


def read_capture(path) -> CaptureFile:
    """Reads a capture back, tolerating a trailing incomplete line (the file
    may still be in-flight, or a process may have died mid-write): parsing
    stops at the first line that fails to decode, and everything read up to
    that point is returned.
    """
    records: list = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                break
            records.append(_record_from_dict(obj))

    if any(isinstance(r, TruncatedRecord) for r in records):
        status = STATUS_TRUNCATED
    elif any(isinstance(r, FooterRecord) for r in records):
        status = STATUS_COMPLETE
    else:
        status = STATUS_IN_FLIGHT

    return CaptureFile(records=records, status=status)
