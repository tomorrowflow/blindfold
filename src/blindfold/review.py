"""Review inbox + allowlist: the human side of the learning loop (ADR-0010).

When the L3 adjudicator (ADR-0003) confirms a novel candidate as an entity, the
engine mints a **provisional surrogate** immediately (protection never waits on
the user — agents don't stall) and records the (real, provisional_surrogate,
context) tuple here. The user later **confirms** (the entry is removed; the
canonical entity-graph mapping grows) or **rejects** (the entry is removed; the
token joins the **allowlist** and is never blindfolded again).

Bidirectional: confirmations make detection more deterministic over time (L2
matches it without an L3 call); rejections suppress L3 calls that would re-flag
a non-sensitive token (e.g. a code identifier).

This slice keeps both stores in-memory and process-local. Persistence lands with
the management-store slice (ADR-0008/0011).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .store._mint import collides_with_known_entity

# Plausible fake names used to mint **provisional** surrogates. Kept disjoint from
# the cold-start ``store._mint._PERSON_POOL`` so a rejected provisional never collides
# with a confirmed entity's surrogate. Falls back to ``"Provisional Surrogate {N}"``
# past the pool so the inbox is never blocked by pool exhaustion.
_PROVISIONAL_POOL: tuple[str, ...] = (
    "Alex Brenner",
    "Berta Falke",
    "Carla Distel",
    "Doris Engler",
    "Emil Fink",
    "Fritz Graf",
    "Greta Henning",
    "Hugo Imhoff",
)

# Plausible fake company names for a candidate GLiNER (or another type-aware
# adjudicator) classifies as "organization" (issue #167) -- kept disjoint from
# _PROVISIONAL_POOL above and from store._mint's _PERSON_POOL/_ORG_POOL/
# _REPLACEMENT_POOL/_TERM_POOL for the same collision-avoidance reason those
# pools are already kept disjoint from each other. Falls back to the same
# "Provisional Surrogate {N}" scheme past the pool.
_PROVISIONAL_ORG_POOL: tuple[str, ...] = (
    "Nordkap Systeme GmbH",
    "Rheinblick Consulting",
    "Waldstein Industries",
    "Kupfertal Solutions",
    "Birkenhain Logistik",
    "Moosburg Analytics",
    "Feldmark Ventures",
    "Silberklang Media",
)

_DEFAULT_PROVISIONAL_POOL_KEY = "person"
_PROVISIONAL_POOLS: dict[str, tuple[str, ...]] = {
    _DEFAULT_PROVISIONAL_POOL_KEY: _PROVISIONAL_POOL,
    "organization": _PROVISIONAL_ORG_POOL,
}


@dataclass(frozen=True)
class ReviewItem:
    """A provisionally-blindfolded novel candidate awaiting human review.

    ``id`` is the routable handle the management API uses for confirm/reject.
    ``real`` is the novel token L3 confirmed as an entity; ``provisional_surrogate``
    is the fake that egressed upstream; ``context`` is the small window around
    the candidate (the same window L3 saw — ADR-0003) so the reviewer can decide
    without re-opening the original transcript. ``context_offset`` is the start
    index of ``real`` inside ``context`` (ADR-0035 decision 11, issue #155) —
    derived from the candidate span's own position, so the frontend can highlight
    the correct occurrence in place without a fragile ``indexOf`` search.
    """

    id: str
    real: str
    provisional_surrogate: str
    context: str
    context_offset: int


class ReviewInbox:
    """In-memory queue of provisional candidates, indexed by stable id.

    The id is derived from the ``real`` value so the same novel candidate hit
    twice across requests does NOT create a duplicate inbox item (the provisional
    surrogate is also reused via the mapping — clause E-stable).
    """

    def __init__(self) -> None:
        self._items: dict[str, ReviewItem] = {}
        # real -> id lookup, so re-encountering the same novel value reuses the
        # existing entry instead of minting a duplicate. Persists across remove()
        # too: a removed entry has been triaged (confirmed or rejected) and the
        # learning loop's two stores (entity graph / allowlist) own re-detection
        # from then on.
        self._by_real: dict[str, str] = {}
        # Monotonic counter for stable item ids; doesn't reset on remove() so a
        # removed-then-re-added item still gets a fresh id.
        self._minted: int = 0
        # Raw provisional-pool cursor (issue #80), one per pool key: separate from
        # ``_minted`` because a collision-skipped pool entry consumes a pool
        # position without ever becoming an item, and skipped entries are never
        # reused for a later item. Kept per-pool (issue #167) so minting an
        # organization surrogate never advances (or is advanced by) the unrelated
        # person-pool cursor.
        self._pool_positions: dict[str, int] = {}

    def upsert(
        self,
        real: str,
        context: str,
        known_values: Iterable[str] = (),
        context_offset: int | None = None,
        entity_type: str | None = None,
    ) -> ReviewItem:
        """Add (or reuse) a provisional inbox entry for ``real`` and return it.

        The provisional surrogate is minted here (not by the engine) so the inbox
        is the single owner of the provisional registry — confirm/reject can
        cleanly promote/drop entries without leaving stale entries in the main
        ``SurrogateMapping``. Mint-time disjointness (issue #80): ``known_values``
        is the closed-world set of known entities' canonical names + Variations
        (the same set the pre-egress leak gate checks); a pool entry that contains
        one as a substring is skipped, never assigned to any item.

        ``context_offset`` (ADR-0035 decision 11, issue #155) should be the
        candidate span's own position within ``context`` — the real detection
        call sites (``engine.py``, ``mining.py``) always pass it, derived from
        ``CandidateSpan.context_offset``. When omitted, it falls back to the
        first occurrence of ``real`` in ``context`` — only correct for callers
        (tests, simple fixtures) that don't have a positional span to hand.

        ``entity_type`` (issue #167, ADR-0005) selects the surrogate pool: an
        ``"organization"`` candidate mints an org-shaped company name, not a
        person name. Any other value (including ``None`` -- the inner LLM
        adjudicators don't detect a type) falls back to today's default person
        pool, unchanged.
        """
        existing_id = self._by_real.get(real)
        if existing_id is not None:
            return self._items[existing_id]
        item_id = str(self._minted + 1)
        self._minted += 1
        pool_key = (
            entity_type if entity_type in _PROVISIONAL_POOLS
            else _DEFAULT_PROVISIONAL_POOL_KEY
        )
        start_position = self._pool_positions.get(pool_key, 0)
        surrogate, next_position = _next_provisional(
            pool_key, start_position, known_values
        )
        self._pool_positions[pool_key] = next_position
        if context_offset is None:
            context_offset = max(0, context.find(real))
        item = ReviewItem(
            id=item_id,
            real=real,
            provisional_surrogate=surrogate,
            context=context,
            context_offset=context_offset,
        )
        self._items[item_id] = item
        self._by_real[real] = item_id
        return item

    def list(self) -> list[ReviewItem]:
        return list(self._items.values())

    def get(self, item_id: str) -> ReviewItem | None:
        return self._items.get(item_id)

    def remove(self, item_id: str) -> ReviewItem | None:
        item = self._items.pop(item_id, None)
        if item is not None:
            self._by_real.pop(item.real, None)
        return item


class Allowlist:
    """Tokens learned to be NOT sensitive (e.g. a code identifier mis-flagged).

    Once a token is on the allowlist, L3 must not re-flag it on subsequent
    requests — over-redaction is a quality bug the learning loop fixes.
    """

    def __init__(self) -> None:
        self._tokens: set[str] = set()

    def add(self, token: str) -> None:
        self._tokens.add(token)

    def contains(self, token: str) -> bool:
        return token in self._tokens

    def tokens(self) -> frozenset[str]:
        return frozenset(self._tokens)


def _provisional_pool_entry(pool_key: str, position: int) -> str:
    pool = _PROVISIONAL_POOLS[pool_key]
    if position < len(pool):
        return pool[position]
    return f"Provisional Surrogate {position}"


def _next_provisional(
    pool_key: str, start_position: int, known_values: Iterable[str]
) -> tuple[str, int]:
    """The first mint-time-disjoint entry at or after ``start_position`` in the
    ``pool_key`` pool, and the cursor position to resume from on the next call
    for that same pool (issue #80, per-pool since issue #167)."""
    known = list(known_values)
    position = start_position
    while True:
        candidate = _provisional_pool_entry(pool_key, position)
        position += 1
        if not collides_with_known_entity(candidate, known):
            return candidate, position
