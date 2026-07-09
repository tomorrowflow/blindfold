"""Deterministic PII / entity detection (ADR-0003).

This module contains two stages of the layered detector:

* **L1** — regex over the full payload. Catches the *contactable PII* set (emails,
  phones, IBANs, IDs) cheaply and with high precision. Its output is a list of
  :class:`PiiSpan` records — what L1 flagged, by kind — for the surrogate engine to
  mint reserved-namespace replacements for (ADR-0005).
* **L2** — 4-pass curated entity-graph detection. Walks the text and flags candidate
  spans that match a seeded :class:`Entity` (canonical or any variation) in four
  passes: exact → normalized (unidecode) → fuzzy (Levenshtein ≤2) → first-name
  ambiguity. German-aware, with stopwords and dedup. Variations of one entity share
  the entity's surrogate so coreference (ADR-0004) collapses naturally. Algorithm
  reused as a concept from voice-diary's ``entity_detector.py`` (ADR-0012), not as
  code.

Both stages are pure (text in, spans out): the engine wires detection into the
per-hop blindfold pass, and the proxy round trip is the network-boundary test that
proves no real PII / entity value egresses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from unidecode import unidecode

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# E.164-flavoured international phone: leading `+`, then 7-15 digits with optional
# separators (space, dash, dot). Anchored to the leading `+` to keep precision high
# (bare digit runs in source code, bug numbers, etc. would otherwise false-positive).
_PHONE_RE = re.compile(r"\+\d[\d \-.()]{6,18}\d")

# IBAN: 2 letters + 2 check digits + up to 30 alphanumerics, optionally grouped in
# 4-char chunks (the canonical printed format). Anchored on word boundaries.
_IBAN_RE = re.compile(
    r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,4})?\b"
)

# ID: structured marker (`ID:` / `ID-` with optional whitespace) followed by 6+ digits.
# The explicit prefix gates precision: free-form numbers (line numbers, sizes, ports)
# stay out of L1; ambiguous numeric tokens are L2/L3's job, not L1's.
_ID_RE = re.compile(r"\bID[:\-][ ]?\d{6,}\b")

_PII_REGEXES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", _EMAIL_RE),
    ("phone", _PHONE_RE),
    ("iban", _IBAN_RE),
    ("id", _ID_RE),
)


@dataclass(frozen=True)
class PiiSpan:
    """A single L1-detected contactable-PII value."""

    kind: str
    value: str


def detect_pii(text: str) -> list[PiiSpan]:
    """Return L1 PII spans found in ``text`` (deterministic regex, full-payload)."""
    return [
        PiiSpan(kind=kind, value=match.group())
        for kind, regex in _PII_REGEXES
        for match in regex.finditer(text)
    ]


# Token = a contiguous run of word characters (Unicode letters, digits, underscore).
# German letters (ä, ö, ü, ß) match ``\w`` under Python's default Unicode rules.
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class Entity:
    """An entity-graph node: a canonical real value, its variations, and its surrogate.

    Variations are coreference surface forms (full name, first name, initials,
    misspelling) — every variation resolves to ``surrogate``.
    """

    canonical: str
    variations: tuple[str, ...]
    surrogate: str


@dataclass(frozen=True)
class DetectedSpan:
    """A candidate span flagged by L2: where in the text, which entity, which pass."""

    start: int
    end: int
    text: str
    real: str
    surrogate: str
    pass_name: str


_FUZZY_MAX_DISTANCE = 2
# Below this many characters, Levenshtein ≤2 admits noise (every 4-letter token
# becomes a near-miss of every other 4-letter token). Voice-diary's detector applies
# the same length floor.
_FUZZY_MIN_SURFACE_LEN = 5

# German + English function words that must never be flagged. Curated to cover the
# common false-positive sources for the fuzzy pass — short verbs, articles, modals,
# prepositions. (Stopword set, not a dictionary: under-redaction risk is bounded
# because a stopword would still be flagged when it matches a surface exactly.)
_STOPWORDS: frozenset[str] = frozenset(
    s.lower()
    for s in (
        # German function words
        "der", "die", "das", "den", "dem", "des",
        "ein", "eine", "einen", "einem", "einer", "eines",
        "und", "oder", "aber", "doch", "noch", "auch", "nur",
        "ist", "sind", "war", "waren", "sein", "wird", "werden", "wurde",
        "haben", "hatte", "hatten", "habe", "hat",
        "kann", "können", "konnte", "soll", "sollen", "muss", "müssen",
        "wegen", "wenn", "weil", "denn", "dass", "ob", "obwohl",
        "über", "unter", "vor", "nach", "bei", "mit", "von", "zu", "zur", "zum",
        "auf", "aus", "für", "gegen", "ohne", "um", "ums",
        "sich", "ihn", "ihm", "ihr", "ihre", "ihrer", "ihren",
        "mein", "meine", "dein", "deine",
        "diese", "dieser", "dieses", "diesem", "diesen",
        "alle", "alles", "allen", "vieler", "viele", "vielen", "wenige",
        "heute", "gestern", "morgen", "immer", "nie", "schon", "wieder",
        # English function words / pronouns
        "the", "and", "or", "but", "for", "with", "from", "into",
        "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did",
        "this", "that", "these", "those", "such",
        "what", "when", "where", "which", "while", "would", "could", "should",
        "please", "about", "after", "before", "again",
    )
)


def detect_l2(text: str, entities: list[Entity]) -> list[DetectedSpan]:
    """Return the candidate spans L2 flags in ``text`` against ``entities``."""
    surfaces = _index_surfaces(entities)
    surfaces_normalized = _index_normalized_surfaces(entities)
    fuzzy_surfaces = _fuzzy_surface_index(entities)
    max_window_tokens = max(
        _max_surface_token_count(entities), _max_fuzzy_reach_tokens(fuzzy_surfaces)
    )
    raw: list[DetectedSpan] = []
    for token_text, token_start, token_end in _walk_token_windows(
        text, max_window_tokens
    ):
        exact_match = _pick(surfaces.get(token_text))
        if exact_match is not None:
            entity, surface, ambiguous = exact_match
            pass_name = "first_name" if ambiguous else "exact"
            raw.append(_span(token_start, token_end, surface, entity, pass_name))
            continue
        normalized_match = _pick(surfaces_normalized.get(_normalize(token_text)))
        if normalized_match is not None:
            entity, surface, ambiguous = normalized_match
            pass_name = "first_name" if ambiguous else "normalized"
            raw.append(_span(token_start, token_end, surface, entity, pass_name))
            continue
        if token_text.lower() in _STOPWORDS:
            continue
        fuzzy = _fuzzy_match(token_text, fuzzy_surfaces)
        if fuzzy is not None:
            entity, surface = fuzzy
            raw.append(_span(token_start, token_end, surface, entity, "fuzzy"))
    return _dedup_overlaps(raw)


def _pick(
    matches: list[tuple[Entity, str]] | None,
) -> tuple[Entity, str, bool] | None:
    """Pick the first declared entity from a surface match-list, marking ambiguity.

    Deterministic + protection-over-disambiguation: when several entities share a
    surface form (e.g. two persons named "Anna"), we still inject a surrogate (an
    under-redacted real name would leak — privacy bug), tagged as ``first_name`` so
    L3 can disambiguate later.
    """
    if not matches:
        return None
    entity, surface = matches[0]
    return entity, surface, len(matches) > 1


def _span(
    start: int, end: int, surface: str, entity: Entity, pass_name: str
) -> DetectedSpan:
    return DetectedSpan(
        start=start,
        end=end,
        text=surface,
        real=entity.canonical,
        surrogate=entity.surrogate,
        pass_name=pass_name,
    )


def _fuzzy_surface_index(entities: list[Entity]) -> list[tuple[str, Entity, str]]:
    """Long-enough single-token surfaces eligible for fuzzy comparison.

    Multi-token variations are skipped at the fuzzy seam — we only fuzz word-shaped
    tokens against word-shaped surfaces to keep the candidate set small and the
    false-positive rate tractable.
    """
    eligible: list[tuple[str, Entity, str]] = []
    for entity in entities:
        for surface in (entity.canonical, *entity.variations):
            if " " in surface or len(surface) < _FUZZY_MIN_SURFACE_LEN:
                continue
            eligible.append((_normalize(surface), entity, surface))
    return eligible


def _fuzzy_match(
    token_text: str, fuzzy_surfaces: list[tuple[str, Entity, str]]
) -> tuple[Entity, str] | None:
    if len(token_text) < _FUZZY_MIN_SURFACE_LEN:
        return None
    needle = _normalize(token_text)
    best: tuple[int, Entity, str] | None = None
    for normalized_surface, entity, surface in fuzzy_surfaces:
        if abs(len(needle) - len(normalized_surface)) > _FUZZY_MAX_DISTANCE:
            continue
        distance = _bounded_levenshtein(needle, normalized_surface, _FUZZY_MAX_DISTANCE)
        if distance is None or distance == 0:
            continue
        if best is None or distance < best[0]:
            best = (distance, entity, surface)
    if best is None:
        return None
    return best[1], best[2]


def _bounded_levenshtein(a: str, b: str, max_distance: int) -> int | None:
    """Levenshtein distance, or ``None`` if it would exceed ``max_distance``.

    Standard dynamic-programming table with early exit when the running row min
    exceeds the bound — cheap because ``max_distance`` is 2 and surfaces are short.
    """
    if abs(len(a) - len(b)) > max_distance:
        return None
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        row_min = current[0]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current[j] = min(
                previous[j] + 1,        # deletion
                current[j - 1] + 1,     # insertion
                previous[j - 1] + cost, # substitution
            )
            if current[j] < row_min:
                row_min = current[j]
        if row_min > max_distance:
            return None
        previous = current
    distance = previous[-1]
    return distance if distance <= max_distance else None


def _normalize(value: str) -> str:
    """unidecode-fold to lower-case ASCII for the normalized pass.

    Casing is folded too — German users often type ASCII fallbacks in lower case
    ("muller") even when the canonical is "Muller".
    """
    return unidecode(value).lower()


def _dedup_overlaps(spans: list[DetectedSpan]) -> list[DetectedSpan]:
    """Drop spans contained in a longer span at the same position. Longest wins.

    Coreference (ADR-0004): same surrogate at the same position is one match, not many.
    """
    spans_by_length = sorted(spans, key=lambda s: (-(s.end - s.start), s.start))
    kept: list[DetectedSpan] = []
    for span in spans_by_length:
        if any(_overlaps(span, k) for k in kept):
            continue
        kept.append(span)
    return sorted(kept, key=lambda s: s.start)


def _overlaps(a: DetectedSpan, b: DetectedSpan) -> bool:
    return a.start < b.end and b.start < a.end


def _index_surfaces(entities: list[Entity]) -> dict[str, list[tuple[Entity, str]]]:
    """Map every surface form (canonical + variations) to all owning entities.

    A surface like ``"Anna"`` is shared when two persons declare it as a variation;
    that ambiguity routes to the first-name pass (ADR-0003 pass 4).
    """
    by_surface: dict[str, list[tuple[Entity, str]]] = {}
    for entity in entities:
        for surface in (entity.canonical, *entity.variations):
            by_surface.setdefault(surface, []).append((entity, surface))
    return by_surface


def _index_normalized_surfaces(
    entities: list[Entity],
) -> dict[str, list[tuple[Entity, str]]]:
    """Map the unidecode-folded surface forms to all owning entities (see above)."""
    by_norm: dict[str, list[tuple[Entity, str]]] = {}
    for entity in entities:
        for surface in (entity.canonical, *entity.variations):
            by_norm.setdefault(_normalize(surface), []).append((entity, surface))
    return by_norm


def _max_surface_token_count(entities: list[Entity]) -> int:
    """Longest known surface's token count, or 1 if there is nothing to match.

    No exact/normalized surface can ever match a window longer than itself, so this
    bounds those two passes exactly -- typically <=4 in practice. The fuzzy pass has
    its own, independent bound (see ``_max_fuzzy_reach_tokens``); the walk must be
    capped by the larger of the two.
    """
    longest = 1
    for entity in entities:
        for surface in (entity.canonical, *entity.variations):
            longest = max(longest, len(_TOKEN_RE.findall(surface)))
    return longest


def _max_fuzzy_reach_tokens(fuzzy_surfaces: list[tuple[str, Entity, str]]) -> int:
    """Longest window (in tokens) the fuzzy pass could still match, or 1 if none.

    Fuzzy surfaces are single-token (no spaces, see ``_fuzzy_surface_index``), but
    a query window can still span *multiple* text tokens and fuzzy-match one --
    e.g. a typo that inserts whitespace into a name ("Wegner" mistyped as
    "We gner"). Removing each of a window's ``k - 1`` embedded spaces costs at
    least one edit apiece (no cheaper operation collapses two characters at once),
    so a ``k``-token window can only be within ``_FUZZY_MAX_DISTANCE`` edits of a
    space-free surface when ``k - 1 <= _FUZZY_MAX_DISTANCE``. That bound holds
    regardless of any exact/normalized surface's own token count.
    """
    if not fuzzy_surfaces:
        return 1
    return 1 + _FUZZY_MAX_DISTANCE


def _walk_token_windows(text: str, max_window_tokens: int):
    """Yield (joined_text, start, end) for runs of 1..max_window_tokens tokens.

    Matching against multi-token surfaces ("Enervia AG", "Stefan Wegner") needs the
    detector to consider windows of adjacent tokens, separated by single spaces in
    the canonical surface form. Capping window length at ``max_window_tokens`` turns
    the walk from O(n^3) into O(n * max_window_tokens).
    """
    tokens = [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]
    for i in range(len(tokens)):
        end_j = min(i + max_window_tokens, len(tokens))
        for j in range(i, end_j):
            joined = " ".join(t[0] for t in tokens[i : j + 1])
            yield joined, tokens[i][1], tokens[j][2]
