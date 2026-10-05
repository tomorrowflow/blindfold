"""ADR-0060 amendment 2026-10-05, decision points 3 (candidate-level half) and
10 (issue #452): the recognition memory for a client that fans a world-acting
request out to a sub-conversation and relays its results into the MAIN
conversation on a LATER, non-world-acting request as a plain string
``tool_result`` block -- Claude Desktop's own measured shape. Every signal the
structural rule (issue #448, ``engine.is_contained_response_block``) depends
on -- assistant role, the provider's own ``*_tool_result`` block type -- is
stripped by that relay, so recognition there has to work at the level of a
candidate's own text instead.

A candidate is exempt from novelty minting only if it lies inside a
``tool_result``/``mcp_tool_result`` block AND its string occurs in a
remembered contained response (``engine``'s own mint call site is the other
half of this rule; this module is only the memory it consults).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
from collections import OrderedDict
from collections.abc import Iterator

# Point 10: "up to six words" -- the same bound the #413 captures measured
# (eight-word shingles survived only 61-69% verbatim; candidate-length spans
# are what the amendment actually relies on).
_MAX_NGRAM_WORDS = 6

_WORD_RE = re.compile(r"\w+")


def _word_ngrams(text: str, max_n: int) -> Iterator[str]:
    """Every contiguous run of 1..``max_n`` words in ``text``, joined by a
    single space -- the same word-boundary tokenization
    ``store._mint._real_value_pattern`` treats as "a word" (runs of ``\\w``),
    so a candidate's own n-gram (:func:`ContainedResponseMemory.remembers`)
    and a remembered response's n-grams (:func:`ContainedResponseMemory.remember`)
    are tokenized identically and can never silently disagree about what
    counts as a word.

    Longest spans first, shortest (1-word) last: :meth:`ContainedResponseMemory.remember`
    evicts least-recently-inserted first, and a single long response's own
    n-grams can together exceed the per-workspace bound before `remember`
    ever returns -- a candidate's own span is almost always 1-2 words, so
    yielding those last keeps them most-recently-used and the longer,
    less-useful shingles are what gets evicted first within that one call.
    """
    words = _WORD_RE.findall(text)
    for n in range(max_n, 0, -1):
        for start in range(len(words) - n + 1):
            yield " ".join(words[start : start + n])


_DEFAULT_MAX_ENTRIES_PER_WORKSPACE = 4096


class ContainedResponseMemory:
    """Keyed hashes of the word n-grams (up to six words) of every contained
    response's text seen so far, never the plaintext -- bounded in size per
    workspace (LRU eviction), in-process only, lost on restart.

    Refresh (point 10): a hit in :meth:`remembers` moves that entry to
    most-recently-used, identically to re-storing it via :meth:`remember` --
    a relayed result is re-sent on every later turn of the conversation, so
    an entry must stay recognised for as long as the conversation keeps
    re-sending it. A fixed TTL was considered and rejected (point 10: "would
    only delay the flood").
    """

    def __init__(
        self, max_entries_per_workspace: int = _DEFAULT_MAX_ENTRIES_PER_WORKSPACE
    ) -> None:
        # Process-random HMAC key (point 10: "keyed hashes") -- never
        # persisted, never derived from any value this memory itself stores.
        self._key = os.urandom(32)
        self._max_entries_per_workspace = max_entries_per_workspace
        self._by_workspace: dict[str, "OrderedDict[bytes, None]"] = {}
        # Issue #452: reached from the same mint-pass threadpool worker
        # engine.DeclaredToolVocabulary's own lock already guards against
        # (see its docstring) -- the identical discipline here.
        self._lock = threading.Lock()

    def _digest(self, ngram: str) -> bytes:
        return hmac.new(self._key, ngram.encode("utf-8"), hashlib.sha256).digest()

    def remember(self, workspace: str, text: str) -> None:
        """Record every word n-gram (up to six words) of ``text`` -- one
        contained response's own leaf text -- as a keyed hash. Refreshes
        (moves to most-recently-used) any n-gram already present, then
        evicts the least-recently-used entries past this workspace's bound.
        """
        with self._lock:
            bucket = self._by_workspace.setdefault(workspace, OrderedDict())
            for ngram in _word_ngrams(text, _MAX_NGRAM_WORDS):
                digest = self._digest(ngram)
                if digest in bucket:
                    bucket.move_to_end(digest)
                else:
                    bucket[digest] = None
                while len(bucket) > self._max_entries_per_workspace:
                    bucket.popitem(last=False)

    def remembers(self, workspace: str, candidate: str) -> bool:
        """True if ``candidate`` (an L3 candidate's own literal text) is
        itself a remembered n-gram for ``workspace``. A hit refreshes it
        (same recency bump :meth:`remember` gives a re-stored n-gram).

        A ``candidate`` longer than six words can never match -- point 10's
        own bound -- which only ever widens novelty minting, never narrows
        protection (the over-protective default this whole amendment
        accepts elsewhere, e.g. point 7's restart behaviour).
        """
        key = " ".join(_WORD_RE.findall(candidate))
        if not key:
            return False
        with self._lock:
            bucket = self._by_workspace.get(workspace)
            if bucket is None:
                return False
            digest = self._digest(key)
            if digest not in bucket:
                return False
            bucket.move_to_end(digest)
            return True
