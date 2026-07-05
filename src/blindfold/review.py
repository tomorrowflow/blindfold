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

from dataclasses import dataclass

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


@dataclass(frozen=True)
class ReviewItem:
    """A provisionally-blindfolded novel candidate awaiting human review.

    ``id`` is the routable handle the management API uses for confirm/reject.
    ``real`` is the novel token L3 confirmed as an entity; ``provisional_surrogate``
    is the fake that egressed upstream; ``context`` is the small window around
    the candidate (the same window L3 saw — ADR-0003) so the reviewer can decide
    without re-opening the original transcript.
    """

    id: str
    real: str
    provisional_surrogate: str
    context: str


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
        # Monotonic counter for stable id + surrogate pool index; doesn't reset on
        # remove() so a removed-then-re-added item still gets a fresh id.
        self._minted: int = 0

    def upsert(self, real: str, context: str) -> ReviewItem:
        """Add (or reuse) a provisional inbox entry for ``real`` and return it.

        The provisional surrogate is minted here (not by the engine) so the inbox
        is the single owner of the provisional registry — confirm/reject can
        cleanly promote/drop entries without leaving stale entries in the main
        ``SurrogateMapping``.
        """
        existing_id = self._by_real.get(real)
        if existing_id is not None:
            return self._items[existing_id]
        index = self._minted
        self._minted += 1
        item_id = str(index + 1)
        item = ReviewItem(
            id=item_id,
            real=real,
            provisional_surrogate=_mint_provisional(index),
            context=context,
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


def _mint_provisional(index: int) -> str:
    if index < len(_PROVISIONAL_POOL):
        return _PROVISIONAL_POOL[index]
    return f"Provisional Surrogate {index}"
