"""Capture directory: filename-as-id, count-bounded eviction (ADR-0047 §5, issue #253).

Eviction runs at capture *start*, oldest first, and never removes the capture(s)
currently being written -- unbounded growth here means plaintext prompts
accumulating on disk indefinitely.
"""

from blindfold_devtools.capture_directory import CaptureDirectory, generate_capture_id


def test_capture_id_doubles_as_sort_key():
    earlier = generate_capture_id(now_iso="2026-08-12T00:00:00.000000+00:00", discriminator="aaaa")
    later = generate_capture_id(now_iso="2026-08-12T00:00:01.000000+00:00", discriminator="bbbb")

    assert earlier < later


def _start_and_close(directory, *, now_iso, discriminator, live_ids=frozenset()):
    capture_id, writer = directory.start_capture(
        now_iso=now_iso, discriminator=discriminator, live_ids=live_ids
    )
    writer.close()
    return capture_id


def test_starting_a_capture_evicts_the_oldest_once_over_the_bound(tmp_path):
    directory = CaptureDirectory(tmp_path, max_captures=2)
    first = _start_and_close(directory, now_iso="2026-08-12T00:00:00+00:00", discriminator="aaaa")
    second = _start_and_close(directory, now_iso="2026-08-12T00:00:01+00:00", discriminator="bbbb")
    third = _start_and_close(directory, now_iso="2026-08-12T00:00:02+00:00", discriminator="cccc")

    remaining = {p.stem for p in tmp_path.glob("*.jsonl")}

    assert remaining == {second, third}
    assert first not in remaining


def test_eviction_never_removes_the_live_capture(tmp_path):
    directory = CaptureDirectory(tmp_path, max_captures=1)
    live_id, live_writer = directory.start_capture(
        now_iso="2026-08-12T00:00:00+00:00", discriminator="aaaa"
    )

    # A second capture starts while the first is still being written -- the
    # directory is already at (and would go over) its bound, but the live
    # capture must survive the eviction that runs at this new capture's start.
    second_id, second_writer = directory.start_capture(
        now_iso="2026-08-12T00:00:01+00:00",
        discriminator="bbbb",
        live_ids=frozenset({live_id}),
    )
    second_writer.close()
    live_writer.close()

    remaining = {p.stem for p in tmp_path.glob("*.jsonl")}

    assert live_id in remaining
    assert second_id in remaining


def test_eviction_runs_at_capture_start_not_lazily(tmp_path):
    directory = CaptureDirectory(tmp_path, max_captures=1)
    first = _start_and_close(directory, now_iso="2026-08-12T00:00:00+00:00", discriminator="aaaa")

    assert (tmp_path / f"{first}.jsonl").exists()

    directory.evict(live_ids=frozenset())

    # A single capture, alone, is never itself "the oldest to evict" -- there
    # is nothing to make room for until a *new* capture actually starts.
    assert (tmp_path / f"{first}.jsonl").exists()
