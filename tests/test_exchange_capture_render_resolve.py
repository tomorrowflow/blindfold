"""``blindfold explain`` selection (ADR-0047 §6, issue #257): resolve a capture
directory + an id (or ``--last``) to the one capture file the render reads.
"""

from blindfold_devtools.capture import SECTION_OBSERVED, CaptureWriter, HeaderRecord
from blindfold_devtools.capture_render import CaptureNotFoundError, resolve_capture


def _write_header_only(path, *, capture_id: str) -> None:
    writer = CaptureWriter(path)
    writer.write(
        HeaderRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00+00:00",
            capture_id=capture_id,
            endpoint="messages",
            streamed=False,
            workspace="default",
            inbound_payload={},
        )
    )
    writer.close()


def test_last_resolves_the_most_recent_capture_by_filename_sort_key(tmp_path):
    _write_header_only(tmp_path / "20260812T000000000000Z-aaaa.jsonl", capture_id="20260812T000000000000Z-aaaa")
    _write_header_only(tmp_path / "20260812T000000010000Z-bbbb.jsonl", capture_id="20260812T000000010000Z-bbbb")

    resolved = resolve_capture(tmp_path, capture_id=None, last=True)

    assert resolved.stem == "20260812T000000010000Z-bbbb"


def test_an_explicit_id_resolves_to_that_capture_regardless_of_recency(tmp_path):
    _write_header_only(tmp_path / "20260812T000000000000Z-aaaa.jsonl", capture_id="20260812T000000000000Z-aaaa")
    _write_header_only(tmp_path / "20260812T000000010000Z-bbbb.jsonl", capture_id="20260812T000000010000Z-bbbb")

    resolved = resolve_capture(tmp_path, capture_id="20260812T000000000000Z-aaaa", last=False)

    assert resolved.stem == "20260812T000000000000Z-aaaa"


def test_an_unknown_id_raises_capture_not_found(tmp_path):
    _write_header_only(tmp_path / "20260812T000000000000Z-aaaa.jsonl", capture_id="20260812T000000000000Z-aaaa")

    try:
        resolve_capture(tmp_path, capture_id="does-not-exist", last=False)
        assert False, "expected CaptureNotFoundError"
    except CaptureNotFoundError:
        pass


def test_last_on_an_empty_directory_raises_capture_not_found(tmp_path):
    try:
        resolve_capture(tmp_path, capture_id=None, last=True)
        assert False, "expected CaptureNotFoundError"
    except CaptureNotFoundError:
        pass
