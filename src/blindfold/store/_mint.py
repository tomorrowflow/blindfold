"""Deterministic surrogate minting for the cold-start seed.

A surrogate is the fake stand-in assigned to a real referent. For this slice minting is
deterministic by (kind, position-in-seed) so that:

- E-stable: the same referent always mints the SAME surrogate (across repository rebuilds
  and across ETL re-runs), with no randomness or wall-clock dependency.
- the in-process vendored repository and the Postgres ETL compute identical surrogates,
  so the fast (hermetic) round-trip and the DB-backed graph agree.

Coherent surrogate world / reserved-namespace PII (ADR-0005, leak-audit clause E) is out
of scope this slice; these are merely plausible, collision-free stand-ins.

Mint-time disjointness (issue #80): a candidate pool/fallback entry is rejected if a
known entity's canonical name or a Variation occurs in it at a word boundary
(:func:`_real_value_pattern`, issue #293/#332) -- the same closed-world set, matched
by the same rule, the pre-egress leak gate (``engine.leak_gate``) checks via
``mapping.real_values()``, so mint and gate can never disagree. A rejected entry is
skipped, never reused for a later referent (:func:`mint_surrogates`).

Pool-vs-corpus disjointness (issue #292): :func:`pool_entry_collides_with_corpus`
extends the issue #80 discipline above from the closed-world set of known REAL
values to the live CORPUS being processed this exchange -- a pool entry (or a
distinctive component of it) that already occurs as plain text in the hop must
never be assigned, the same way a pool entry colliding with a known real is
skipped. This is what actually closes the reported deadlock: this repository's
own docs used a literal ``review._PROVISIONAL_POOL`` entry as a worked example,
so reading them as a tool result handed L3 that entry's own component as prose,
which L3 confirmed as a novel real -- colliding with the identical string
already live as an unrelated referent's surrogate. Preventing the surrogate
assignment from ever colliding with the corpus in the first place removes the
ambiguity instead of adjudicating it after the fact.

:func:`surrogate_space_match` (kept for the repair path,
``review.ReviewInbox.purge_surrogate_collisions``) is the mirror-direction
check: whether an already-poisoned, already-persisted item's ``real`` collides
with surrogate-space. It is no longer used at mint time (that dismissal path
-- fail-open on a genuine real/surrogate-component coincidence -- was removed;
pool-vs-corpus disjointness prevents the collision from arising instead of
adjudicating it after the mint).

Reserved-namespace fallback (ADR-0052, issue #335): past a named pool,
:func:`next_replacement_surrogate` and :func:`mint_surrogates` both fall back
to a single opaque ASCII token from a reserved namespace closed against ever
being minted as a real (:func:`is_reserved_provisional_surrogate_form`), and
both walks are bounded, raising :class:`FallbackSurrogatePoolExhaustedError`
rather than looping forever when a known value collides with every fallback
candidate in range -- the same invariant #330/#331 already gave ``review.py``'s
own provisional-pool fallback, applied to this module's two sibling paths.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable

from ..l3 import _SENTENCE_STOPWORDS as _COMPONENT_STOPWORDS

# Issue #338 (ADR-0052's acknowledged separate work): enlarged from 8 to 32 entries so
# the opaque BFX fallback fires far less often (run 7 minted 43 provisional items against
# a pool of 8). Existing entries (position 0-7) are unchanged and keep their positions --
# the cursor is durable, so editing an already-issued position would re-issue a different
# surrogate there. New entries (position 8-31) only append. Kept pairwise disjoint, at
# the distinctive-word level, from every other pool here and in review.py (see
# tests/test_surrogate_pool_enlargement.py; that test documents one pre-existing,
# out-of-scope exception between this pool and _REPLACEMENT_POOL below -- "Lorenz").
_PERSON_POOL: tuple[str, ...] = (
    "Bernhard Vogt",
    "Claudia Reinhardt",
    "Dieter Sommer",
    "Elena Fuchs",
    "Stefan Kaiser",
    "Gabriele Wirth",
    "Heinz Lorenz",
    "Iris Hartmann",
    "Zacharias Ebner",
    "Arno Falkner",
    "Benno Gruber",
    "Curt Hauser",
    "Dietmar Ilg",
    "Egon Jost",
    "Ferdinand Kraus",
    "Gustav Lindner",
    "Helmut Mahler",
    "Ivo Nagel",
    "Jakob Oster",
    "Konrad Pfeiffer",
    "Leon Quint",
    "Martin Rieger",
    "Nikolas Stahl",
    "Otto Trautmann",
    "Peter Unger",
    "Ralf Vollrath",
    "Simon Winkler",
    "Thorsten Adler",
    "Uwe Brandt",
    "Viktor Conradi",
    "Walter Decker",
    "Anselm Ebeling",
)

# Issue #338: enlarged from 9 to 32 entries, same rationale/constraints as
# _PERSON_POOL above.
_TERM_POOL: tuple[str, ...] = (
    "Projekt Polarstern",
    "Vorgang Silberpfeil",
    "Initiative Tannwald",
    "Vorhaben Eichberg",
    "Programm Nordlicht",
    "Projekt Steinadler",
    "Verfahren Lindenhof",
    "Vorhaben Rabenstein",
    "Initiative Falkenberg",
    "Projekt Bergkristall",
    "Vorgang Feuerstein",
    "Initiative Wolkenbruch",
    "Vorhaben Sturmwind",
    "Programm Regenbogen",
    "Verfahren Schneesturm",
    "Projekt Eisregen",
    "Vorgang Sonnenwende",
    "Initiative Mondlicht",
    "Vorhaben Sternschnuppe",
    "Programm Windhauch",
    "Verfahren Wellenschlag",
    "Projekt Flussbett",
    "Vorgang Bergpfad",
    "Initiative Waldlichtung",
    "Vorhaben Felsenriff",
    "Programm Dornenhecke",
    "Verfahren Distelfeld",
    "Projekt Kiefernwald",
    "Vorgang Farngrund",
    "Initiative Moosgrund",
    "Vorhaben Heidekraut",
    "Programm Erlenbruch",
)

# Issue #338: enlarged from 6 to 32 entries, same rationale/constraints as
# _PERSON_POOL above.
_ORG_POOL: tuple[str, ...] = (
    "Brunnen Technik AG",
    "Abteilung Entwicklung Nord",
    "Team Atlas",
    "Gruppe Meridian",
    "Sparte Hofgarten",
    "Bereich Talblick",
    "Abteilung Orion",
    "Team Zenit",
    "Gruppe Suedpol",
    "Sparte Kompass",
    "Bereich Leitstern",
    "Abteilung Horizont",
    "Team Quelle",
    "Gruppe Wellenkamm",
    "Sparte Bergkamm",
    "Bereich Waldrand",
    "Abteilung Feldrand",
    "Team Hochebene",
    "Gruppe Tiefental",
    "Sparte Sonnenhang",
    "Bereich Sternwarte",
    "Abteilung Kristallhang",
    "Team Silberbach",
    "Gruppe Goldgrund",
    "Sparte Kupferfeld",
    "Bereich Zinnwald",
    "Abteilung Bronzehain",
    "Team Perlbach",
    "Gruppe Achatstein",
    "Sparte Opalgrund",
    "Bereich Rubinfeld",
    "Abteilung Saphirtal",
)

_POOLS: dict[str, tuple[str, ...]] = {
    "person": _PERSON_POOL,
    "term": _TERM_POOL,
    "org_unit": _ORG_POOL,
}

# Pool for learn-time re-mints (issue #81): once a surrogate has been flattened into
# ``SurrogateMapping``'s real -> surrogate registry, the referent's original kind
# (person/term/org_unit) is no longer tracked there, so a replacement can't be drawn
# from a kind-specific pool. Kept disjoint (distinct given names/word tokens) from
# every other pool above and from review.py's ``_PROVISIONAL_POOL`` so a replacement
# never collides with an already-active surrogate from another pool.
# Issue #338: enlarged from 8 to 32 entries, same rationale/constraints as
# _PERSON_POOL above.
_REPLACEMENT_POOL: tuple[str, ...] = (
    "Ruth Vollmer",
    "Wolfgang Ehrlich",
    "Sabine Krug",
    "Norbert Beckmann",
    "Ottilie Rathke",
    "Kurt Steinmetz",
    "Waltraud Nickel",
    "Lorenz Bruckner",
    "Bertram Frey",
    "Dankwart Grimm",
    "Annegret Hummel",
    "Brigitte Ingold",
    "Christel Jaeger",
    "Dagmar Krueger",
    "Edith Lauer",
    "Franziska Marx",
    "Gudrun Neumann",
    "Hedwig Osswald",
    "Ingrid Prinz",
    "Johanna Quandt",
    "Karin Roth",
    "Liesel Sattler",
    "Mechthild Thoma",
    "Nora Ullrich",
    "Olga Vogel",
    "Petra Wenzel",
    "Renate Amend",
    "Silvia Bode",
    "Traudel Cramer",
    "Ursula Delling",
    "Vera Ehlert",
    "Wilma Frost",
)

# ADR-0052 (issue #335, applying #330's decision to this module's own sibling
# fallbacks): past a named pool, a fallback surrogate is a single opaque
# ASCII token -- no natural-language word, no free-standing integer, no
# whitespace -- drawn from a namespace reserved against ever being minted as
# a real (`is_reserved_provisional_surrogate_form` below). Every path/kind
# that can exhaust its own pool gets its OWN prefix, by construction, so no
# two paths/kinds can ever render an identical fallback token at the same
# numeric position -- unlike `review.py`'s own person/organization pools,
# which still share one prefix (`BFX`) and are deliberately left as-is: that
# pre-existing ambiguity is `review.py`'s own walk (#330/#331), out of this
# issue's scope, which touches only this module's two sibling fallback paths.
REVIEW_FALLBACK_PREFIX = "BFX"  # review.py's provisional-inbox pools (#330)
REPLACEMENT_FALLBACK_PREFIX = "BFR"  # next_replacement_surrogate (#81, this issue)
POOL_FALLBACK_PREFIXES: dict[str, str] = {
    "person": "BFP",
    "term": "BFT",
    "org_unit": "BFO",
}
DEFAULT_POOL_FALLBACK_PREFIX = "BFK"  # any kind absent from _POOLS/POOL_FALLBACK_PREFIXES

# The single source of truth for the WHOLE reserved-namespace family's shape
# (ADR-0052 decision 2, issue #335): every prefix any fallback path in the
# process can mint, above -- kept here (a leaf module review.py already
# depends on, per issue #332's precedent moving `_real_value_pattern` here for
# the identical reason) rather than in review.py, so review.py can import the
# one shared recognizer instead of keeping a second one that only knew its
# own `BFX` prefix and would drift the moment a sibling path added its own.
_ALL_RESERVED_PREFIXES: tuple[str, ...] = (
    REVIEW_FALLBACK_PREFIX,
    REPLACEMENT_FALLBACK_PREFIX,
    *dict.fromkeys(POOL_FALLBACK_PREFIXES.values()),
    DEFAULT_POOL_FALLBACK_PREFIX,
)
_RESERVED_SURROGATE_RE = re.compile(
    rf"^(?:{'|'.join(_ALL_RESERVED_PREFIXES)})\d{{4,}}$"
)


def is_reserved_provisional_surrogate_form(value: str) -> bool:
    """True if ``value`` matches ANY reserved-namespace fallback shape in the
    family (ADR-0052) -- one of the prefixes above, followed by four-or-more
    zero-padded digits.

    A closed syntactic class, not an open blocklist of English words (#301):
    a candidate real matching this form must never be minted (enforced in
    :meth:`review.ReviewInbox.upsert`), so a value that appears in
    Blindfold's own documentation -- including this ADR and `CONTEXT.md` --
    can never be re-detected as a novel real and reproduce the #328 deadlock,
    regardless of which fallback path originally minted that shape.
    """
    return bool(_RESERVED_SURROGATE_RE.match(value))


# Issue #335 (mirroring review._MAX_FALLBACK_ATTEMPTS, issue #331): a bound on
# how many fallback positions either walk below will try before giving up.
# Both named pools are guarded against known-real collision (issue #80), but
# the numbered fallback has no other stop condition -- a known value that
# collides with every fallback candidate in range (see review.py's own
# docstring for the general shape of this hazard) would otherwise walk the
# integers upward forever, hanging the proxy instead of failing closed.
# Generous -- this should never be reached by real traffic.
_MAX_FALLBACK_ATTEMPTS = 10_000


class FallbackSurrogatePoolExhaustedError(Exception):
    """A ``store._mint`` fallback surrogate walk has no mint-time-disjoint
    candidate left to issue, even after walking ``_MAX_FALLBACK_ATTEMPTS``
    positions past its named pool (ADR-0052 decision 3, issue #335).

    The sibling of :class:`review.ProvisionalPoolExhaustedError` for this
    module's two fallback paths (:func:`next_replacement_surrogate`,
    :func:`mint_surrogates`). Raised instead of walking the numbered fallback
    forever -- fail-closed (ADR-0009), and its own reason distinct in shape
    from a leak reason: the message names only the exhausted ``path`` (the
    literal ``"replacement"``, or a kind such as ``"person"``), never a real
    value or a candidate surrogate.
    """

    def __init__(self, path: str) -> None:
        super().__init__(
            f"the {path!r} surrogate fallback is exhausted -- no mint-time-disjoint "
            f"candidate remains to issue after {_MAX_FALLBACK_ATTEMPTS} attempts"
        )
        self.path = path


def _replacement_pool_entry(position: int) -> str:
    if position < len(_REPLACEMENT_POOL):
        return _REPLACEMENT_POOL[position]
    return f"{REPLACEMENT_FALLBACK_PREFIX}{position:04d}"


def next_replacement_surrogate(
    start_position: int, known_values: Iterable[str]
) -> tuple[str, int]:
    """The first mint-time-disjoint replacement at or after ``start_position``, and
    the cursor position to resume from on the next call (issue #81).

    Mirrors :func:`_next_provisional` in ``review.py``: walks ``_REPLACEMENT_POOL``
    (falling back to a numbered surrogate once exhausted), skipping any entry that
    collides with ``known_values`` -- the same closed-world set the pre-egress leak
    gate consults -- so a re-minted replacement can never itself be stale on arrival.

    Bounded (issue #335, mirroring #331): tries at most ``_MAX_FALLBACK_ATTEMPTS``
    positions before raising :class:`FallbackSurrogatePoolExhaustedError` --
    fail-closed rather than looping forever when something known collides with
    every fallback candidate in range.
    """
    known = list(known_values)
    position = start_position
    for _ in range(_MAX_FALLBACK_ATTEMPTS):
        candidate = _replacement_pool_entry(position)
        position += 1
        if not collides_with_known_entity(candidate, known):
            return candidate, position
    raise FallbackSurrogatePoolExhaustedError("replacement")


def _pool_entry(kind: str, position: int) -> str:
    """The candidate surrogate at raw ``position`` in ``kind``'s pool (or its numbered
    fallback once the pool is exhausted). Pure function of (kind, position) -- no
    collision-skipping -- so :func:`mint_surrogates` can walk positions deterministically.
    """
    pool = _POOLS.get(kind, ())
    if position < len(pool):
        return pool[position]
    prefix = POOL_FALLBACK_PREFIXES.get(kind, DEFAULT_POOL_FALLBACK_PREFIX)
    return f"{prefix}{position:04d}"


@functools.lru_cache(maxsize=None)
def _real_value_pattern(value: str) -> re.Pattern[str]:
    """Word-boundary-only match for a known real entity value (issue #293).

    The single source of truth for what "a known real value occurs in this text"
    means -- ``engine.leak_gate`` imports this same function rather than
    reimplementing the rule, so mint-time disjointness (:func:`collides_with_known_entity`)
    and the gate that actually blocks egress can never silently drift apart again
    (ADR-0052 decision 4; issue #332 fixed the drift #293 left behind).

    Boundaries are "not adjacent to a word character" (so ``"Weber"`` inside
    ``"Weberei"`` still doesn't match), deliberately WITHOUT the closed-set
    inflectional-suffix extension :func:`engine._surrogate_pattern` uses for
    surrogates -- that set includes bare ``"s"``/``"en"``, which would let an
    ordinary common-word real (``"Prompt"``) match right back inside
    ``"Prompts"``/``"PromptCache"``, exactly the over-match #293 fixed for the
    gate. A real value's own bare word-boundary occurrence is all the gate and
    the mint-time collision check need to catch.
    """
    return re.compile(rf"(?<!\w){re.escape(value)}(?!\w)")


def collides_with_known_entity(candidate: str, known_values: Iterable[str]) -> bool:
    """True if any ``known_values`` entry occurs in ``candidate`` at a word boundary.

    Uses the identical rule :func:`_real_value_pattern` gives ``engine.leak_gate``
    (issue #293's word-boundary match, not raw substring containment) so a
    candidate that passes here can never trip the leak gate once minted and
    injected (ADR-0052 decision 4; issue #332). Before this change the two had
    silently drifted: this check tested raw substring containment, which is
    *stricter* than the gate it claimed to mirror -- refusing candidates
    (e.g. a two-character real colliding with an opaque reserved token like
    ``"BFX0008"``, ADR-0052) the gate would never have blocked.
    ``known_values`` is the closed-world set of canonical names and Variations
    -- the same set ``SurrogateMapping.real_values()`` exposes.
    """
    return any(
        known and _real_value_pattern(known).search(candidate) for known in known_values
    )


def surrogate_space_match(candidate: str, surrogate_values: Iterable[str]) -> str | None:
    """Return the first ``surrogate_values`` entry ``candidate`` collides with --
    equal to it, or a whole, distinctive **surrogate component** of it
    (CONTEXT.md) -- or ``None`` if ``candidate`` shares no such span with any
    of them.

    The mirror-direction check to :func:`collides_with_known_entity`: that
    function keeps a newly-minted *surrogate* disjoint from known *real*
    values (issue #80) by testing containment one way; this one keeps a
    newly-L3-confirmed *real* candidate from being minted when it is actually
    a fragment of surrogate-space re-entering the transcript (issue #292) --
    Blindfold's own prior output, not a novel referent -- by testing
    containment the other way.

    Matching is **word-boundary and stopword-filtered**, the same basis
    ``engine._component_restore_map`` uses for the restore direction (ADR-0036)
    -- NOT a raw ``candidate in value`` substring test. A cycle-1 review found
    that a raw substring test dismissed genuinely novel real values sharing
    only characters (not a whole word) with an unrelated live surrogate --
    e.g. ``"Kurt"`` inside ``"Kurtis Vale"``, or ``"Alan"`` inside
    ``"Alana Bright"`` -- leaving them un-blindfolded in plaintext (a leak-
    audit clause A regression: fail-closed had become fail-open). A
    CONTEXT.md "surrogate component" is a whole word token of a multi-word
    surrogate, so the match is restricted to that: ``candidate`` equal to the
    whole surrogate value, or equal to one of its distinctive (non-stopword)
    word tokens.
    """
    if not candidate:
        return None
    for value in surrogate_values:
        if not value:
            continue
        if candidate == value:
            return value
        words = {
            word
            for word in re.findall(r"\w+", value)
            if word not in _COMPONENT_STOPWORDS
        }
        if candidate in words:
            return value
    return None


def pool_entry_collides_with_corpus(candidate: str, corpus_text: str) -> bool:
    """True if ``candidate`` (a surrogate pool entry under consideration) already
    occurs -- as its whole value, or a whole, distinctive word-boundary
    component -- in ``corpus_text``, the text being processed for this exchange.

    The corpus-vs-pool counterpart to :func:`collides_with_known_entity`'s
    pool-vs-known-real check (issue #80), extended per issue #292: a pool entry
    that already appears as plain text in the hop must never be assigned to a
    referent, or the assigned surrogate collides with prose already present in
    the very exchange it is injected into. Matching is word-boundary and
    stopword-filtered -- the same basis :func:`surrogate_space_match` uses --
    not a raw substring test, so a pool entry merely sharing characters (not a
    whole word) with the corpus is not treated as a collision.
    """
    if not candidate or not corpus_text:
        return False
    if re.search(rf"\b{re.escape(candidate)}\b", corpus_text):
        return True
    corpus_words = set(re.findall(r"\w+", corpus_text))
    for word in re.findall(r"\w+", candidate):
        if word in _COMPONENT_STOPWORDS:
            continue
        if word in corpus_words:
            return True
    return False


def _next_pool_entry(kind: str, start_position: int, known: list[str]) -> tuple[str, int]:
    """The first mint-time-disjoint ``kind``-pool entry at or after
    ``start_position``, and the position to resume from (issue #335).

    Bounded (mirroring :func:`next_replacement_surrogate` /
    ``review._next_provisional``): tries at most ``_MAX_FALLBACK_ATTEMPTS``
    positions before raising :class:`FallbackSurrogatePoolExhaustedError`.
    """
    position = start_position
    for _ in range(_MAX_FALLBACK_ATTEMPTS):
        candidate = _pool_entry(kind, position)
        position += 1
        if not collides_with_known_entity(candidate, known):
            return candidate, position
    raise FallbackSurrogatePoolExhaustedError(kind)


def mint_surrogates(kind: str, count: int, known_values: Iterable[str] = ()) -> list[str]:
    """Return ``count`` deterministic, mint-time-disjoint surrogates for ``kind``, in order.

    Walks the plausible-name pool (falling back to numbered surrogates once exhausted),
    skipping any entry that collides with ``known_values`` (issue #80). A skipped entry
    is never reused for a later referent, and every non-colliding entry keeps the exact
    position it would have had without collision-skipping -- so this is a strict
    superset-preserving refinement of the old positional ``mint_surrogate``: E-stable
    for every referent whose assigned entry never collides.

    Bounded per referent (issue #335): if something known collides with every
    fallback candidate in range, :func:`_next_pool_entry` raises
    :class:`FallbackSurrogatePoolExhaustedError` -- fail-closed rather than
    walking positions forever without ever appending to ``result``.
    """
    known = list(known_values)
    result: list[str] = []
    position = 0
    while len(result) < count:
        candidate, position = _next_pool_entry(kind, position, known)
        result.append(candidate)
    return result


def mint_surrogate(kind: str, index: int, known_values: Iterable[str] = ()) -> str:
    """Return the stable, mint-time-disjoint surrogate for the ``index``-th referent
    of ``kind``, given the full ``known_values`` closed-world set (issue #80).

    A thin single-referent wrapper over :func:`mint_surrogates` for callers (the
    Postgres ETL) that mint one referent at a time rather than a whole kind's batch;
    it recomputes the same deterministic walk from position 0 each call, so it agrees
    with :func:`mint_surrogates` on every index.
    """
    return mint_surrogates(kind, index + 1, known_values)[index]
