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

The allowlist stays in-memory and process-local (the learned side persists via
``store/allowlist_store.py``, issue #168). The review inbox is optionally
persisted through the same store-or-fallback seam (``store/review_inbox_store.py``,
ADR-0037, issue #169) — a durable real-value surface, Transit-encrypted, unlike
the dismissal log / processing trace.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .policy import DEFAULT_WORKSPACE
from .store._mint import (
    collides_with_known_entity,
    pool_entry_collides_with_corpus,
    surrogate_space_match,
)

if TYPE_CHECKING:
    from .mapping_cipher import MappingCipher
    from .surrogates import SurrogateMapping
    from .transit import TransitClient

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

# Trailing legal-form suffixes (issue #289): an organisation mentioned with and
# without its legal form ("Kestrel Dynamics GmbH" vs "Kestrel Dynamics") is the
# same referent, not two. Longest-first so "GmbH & Co. KG" matches before the
# bare "KG" it contains.
_LEGAL_FORM_SUFFIXES: tuple[str, ...] = (
    "GmbH & Co. KG",
    "GmbH",
    "AG",
    "KG",
    "SE",
    "Ltd.",
    "Ltd",
    "LLC",
    "LLP",
    "PLC",
    "Inc.",
    "Inc",
    "Corp.",
    "Corp",
    "Co.",
    "S.A.",
    "SA",
    "BV",
    "NV",
)


def _referent_key(real: str, entity_type: str | None) -> str:
    """The key :meth:`ReviewInbox.upsert` dedups a novel candidate's referent on
    (issue #289).

    Plain ``real`` for anything not typed ``"organization"``. For an
    organisation, a trailing legal-form suffix (``GmbH``, ``AG``, ``Ltd``, ...)
    is stripped first, so the same company mentioned with and without its legal
    form resolves to one referent -- and therefore one surrogate -- instead of
    minting a second provisional entity.
    """
    if entity_type != "organization":
        return real
    stripped = real
    for suffix in _LEGAL_FORM_SUFFIXES:
        if stripped == suffix:
            continue
        if stripped.endswith(" " + suffix):
            stripped = stripped[: -(len(suffix) + 1)].rstrip()
            break
    return stripped


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
    ``workspace`` (issue #171) is the workspace slug the candidate was detected
    under, captured at detection time — confirm reads it to know which
    workspace's EntityGraph to grow, since it is not itself a real value it is
    never Transit-encrypted, unlike ``real``/``context``.
    """

    id: str
    real: str
    provisional_surrogate: str
    context: str
    context_offset: int
    entity_type: str | None = None
    workspace: str = DEFAULT_WORKSPACE


class ReviewInboxStore(Protocol):
    """Persistence seam for :class:`ReviewInbox` (ADR-0037, issue #169).

    Only Transit ciphertext (+ a blind index for ``real``) is ever written for the
    two real-value columns -- the store performs no encryption of its own;
    ``ReviewInbox`` encrypts before / decrypts after calling it. Backed in
    production by
    :class:`~blindfold.store.review_inbox_store.PostgresReviewInboxStore`; a
    recording double stands in for it in the fast unit tests.
    """

    def upsert_row(
        self,
        item_id: str,
        real_ciphertext: str,
        real_blind_index: str,
        context_ciphertext: str,
        context_offset: int,
        provisional_surrogate: str,
        entity_type: str | None,
        workspace: str,
    ) -> None: ...

    def remove_row(self, item_id: str) -> None: ...

    def list_rows(self) -> list[tuple[str, str, str, int, str, str | None, str]]: ...

    def pool_positions(self) -> dict[str, int]: ...

    def set_pool_position(self, pool_key: str, position: int) -> None: ...


class ReviewInbox:
    """In-memory queue of provisional candidates, indexed by stable id.

    The id is derived from the ``real`` value so the same novel candidate hit
    twice across requests does NOT create a duplicate inbox item (the provisional
    surrogate is also reused via the mapping — clause E-stable).

    Optionally persisted (``store`` + a mapping cipher, ADR-0037 / issue #169,
    generalized from Transit-only by ADR-0045 §2/§4 / issue #231) as a durable
    real-value surface: ``real``/``context`` reach the store only as
    mapping-cipher ciphertext (plus a blind index for ``real``, for dedup
    without decrypting), never plaintext. Persistence requires BOTH a store and
    an active mapping cipher -- Transit or the Local key cipher (graceful
    degradation, issue #149) — with either missing, this stays the plain
    in-memory/ephemeral inbox, byte-identical to before this slice.

    ``mapping_cipher`` is the preferred constructor parameter; ``transit`` is
    kept as a backward-compat alias (mirroring ``store/sqlite.py``'s
    ``mapping_cipher or transit`` convention) so existing callers passing a
    ``TransitClient`` via ``transit=`` keep working unchanged.
    """

    def __init__(
        self,
        store: "ReviewInboxStore | None" = None,
        transit: "TransitClient | None" = None,
        mapping_cipher: "MappingCipher | None" = None,
    ) -> None:
        self._items: dict[str, ReviewItem] = {}
        # referent key (see _referent_key) -> id lookup, so re-encountering the
        # same novel referent reuses the existing entry instead of minting a
        # duplicate -- including an organisation mentioned with and without its
        # trailing legal form (issue #289). Persists across remove() too: a
        # removed entry has been triaged (confirmed or rejected) and the
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
        self._store = store
        self._cipher = mapping_cipher or transit

    def _persistent(self) -> bool:
        return self._store is not None and self._cipher is not None

    def attach_store(
        self, store: "ReviewInboxStore", mapping_cipher: "MappingCipher | None"
    ) -> None:
        """Wire persistence into an already-constructed inbox and hydrate every
        previously-persisted item + pool cursor (ADR-0037, issue #169).

        Call once, e.g. at process startup, when both dependencies are (or become)
        available. A no-op for hydration when ``mapping_cipher`` isn't configured
        too (issue #149 graceful degradation) — a store alone can't decrypt.
        ``mapping_cipher`` accepts either a ``TransitClient`` or a
        ``LocalKeyCipher`` (ADR-0045 §2/§4, issue #231) -- both satisfy the same
        ``encrypt``/``decrypt``/``blind_index`` seam.
        """
        self._store = store
        self._cipher = mapping_cipher
        if not self._persistent():
            return
        for row in store.list_rows():
            (
                item_id,
                real_ciphertext,
                context_ciphertext,
                context_offset,
                surrogate,
                entity_type,
                workspace,
            ) = row
            real = self._cipher.decrypt(real_ciphertext)
            context = self._cipher.decrypt(context_ciphertext)
            item = ReviewItem(
                id=item_id,
                real=real,
                provisional_surrogate=surrogate,
                context=context,
                context_offset=context_offset,
                entity_type=entity_type,
                workspace=workspace,
            )
            self._items[item_id] = item
            self._by_real[_referent_key(real, entity_type)] = item_id
            self._minted = max(self._minted, int(item_id))
        self._pool_positions.update(store.pool_positions())

    def upsert(
        self,
        real: str,
        context: str,
        known_values: Iterable[str] = (),
        context_offset: int | None = None,
        entity_type: str | None = None,
        workspace: str = DEFAULT_WORKSPACE,
        corpus_text: str | None = None,
    ) -> ReviewItem:
        """Add (or reuse) a provisional inbox entry for ``real`` and return it.

        Reuse is keyed on :func:`_referent_key`, not ``real`` verbatim (issue
        #289): for an ``"organization"`` candidate, a trailing legal form
        (``GmbH``, ``AG``, ``Ltd``, ...) is stripped before the lookup, so the
        same company mentioned with and without its legal form resolves to the
        one existing item -- and its one surrogate -- instead of minting a
        second provisional entity. The item's own ``real`` field keeps whichever
        surface form was encountered first; only the dedup key is normalized.

        The provisional surrogate is minted here (not by the engine) so the inbox
        is the single owner of the provisional registry — confirm/reject can
        cleanly promote/drop entries without leaving stale entries in the main
        ``SurrogateMapping``. Mint-time disjointness (issue #80): ``known_values``
        is the closed-world set of known entities' canonical names + Variations
        (the same set the pre-egress leak gate checks); a pool entry that contains
        one as a substring is skipped, never assigned to any item.

        ``corpus_text`` (issue #292) extends that same disjointness from known
        reals to the live corpus being processed this exchange: a pool entry
        already occurring (whole value or distinctive component) in
        ``corpus_text`` is skipped too, the same way a pool entry colliding
        with a known real is. Defaults to ``context`` when omitted, so every
        existing caller that only ever had the local context snippet keeps
        checking against it; ``engine.py``'s real call site passes the full
        hop text explicitly, since the collision that matters can be anywhere
        in the hop, not just near this candidate's own occurrence.

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

        ``workspace`` (issue #171) is the workspace this candidate was detected
        under -- captured on the item so confirm knows which workspace's
        EntityGraph to grow. Falls back to the default workspace slug for a
        caller with no workspace in context.
        """
        referent_key = _referent_key(real, entity_type)
        existing_id = self._by_real.get(referent_key)
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
            pool_key,
            start_position,
            known_values,
            corpus_text if corpus_text is not None else context,
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
            entity_type=entity_type,
            workspace=workspace,
        )
        self._items[item_id] = item
        self._by_real[referent_key] = item_id
        if self._persistent():
            self._persist_item(item, pool_key, next_position)
        return item

    def _persist_item(self, item: ReviewItem, pool_key: str, next_position: int) -> None:
        """Write ``item`` through the store seam as mapping-cipher ciphertext
        (ADR-0037, generalized from Transit-only by ADR-0045 §2/§4 / issue #231).

        Only ``real`` (+ its blind index) and ``context`` are encrypted;
        ``provisional_surrogate``/``entity_type``/``workspace`` are never real
        values, so they are written plaintext, matching the store's own column
        shapes (workspace: issue #171).
        """
        assert self._store is not None and self._cipher is not None
        real_ciphertext = self._cipher.encrypt(item.real)
        real_blind_index = self._cipher.blind_index(item.real)
        context_ciphertext = self._cipher.encrypt(item.context)
        self._store.upsert_row(
            item.id,
            real_ciphertext,
            real_blind_index,
            context_ciphertext,
            item.context_offset,
            item.provisional_surrogate,
            item.entity_type,
            item.workspace,
        )
        self._store.set_pool_position(pool_key, next_position)

    def list(self) -> list[ReviewItem]:
        return list(self._items.values())

    def get(self, item_id: str) -> ReviewItem | None:
        return self._items.get(item_id)

    def remove(self, item_id: str) -> ReviewItem | None:
        item = self._items.pop(item_id, None)
        if item is not None:
            self._by_real.pop(_referent_key(item.real, item.entity_type), None)
            if self._persistent():
                self._store.remove_row(item_id)
        return item

    def purge_surrogate_collisions(self, mapping: "SurrogateMapping") -> list[ReviewItem]:
        """Repair path (issue #292) for a store already poisoned before the
        mint-time guard existed: drop every item whose ``real`` is equal to, or
        a whole word-boundary component of, a surrogate live in that item's own
        recorded ``context`` -- Blindfold's own prior output, wrongly minted as
        a provisional real entity, not a genuine referent. See
        ``store._mint.surrogate_space_match`` for why this is word-boundary and
        stopword-filtered rather than a raw substring test.

        A ``mapping_cipher: none`` inbox is in-memory and a restart clears it,
        but a persisted inbox (ADR-0037) carries the deadlock across restarts
        with no way out except hand-rejecting every colliding item one at a
        time. This sweeps the whole inbox in one call, going through
        :meth:`remove` for each hit so a persisted row is deleted too.

        Scoped to surrogates occurring in the item's own ``context`` -- the
        same occurs-in-text scope the mint-time guard uses
        (``engine._live_surrogate_values``) -- rather than the full
        process-global surrogate vocabulary, so a genuinely novel item that
        merely shares a word with an unrelated surrogate never mentioned in
        its own context survives the sweep (issue #68's "Vogt" precedent).
        Checked against every *other* item's provisional surrogate too (not
        just ``mapping``'s), mirroring the mint-time guard's own known-values
        composition.
        """
        items = list(self._items.values())
        colliding: list[ReviewItem] = []
        for item in items:
            other_surrogates = [
                other.provisional_surrogate
                for other in items
                if other.id != item.id
            ]
            live = {
                value
                for value in list(mapping.known_surrogates()) + other_surrogates
                if value and value in item.context
            }
            if surrogate_space_match(item.real, live) is not None:
                colliding.append(item)
        for item in colliding:
            self.remove(item.id)
        return colliding


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
    pool_key: str,
    start_position: int,
    known_values: Iterable[str],
    corpus_text: str = "",
) -> tuple[str, int]:
    """The first mint-time-disjoint entry at or after ``start_position`` in the
    ``pool_key`` pool, and the cursor position to resume from on the next call
    for that same pool (issue #80, per-pool since issue #167).

    ``corpus_text`` (issue #292) adds pool-vs-corpus disjointness alongside
    the existing pool-vs-known-real check: a *named* pool entry already
    occurring (whole value or distinctive component) in the text being
    processed this exchange is skipped too, the same way a known-real
    collision is. Deliberately scoped to named entries only (``position <
    len(pool)``), never the numbered ``"Provisional Surrogate {N}"`` fallback:
    that fallback's own words ("Provisional", "Surrogate", the pool's kind
    name) are generic project vocabulary that legitimately appears constantly
    in ordinary corpus text about Blindfold itself -- checking it against the
    corpus would spuriously collide on every fallback candidate forever,
    turning a bounded pool walk into an unbounded one. The embedded position
    number already makes every fallback entry unique without needing a corpus
    check.
    """
    known = list(known_values)
    pool_size = len(_PROVISIONAL_POOLS[pool_key])
    position = start_position
    while True:
        candidate = _provisional_pool_entry(pool_key, position)
        is_named_entry = position < pool_size
        position += 1
        if collides_with_known_entity(candidate, known):
            continue
        if is_named_entry and pool_entry_collides_with_corpus(candidate, corpus_text):
            continue
        return candidate, position
