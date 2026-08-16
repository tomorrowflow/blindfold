"""Mint-state concurrency (issue #312).

The mint path runs inside ``run_in_threadpool`` -- real OS threads, not
cooperative coroutines (``test_l3_non_blocking.py`` already pins this).
Every process-global mint surface (the PII mint counters, the review inbox's
minted counter + surrogate-pool positions, the workspace declared-tool
vocabulary registry) was a plain, unsynchronized Python attribute: two
concurrent exchanges interleaving inside a read-modify-write critical section
can hand the *same* surrogate (or review-inbox item id) to two different
referents. Because an exchange's injected table is keyed by surrogate, the
second referent's real value wins and the first restores wrong (ADR-0048:
also makes a reported miss unreproducible).

A wall-clock race between ordinary concurrent calls is not reliably
reproducible in CPython (``test_l3_non_blocking.py``'s own docstring notes
exactly this about HTTP-level races) -- the GIL still serializes actual
bytecode execution, so one thread's whole (short, I/O-free) critical section
routinely runs to completion before the interpreter ever looks at another
thread, race or no race. Each test below forces the interleaving
deterministically with a two-phase ``threading.Barrier`` double standing in
for the shared container the real bug races on:

- ``read_barrier`` gates the container's read method (``__contains__``/``get``)
  -- every thread must arrive there before *any* of them is allowed to see the
  pre-mutation value, so all N threads observe the identical stale snapshot.
- ``write_barrier`` gates the container's write method (``add``/``__setitem__``)
  -- every thread must arrive there before *any* of them is allowed to commit,
  so all N decisions (already made from the shared stale snapshot) land
  without any of them observing an earlier thread's write.

Together this reproduces the worst-case interleaving on every run instead of
occasionally. Once the seam is locked, only one thread at a time ever reaches
either barrier; a bounded ``timeout`` degrades each wait to a no-op instead of
deadlocking on the missing parties.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from blindfold.engine import DeclaredToolVocabulary
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping

_N = 8
_BARRIER_TIMEOUT = 0.3


def _run_concurrently(n: int, worker: Callable[[int], None]) -> list[BaseException]:
    errors: list[BaseException] = []

    def guarded(i: int) -> None:
        try:
            worker(i)
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`, not raised in-thread
            errors.append(exc)

    threads = [threading.Thread(target=guarded, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def _barrier_wait(barrier: threading.Barrier) -> None:
    try:
        barrier.wait(timeout=_BARRIER_TIMEOUT)
    except threading.BrokenBarrierError:
        pass


class _RacingSet(set):
    """A ``set`` double that forces every concurrent reader through
    ``read_barrier`` before any of them sees the pre-mutation contents, and
    every concurrent writer through ``write_barrier`` before any of them
    commits -- reproducing the exact check-then-act race a missing lock
    allows, deterministically.
    """

    def __init__(self, read_barrier: threading.Barrier, write_barrier: threading.Barrier) -> None:
        super().__init__()
        self._read_barrier = read_barrier
        self._write_barrier = write_barrier

    def __contains__(self, item: object) -> bool:
        _barrier_wait(self._read_barrier)
        return super().__contains__(item)

    def add(self, item: object) -> None:
        _barrier_wait(self._write_barrier)
        super().add(item)


class _RacingDict(dict):
    """The ``dict`` counterpart of :class:`_RacingSet`, for a pool-position
    cursor read via ``get`` and written back via ``__setitem__``.
    """

    def __init__(self, read_barrier: threading.Barrier, write_barrier: threading.Barrier) -> None:
        super().__init__()
        self._read_barrier = read_barrier
        self._write_barrier = write_barrier

    def get(self, key, default=None):
        _barrier_wait(self._read_barrier)
        return super().get(key, default)

    def __setitem__(self, key, value) -> None:
        _barrier_wait(self._write_barrier)
        super().__setitem__(key, value)


class _RacingReviewInbox(ReviewInbox):
    """A :class:`ReviewInbox` whose monotonic ``_minted`` item-id counter is
    forced through the same two-phase-barrier race as ``_RacingSet``/
    ``_RacingDict`` above -- ``_minted`` is a plain ``int`` attribute (no
    container method to intercept), so this backs it with a property whose
    getter/setter gate on the same read/write barrier pair.
    """

    _minted_read_barrier: threading.Barrier | None = None
    _minted_write_barrier: threading.Barrier | None = None
    _minted_value: int = 0

    @property
    def _minted(self) -> int:
        if self._minted_read_barrier is not None:
            _barrier_wait(self._minted_read_barrier)
        return self._minted_value

    @_minted.setter
    def _minted(self, value: int) -> None:
        if self._minted_write_barrier is not None:
            _barrier_wait(self._minted_write_barrier)
        self._minted_value = value


def test_concurrent_mint_pii_of_n_distinct_referents_never_yields_a_duplicate_surrogate():
    mapping = SurrogateMapping()
    mapping._known_surrogates = _RacingSet(
        threading.Barrier(_N), threading.Barrier(_N)
    )
    reals = [f"+1-202-555-{i:04d}" for i in range(_N)]
    results: list[str | None] = [None] * _N

    def worker(i: int) -> None:
        results[i] = mapping.mint_pii("phone", reals[i])

    errors = _run_concurrently(_N, worker)

    assert not errors
    assert len(set(results)) == _N, "every distinct referent must get a distinct surrogate"


def test_concurrent_review_inbox_upsert_of_n_distinct_referents_never_yields_a_duplicate_surrogate():
    inbox = ReviewInbox()
    inbox._pool_positions = _RacingDict(threading.Barrier(_N), threading.Barrier(_N))
    reals = [f"Person Number {i:04d}" for i in range(_N)]
    surrogates_out: list[str | None] = [None] * _N

    def worker(i: int) -> None:
        item = inbox.upsert(reals[i], context=reals[i])
        surrogates_out[i] = item.provisional_surrogate

    errors = _run_concurrently(_N, worker)

    assert not errors
    assert len(set(surrogates_out)) == _N, "every distinct referent must get a distinct surrogate"


def test_concurrent_review_inbox_upsert_of_n_distinct_referents_never_yields_a_duplicate_item_id():
    inbox = _RacingReviewInbox()
    inbox._minted_read_barrier = threading.Barrier(_N)
    inbox._minted_write_barrier = threading.Barrier(_N)
    reals = [f"Person Number {i:04d}" for i in range(_N)]
    item_ids: list[str | None] = [None] * _N

    def worker(i: int) -> None:
        item = inbox.upsert(reals[i], context=reals[i])
        item_ids[i] = item.id

    errors = _run_concurrently(_N, worker)

    assert not errors
    assert len(set(item_ids)) == _N, "every distinct referent must get a distinct item id"


def test_concurrent_declared_tool_vocabulary_record_never_loses_an_update():
    vocab = DeclaredToolVocabulary()
    workspace = "acme"
    tool_names = [frozenset({f"tool_{i}"}) for i in range(_N)]

    def worker(i: int) -> None:
        vocab.record(workspace, tool_names[i])

    errors = _run_concurrently(_N, worker)

    assert not errors
    assert vocab.for_workspace(workspace) == frozenset(
        name for names in tool_names for name in names
    )
