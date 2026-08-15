"""L3 candidate-span adjudication (ADR-0003).

L3 is invoked **only on flagged candidate spans plus minimal context** — never on the
full payload. The deterministic passes (L1+L2) have already protected known entities;
L3's job is to adjudicate the leftovers: unknown capitalized tokens, fuzzy near-misses,
ambiguous first names. Cost scales with the number of candidate spans, not payload
size — which is what makes the proxy tractable on large code bodies.

The adjudicator itself (Ollama) is a network-boundary seam: production wires a real
local-LLM client; tests substitute a recording stub. This module owns candidate-span
*selection* and *context-windowing*; the adjudicator owns the LLM call. A content
cache, keyed on the individual candidate's own ``(text, context)``, prevents
re-scanning unchanged chunks across agent turns.

Issue #283 (ADR-0048 corollary 3): batched adjudication (N candidates in one prompt)
is gone -- one candidate, one prompt, always. #260's measurement showed a batched
verdict moves with the candidate's position and the batch's size, never agreeing
reliably with the solo verdict ADR-0048 designates as the reference answer. The
content cache reverts to per-candidate keying as a consequence (it was moved to
per-*group* keying by issue #261 specifically to keep batch composition a pure
function of the hop's inputs; with no batch left to protect, per-candidate keying
is simply the natural shape and, if anything, serves more cache hits since one
candidate's miss can no longer force its would-be batch-mates to miss too).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .detection import Entity

if TYPE_CHECKING:
    from .review import Allowlist

# Window of context around a candidate span handed to L3 — wide enough to disambiguate
# ("Klaus signed the contract" vs. "Klaus the bus driver"), narrow enough that cost
# is bounded by span count, not payload size.
_CONTEXT_WINDOW = 40

# Word run (any script) -- candidacy itself is decided by _is_capitalized_token,
# not by this character class, so no diacritic needs enumerating here (issue #288:
# the old [A-ZÄÖÜ][a-zäöüß]+ class special-cased German umlauts/ß only, so a token
# like "Tomás" -- 'á' isn't German -- broke the continuation run and never matched
# at all, never becoming an L3 candidate).
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _is_capitalized_token(token: str) -> bool:
    """True if ``token`` is Title-Case: one uppercase letter then only lowercase
    letters, in any script. Matches the intent of the old ``[A-ZÄÖÜ][a-zäöüß]+``
    class -- excludes ALL-CAPS acronyms, digit/underscore runs, and internal-cap
    tokens ("GmbH", "FastAPI") -- without hand-enumerating every diacritic a real
    given/family name might carry.
    """
    return (
        len(token) >= 2
        and token.isalpha()
        and token[0].isupper()
        and token[1:].islower()
    )


def _capitalized_token_matches(text: str):
    """Yield ``re.Match`` objects for each Title-Case word in ``text`` (see
    :func:`_is_capitalized_token`) -- the Unicode-general replacement for what
    used to be ``_CAPITALIZED_RE.finditer(text)``."""
    return (m for m in _WORD_RE.finditer(text) if _is_capitalized_token(m.group(0)))


# Phone-*shaped* candidate matcher (issue #277): `_PHONE_RE` (detection.py, L1) is
# anchored on a leading `+` for precision, so a NANPA-format number missing it --
# `415-555-0142`, `(415) 555-0142`, `555-0142` -- never reaches L1. This regex is
# deliberately loose *relative to `_PHONE_RE`* (no mandatory `+`) but not
# unstructured: it keeps the real structural shape of a NANPA number (an optional
# 3-digit area code, parenthesized or separator-joined, followed by a 3-digit
# exchange and a 4-digit line) so it doesn't fire on every bare digit run (order
# numbers, dimensions, ports, line numbers). Candidates only -- L3 adjudication
# (engine.py) decides which are genuinely phone numbers; see the issue's "hybrid"
# decision for why neither stage alone is correct.
#
# The exchange-line pair is dash-only, never dot-joined: the blast-radius
# measurement (issue #277 acceptance criteria) found a dotted 3+4-digit group is
# exactly the shape of a decimal build/version fragment or a GPS coordinate
# (`-122.4194`) -- a dash is a strong phone-specific signal a dot isn't, so
# excluding it at the matcher removes that whole false-positive class rather than
# relying on L3 to reject every occurrence.
_PHONE_SHAPED_RE = re.compile(
    r"(?<!\d)(?:\(\d{3}\)[ ]?|\d{3}[ \-.])?\d{3}-\d{4}(?!\d)"
)

# ADR-0033 positional evidence: what precedes a token for it to count as a
# sentence, quotation, or heading start -- start of the hop text, start of a
# line (covers markdown headings and bullet/numbered list markers, optionally
# followed by a bold-label marker -- issue #141: "- **Assist**: ..." nests a
# bold label inside a bullet, separated from the bullet marker by a space),
# after sentence-ending punctuation, or right after an opening quotation mark.
_POSITION_START_RE = re.compile(
    r"""
    (?: (?:\A|\n)[ \t]*(?:[#>*+-]+|\d+[.)])?[ \t]*(?:\*\*|__)?[ \t]*
                                             # start of text/line, optional heading/
                                             # bullet/numbered marker, optional
                                             # bold-label marker (**Label**/__Label__)
      | [.!?]["'’”)\]]*\s+                  # end of a sentence
      | ["'‘“]                              # an opening quotation mark
    )
    ["'‘“]?                                 # the marker may itself be followed by an opening quote
    \Z
    """,
    re.VERBOSE,
)

# ADR-0033 update (issue #161): a *mandatory* bullet/numbered-list marker --
# never a bare heading ('#'), blockquote ('>'), or unmarked paragraph start --
# right after the start of text/line. Narrower than `_POSITION_START_RE`'s
# marker group on purpose: a list item is strong positional evidence on its
# own (see `_is_positional_case_noise`), a heading or bare paragraph start is
# not (a heading like "## Behavior" or a label like "Rules:" still needs
# vocabulary evidence, or a single occurrence would be suppressed as noise).
_LIST_MARKER_START_RE = re.compile(
    r"""
    (?:\A|\n)[ \t]*(?:[*+-]|\d+[.)])[ \t]*(?:\*\*|__)?[ \t]*
    \Z
    """,
    re.VERBOSE,
)

_STOPWORDS_PATH = Path(__file__).with_name("l3_stopwords_en_de.txt")

logger = logging.getLogger(__name__)

# Issue #134: a live-testing session reported 250+ sequential adjudication calls
# against a cold allowlist with no way to tell, while it was happening, whether the
# request was still progressing or stuck -- only raw per-call httpx log lines. This
# is how often (in candidates processed) L3Detector.detect() logs a progress line
# for a single pass, so an operator tailing logs sees forward progress mid-request
# instead of only after the whole pass completes.
_DEFAULT_PROGRESS_LOG_INTERVAL = 25


@lru_cache(maxsize=1)
def _load_sentence_stopwords() -> frozenset[str]:
    """Load the closed-class function-word list (EN+DE, ADR-0023) from the packaged
    data file — articles, pronouns, prepositions, conjunctions, auxiliaries, and
    common capitalized adverbs. The L3 LLM could filter these too, but pre-filtering
    avoids wasting an adjudicator call (and a content-cache slot) on every "The"/
    "Please". Function words are essentially never entity names, so this is a pure
    quality win — it never affects L1/L2 protection (a registered Term or
    entity-graph surface always wins regardless of stopword status).
    """
    words = []
    for line in _STOPWORDS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words.append(line)
    return frozenset(words)


_SENTENCE_STOPWORDS: frozenset[str] = _load_sentence_stopwords()


class L3Unavailable(Exception):
    """Raised when the L3 adjudicator can't complete a request (e.g. Ollama down).

    Fail-closed by default (ADR-0009 / leak-audit clause F): the proxy translates
    this into a clear block rather than letting a novel candidate egress unscanned.
    The per-workspace ``deterministic_only`` opt-in is the documented escape valve.
    """


@dataclass(frozen=True)
class CandidateSpan:
    """A token flagged for L3 adjudication, with the minimal context around it.

    The ``context`` field is what L3 actually sees — a window of characters around
    the span, not the full payload. Keeping the window small is what decouples L3
    latency from payload size (ADR-0003).

    ``context_offset`` is the start index of this exact occurrence of ``text``
    within ``context`` (ADR-0035, issue #155) — derived from the span's own
    ``start``/``end`` position, not a text search, so it points at the correct
    occurrence even when ``text`` repeats or is inflected elsewhere in the window.
    """

    text: str
    start: int
    end: int
    context: str
    context_offset: int = 0


@dataclass(frozen=True)
class L3Adjudication:
    """L3's verdict for a candidate span.

    ``is_entity`` is the load-bearing flag: the engine mints a surrogate for
    confirmed entities, ignores rejections.

    ``entity_type`` (issue #167) is the detected entity's coarse kind (e.g.
    ``"person"``, ``"organization"``), used by the mint pass to pick a
    type-appropriate surrogate pool (ADR-0005). ``None`` when the adjudicator
    that produced this verdict doesn't detect a type (the inner LLM
    adjudicators today) — the mint pass falls back to its default pool
    without error, exactly as before this field existed.

    ``span_start``/``span_end`` (issue #170) are the absolute offsets, in the
    coordinate space of the ``text`` passed to :meth:`L3Detector.detect`, of
    the *authoritative* entity extent this verdict confirmed -- e.g. GLiNER's
    own multi-word span, which may be wider than the confirming candidate's
    own single-token ``start``/``end``. ``None`` when the adjudicator that
    produced this verdict has no span concept of its own (the inner LLM
    adjudicators, which only ever confirm/dismiss the single candidate token
    they were asked about) -- the mint pass falls back to the candidate's own
    extent, exactly as before this field existed.
    """

    is_entity: bool
    entity_type: str | None = None
    span_start: int | None = None
    span_end: int | None = None


class L3Adjudicator(Protocol):
    """The network-boundary seam for the local LLM (Ollama).

    Production wires a real Ollama HTTP client behind this protocol; tests
    substitute a recording stub. Either way, the engine only depends on this
    one-method interface — call cost is the test's measure of L3 cost.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication: ...


_DEFAULT_CACHE_MAX_ENTRIES = 4096


def _shift_span(decision: L3Adjudication, delta: int) -> L3Adjudication:
    """Translate ``decision``'s authoritative span offsets by ``delta``.

    A no-op (returns the decision unchanged) when the adjudicator recorded no
    span. Used by :class:`L3ContentCache` to store spans context-relative
    (``delta = -window_left``) and re-anchor them to the querying occurrence
    (``delta = +window_left``).
    """
    if decision.span_start is None and decision.span_end is None:
        return decision
    return replace(
        decision,
        span_start=(
            decision.span_start + delta if decision.span_start is not None else None
        ),
        span_end=(
            decision.span_end + delta if decision.span_end is not None else None
        ),
    )


def _window_left(candidate: CandidateSpan) -> int:
    """The absolute offset of ``candidate.context``'s left edge — the ``delta`` that
    translates a context-relative span to/from this occurrence's absolute position
    (see :func:`_shift_span` and :class:`L3ContentCache`)."""
    return candidate.start - candidate.context_offset


def _candidate_digest(candidate: CandidateSpan) -> str:
    """Digest a single candidate's ``(text, context)`` pair into a cache key.

    Length-prefixing each field (rather than bare concatenation) rules out
    boundary-ambiguity collisions (``"ab"`` + ``"c"`` vs. ``"a"`` + ``"bc"``).
    Hashing rather than keying on the raw tuple also means the cache no longer
    holds real candidate text in its keys (ADR-0022) — an incidental hardening of
    the real-value-store note this class already carried, kept across issue
    #283's reversion to per-candidate keying.
    """
    digest = hashlib.sha256()
    digest.update(f"{len(candidate.text)}:".encode())
    digest.update(candidate.text.encode("utf-8"))
    digest.update(f"\x00{len(candidate.context)}:".encode())
    digest.update(candidate.context.encode("utf-8"))
    return digest.hexdigest()


@dataclass
class L3ContentCache:
    """Cache adjudications keyed by the individual candidate's own ``(text,
    context)`` digest. Unchanged spans — same span, same surroundings — aren't
    re-scanned across agent turns (ADR-0003); a candidate in identical context
    produces an identical ``is_entity``/``entity_type`` verdict.

    Issue #283 (ADR-0048 corollary 3): this is a reversion of issue #261's move to
    per-*group* keying, which existed only to keep batch composition a pure
    function of the hop's inputs while batching existed. With batching gone there
    is no composition left to protect, and per-candidate keying is strictly better
    for the cache-hit rate: one candidate's cache state can no longer force an
    unrelated candidate that merely shared its batch to miss too.

    Issue #207 (residual of #179), preserved across the reversion:
    ``L3Adjudication.span_start``/``span_end`` are NOT position-independent the
    way ``is_entity``/``entity_type`` are — they are absolute offsets into
    whichever hop's text first populated this entry. The identical local
    ``(span_text, context)`` pair recurs at a *different* absolute position
    whenever an agentic transcript re-quotes the same sentence turn over turn
    (the whole point of caching "across agent turns") — reusing the first
    occurrence's absolute span for a later occurrence mis-anchors it. Stored and
    retrieved values translate span_start/span_end through the *querying*
    candidate's own ``window_left`` (``candidate.start - candidate.context_offset``)
    so every retrieval re-anchors to its own occurrence — exactly what a fresh
    (uncached) adjudication of that occurrence would have produced.

    Bounded by ``max_entries`` with least-recently-used eviction, so a
    long-running process's memory stays bounded regardless of how many distinct
    candidates it has ever seen. Never persisted to disk.
    """

    max_entries: int = _DEFAULT_CACHE_MAX_ENTRIES
    _entries: "OrderedDict[str, L3Adjudication]" = field(
        default_factory=OrderedDict
    )

    def get(self, candidate: CandidateSpan) -> L3Adjudication | None:
        key = _candidate_digest(candidate)
        if key not in self._entries:
            return None
        self._entries.move_to_end(key)
        return _shift_span(self._entries[key], _window_left(candidate))

    def put(self, candidate: CandidateSpan, decision: L3Adjudication) -> None:
        key = _candidate_digest(candidate)
        self._entries[key] = _shift_span(decision, -_window_left(candidate))
        self._entries.move_to_end(key)
        if len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)


def select_candidate_spans(
    text: str,
    known_entities: list[Entity],
    allowlist: "Allowlist | None" = None,
    declared_tools: frozenset[str] = frozenset(),
) -> list[CandidateSpan]:
    """Flag the unknown capitalized tokens in ``text``, with minimal context.

    Tokens already covered by an entity-graph surface (canonical or any variation)
    are L2's territory and are NOT re-flagged here. Closed-class function words
    (the EN+DE stopword list, ADR-0023) are filtered to keep the candidate set
    small (a quality optimisation, not a privacy one — L3 would reject "Please"
    anyway, but pre-filtering saves a call and a content-cache slot).
    Tokens the user has rejected (ADR-0010 allowlist) are filtered too — over-
    redaction is the quality bug the learning loop fixes.
    ``declared_tools`` (ADR-0023) suppresses a request's own declared tool
    vocabulary (``tools[].name`` / ``tools[].function.name``) from candidacy for
    that request only — never persisted, never state on this function or its
    caller. Suppression only removes L3 novelty discovery: a declared name that
    is also a registered Term or entity-graph surface is still blindfolded by the
    deterministic L1/L2 passes, which run before L3 (L2 wins).
    A fourth suppression condition — the ADR-0033 positional case heuristic —
    runs after the three above: a token is suppressed when it has positional
    evidence (it is never capitalized mid-sentence in this hop — only at a
    sentence, quotation, heading, or list-marker start) AND either vocabulary
    evidence (its lowercase form appears as a standalone word elsewhere in
    this hop) or list-marker evidence (issue #161: at least one occurrence
    sits at a list/numbered-marker start specifically, the shape of an
    agentic system prompt's one-off skill/tool list, where "vocabulary
    evidence" would never fire since each item's name is used exactly once).
    The positional gate is load-bearing either way: vocabulary evidence alone
    would eat real names ("mark this as done" would suppress "Mark" the
    person too); the positional gate protects any token that is ever
    capitalized mid-sentence, regardless of which suppression signal fired.
    """
    known_surfaces = _known_surfaces(known_entities)
    capitalized_positions = _capitalized_positions(text)
    candidates: list[CandidateSpan] = []
    for match in _capitalized_token_matches(text):
        token = match.group(0)
        if token in _SENTENCE_STOPWORDS:
            continue
        if token in known_surfaces:
            continue
        if allowlist is not None and allowlist.contains(token):
            continue
        if token in declared_tools:
            continue
        if _is_positional_case_noise(token, text, capitalized_positions):
            continue
        start, end = match.start(), match.end()
        context, context_offset = _context_window(text, start, end)
        candidates.append(
            CandidateSpan(
                text=token,
                start=start,
                end=end,
                context=context,
                context_offset=context_offset,
            )
        )
    return candidates


def select_phone_candidate_spans(text: str) -> list[CandidateSpan]:
    """Flag phone-*shaped* spans in ``text`` (issue #277) -- a producer separate
    from :func:`select_candidate_spans`'s capitalized-token pass, feeding the same
    L3 candidate path (:meth:`L3Detector.detect`). Pure function of ``text`` alone,
    same as the capitalized-token pass is pure over its own inputs (#261's
    invariant: candidate selection never depends on history or process state).
    """
    candidates: list[CandidateSpan] = []
    for match in _PHONE_SHAPED_RE.finditer(text):
        start, end = match.start(), match.end()
        context, context_offset = _context_window(text, start, end)
        candidates.append(
            CandidateSpan(
                text=match.group(0),
                start=start,
                end=end,
                context=context,
                context_offset=context_offset,
            )
        )
    return candidates


def _capitalized_positions(text: str) -> dict[str, list[int]]:
    """Pre-scan: map each exact capitalized token to every start offset where it
    appears in ``text`` (ADR-0033). Built once per hop so the main candidate loop
    can check whether a token is *ever* capitalized mid-sentence, not just at the
    occurrence currently being filtered.
    """
    positions: dict[str, list[int]] = {}
    for match in _capitalized_token_matches(text):
        positions.setdefault(match.group(0), []).append(match.start())
    return positions


def _is_positional_case_noise(
    token: str, text: str, capitalized_positions: dict[str, list[int]]
) -> bool:
    """ADR-0033: suppress ``token`` when positional evidence holds -- every
    capitalized occurrence of ``token`` in ``text`` is at a sentence/quotation/
    heading/list-marker start (never mid-sentence) -- AND either of two signals
    confirms it isn't a real referent:

    (a) vocabulary evidence: its lowercase form appears as a standalone word
        elsewhere in ``text``; or
    (b) (issue #161) list-marker evidence: at least one occurrence sits at a
        *list/numbered-marker* start specifically (not a bare heading or
        unmarked paragraph start) -- the shape of an agentic system prompt's
        skill/tool list ("- Compact the conversation…"), where each item is a
        one-off command name that never recurs lowercase in the same hop.

    The positional gate is load-bearing either way: a token ever capitalized
    mid-sentence ("The lawyer said Mark signed the contract") fails it and
    stays a candidate regardless of vocabulary or list-marker evidence.
    """
    positions = capitalized_positions.get(token, [])
    if not all(_is_start_position(text, pos) for pos in positions):
        return False
    lowered = token.lower()
    if re.search(rf"\b{re.escape(lowered)}\b", text):
        return True
    return any(_is_list_marker_position(text, pos) for pos in positions)


def _is_start_position(text: str, pos: int) -> bool:
    return bool(_POSITION_START_RE.search(text[:pos]))


def _is_list_marker_position(text: str, pos: int) -> bool:
    return bool(_LIST_MARKER_START_RE.search(text[:pos]))


def _known_surfaces(entities: list[Entity]) -> frozenset[str]:
    surfaces: set[str] = set()
    for entity in entities:
        surfaces.add(entity.canonical)
        surfaces.update(entity.variations)
    return frozenset(surfaces)


def _context_window(text: str, start: int, end: int) -> tuple[str, int]:
    left = max(0, start - _CONTEXT_WINDOW)
    right = min(len(text), end + _CONTEXT_WINDOW)
    return text[left:right], start - left


def count_capitalized_tokens(text: str) -> int:
    """Count every raw capitalized-token occurrence in ``text``, before suppression.

    Issue #153 (processing trace per-hop detail, ADR-0035): the trace's "suppressed"
    count is (this raw count) - (candidates :meth:`L3Detector.detect` actually
    considered), so it must count every occurrence :func:`select_candidate_spans`
    would later filter (stopwords, known entities, declared tools, positional-case
    noise) — never the already-filtered candidate count.
    """
    return sum(1 for _ in _capitalized_token_matches(text))


class L3Detector:
    """Drive the L3 candidate-span seam: select → cache check → adjudicate.

    Holds a content cache across calls so the same chunk (same span in the same
    context) is adjudicated once per process — the cost-amortisation property
    ADR-0003 calls for ("content cache prevents re-scanning unchanged chunks
    across agent turns").
    """

    def __init__(
        self,
        adjudicator: L3Adjudicator,
        cache: L3ContentCache | None = None,
        deterministic_only: bool = False,
        allowlist: "Allowlist | None" = None,
        dismissal_log_path: str | None = None,
        progress_log_interval: int = _DEFAULT_PROGRESS_LOG_INTERVAL,
        provider_name: str = "ollama",
    ) -> None:
        self._adjudicator = adjudicator
        self._cache = cache if cache is not None else L3ContentCache()
        # Issue #153 (processing trace L3 column, ADR-0035): a label only, never
        # used to select behavior here -- "ollama" reproduces
        # config.DEFAULT_L3_PROVIDER for every existing caller that doesn't name one.
        self.provider_name = provider_name
        # ADR-0009: per-workspace opt-in to skip L3 entirely. Known-entity protection
        # via L1+L2 still runs; novelty discovery is the documented loss.
        self._deterministic_only = deterministic_only
        # ADR-0010 allowlist: rejected tokens are filtered before adjudication so
        # the learning loop's "reject" verdict actually suppresses re-detection.
        self._allowlist = allowlist
        # ADR-0032 / issue #133: opt-in local capture of dismissed candidates, to
        # curate the seeded allowlist. None (default/unset) is the exact today's
        # behavior -- no file created or written. Dedup is a small in-process set,
        # deliberately separate from the (text, context)-keyed content cache above:
        # the same token dismissed 200 times across one system prompt writes exactly
        # one line, not 200.
        self._dismissal_log_path = dismissal_log_path
        self._logged_dismissals: set[str] = set()
        # Issue #134: how many candidates between progress log lines (see detect()).
        self._progress_log_interval = progress_log_interval

    def detect(
        self,
        text: str,
        known_entities: list[Entity],
        declared_tools: frozenset[str] = frozenset(),
        phone_candidates_enabled: bool = True,
    ) -> list[tuple[CandidateSpan, L3Adjudication]]:
        if self._deterministic_only:
            return []
        results: list[tuple[CandidateSpan, L3Adjudication]] = []
        pass_started_at = time.monotonic()
        processed = 0

        def record(candidate: CandidateSpan, decision: L3Adjudication) -> None:
            nonlocal processed
            self._maybe_log_dismissal(candidate, decision)
            results.append((candidate, decision))
            processed += 1
            self._maybe_log_progress(processed, pass_started_at)

        # Issue #277: the phone-shaped producer (select_phone_candidate_spans) is
        # a separate, named pass -- deliberately not folded into
        # select_candidate_spans itself (its capitalized-token candidates and a
        # phone-shaped digit run have nothing in common) -- but still merges into
        # ONE ordered candidate list, in document order.
        #
        # Issue #279: ``phone_candidates_enabled`` is the audited per-workspace
        # opt-out -- its false positives are structurally opaque to a user with no
        # adjudicator wired (unlike a flagged capitalized token, which self-
        # explains). Narrower than ``deterministic_only``: it drops only the
        # phone-shaped producer's output from the merge, never
        # select_candidate_spans's.
        candidates = sorted(
            select_candidate_spans(text, known_entities, self._allowlist, declared_tools)
            + (select_phone_candidate_spans(text) if phone_candidates_enabled else []),
            key=lambda candidate: candidate.start,
        )

        # Issue #283 (ADR-0048 corollary 3): one candidate, one prompt, always --
        # no chunking, no adjudicate_batch seam. #260 measured a batched verdict
        # moving with the candidate's position and the batch's size, which
        # ADR-0048 designates as defective (the solo verdict is the reference
        # answer); the only conforming fix is to never batch at all.
        for candidate in candidates:
            cached = self._cache.get(candidate)
            if cached is not None:
                record(candidate, cached)
                continue
            decision = self._adjudicate_one(candidate)
            self._cache.put(candidate, decision)
            record(candidate, decision)
        return results

    def _adjudicate_one(self, candidate: CandidateSpan) -> L3Adjudication:
        try:
            return self._adjudicator.adjudicate(candidate)
        except Exception as exc:
            # Fail-closed (ADR-0009): a novel candidate we couldn't adjudicate
            # is exactly the case where letting the payload through would risk
            # leaking an undiscovered entity. Block.
            # SEC-7 (issue #48): the candidate is, by definition, unresolved —
            # it may be a real entity value never minted a surrogate. Reference
            # it by a hashed id (ADR-0009's scrub fallback), never the plaintext.
            digest = hashlib.sha256(candidate.text.encode("utf-8")).hexdigest()[:12]
            raise L3Unavailable(
                f"L3 adjudication failed for candidate (ref: hash:{digest}): {exc}"
            ) from exc

    def _maybe_log_progress(self, processed: int, pass_started_at: float) -> None:
        """Log forward progress every ``progress_log_interval`` candidates (issue #134).

        Fires mid-pass, not just on completion, so an operator tailing logs during a
        long run (the live-testing report: 250+ sequential candidates against a cold
        allowlist) sees a periodic signal that the request is still moving, without
        waiting for it to finish. Observability-only: never changes an adjudication
        result, cache entry, or dismissal-log write. Scrubbed by construction --
        candidate count and elapsed seconds only, never candidate text.
        """
        if processed % self._progress_log_interval != 0:
            return
        elapsed_s = time.monotonic() - pass_started_at
        logger.info(
            "l3_detect_progress: candidates_processed=%d elapsed_s=%.1f",
            processed,
            elapsed_s,
        )

    def _maybe_log_dismissal(
        self, candidate: CandidateSpan, decision: L3Adjudication
    ) -> None:
        """Append a dismissed candidate's bare token text to the dismissal log, the
        first time that exact token is dismissed in the process's lifetime (ADR-0032).

        Only ``candidate.text`` is ever written -- never ``candidate.context``: the
        curation rule (ADR-0023) is a property of the word itself, not the sentence
        it appeared in. Open-append immediately (not buffered) so a killed process
        doesn't lose the session's dismissal data.
        """
        if self._dismissal_log_path is None or decision.is_entity:
            return
        if candidate.text in self._logged_dismissals:
            return
        self._logged_dismissals.add(candidate.text)
        with open(self._dismissal_log_path, "a", encoding="utf-8") as handle:
            handle.write(candidate.text + "\n")
