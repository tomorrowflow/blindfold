"""L3 candidate-span adjudication (ADR-0003).

L3 is invoked **only on flagged candidate spans plus minimal context** — never on the
full payload. The deterministic passes (L1+L2) have already protected known entities;
L3's job is to adjudicate the leftovers: unknown capitalized tokens, fuzzy near-misses,
ambiguous first names. Cost scales with the number of candidate spans, not payload
size — which is what makes the proxy tractable on large code bodies.

The adjudicator itself (Ollama) is a network-boundary seam: production wires a real
local-LLM client; tests substitute a recording stub. This module owns candidate-span
*selection* and *context-windowing*; the adjudicator owns the LLM call. A content
cache (keyed by ``(span_text, context)``) prevents re-scanning unchanged chunks across
agent turns.
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field
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

_CAPITALIZED_RE = re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]+\b")

_STOPWORDS_PATH = Path(__file__).with_name("l3_stopwords_en_de.txt")


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
    """

    text: str
    start: int
    end: int
    context: str


@dataclass(frozen=True)
class L3Adjudication:
    """L3's verdict for a candidate span.

    ``is_entity`` is the load-bearing flag: the engine mints a surrogate for
    confirmed entities, ignores rejections.
    """

    is_entity: bool


class L3Adjudicator(Protocol):
    """The network-boundary seam for the local LLM (Ollama).

    Production wires a real Ollama HTTP client behind this protocol; tests
    substitute a recording stub. Either way, the engine only depends on this
    one-method interface — call cost is the test's measure of L3 cost.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication: ...


_DEFAULT_CACHE_MAX_ENTRIES = 4096


@dataclass
class L3ContentCache:
    """Cache adjudications keyed by ``(span_text, context)`` so unchanged chunks of
    text — same span, same surroundings — aren't re-scanned across agent turns
    (ADR-0003). The key is the span + its minimal context, not the whole payload:
    a candidate in identical context produces an identical decision.

    Keys hold real, un-blindfolded candidate text, so this is an in-memory
    real-value store (ADR-0022) — bounded by ``max_entries`` with least-recently-used
    eviction, so a long-running process's memory stays bounded regardless of how many
    distinct candidates it has ever seen. Never persisted to disk.
    """

    max_entries: int = _DEFAULT_CACHE_MAX_ENTRIES
    _entries: "OrderedDict[tuple[str, str], L3Adjudication]" = field(
        default_factory=OrderedDict
    )

    def get(self, candidate: CandidateSpan) -> L3Adjudication | None:
        key = (candidate.text, candidate.context)
        if key not in self._entries:
            return None
        self._entries.move_to_end(key)
        return self._entries[key]

    def put(
        self, candidate: CandidateSpan, decision: L3Adjudication
    ) -> None:
        key = (candidate.text, candidate.context)
        self._entries[key] = decision
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
    """
    known_surfaces = _known_surfaces(known_entities)
    candidates: list[CandidateSpan] = []
    for match in _CAPITALIZED_RE.finditer(text):
        token = match.group(0)
        if token in _SENTENCE_STOPWORDS:
            continue
        if token in known_surfaces:
            continue
        if allowlist is not None and allowlist.contains(token):
            continue
        if token in declared_tools:
            continue
        start, end = match.start(), match.end()
        context = _context_window(text, start, end)
        candidates.append(
            CandidateSpan(text=token, start=start, end=end, context=context)
        )
    return candidates


def _known_surfaces(entities: list[Entity]) -> frozenset[str]:
    surfaces: set[str] = set()
    for entity in entities:
        surfaces.add(entity.canonical)
        surfaces.update(entity.variations)
    return frozenset(surfaces)


def _context_window(text: str, start: int, end: int) -> str:
    left = max(0, start - _CONTEXT_WINDOW)
    right = min(len(text), end + _CONTEXT_WINDOW)
    return text[left:right]


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
    ) -> None:
        self._adjudicator = adjudicator
        self._cache = cache if cache is not None else L3ContentCache()
        # ADR-0009: per-workspace opt-in to skip L3 entirely. Known-entity protection
        # via L1+L2 still runs; novelty discovery is the documented loss.
        self._deterministic_only = deterministic_only
        # ADR-0010 allowlist: rejected tokens are filtered before adjudication so
        # the learning loop's "reject" verdict actually suppresses re-detection.
        self._allowlist = allowlist

    def detect(
        self,
        text: str,
        known_entities: list[Entity],
        declared_tools: frozenset[str] = frozenset(),
    ) -> list[tuple[CandidateSpan, L3Adjudication]]:
        if self._deterministic_only:
            return []
        results: list[tuple[CandidateSpan, L3Adjudication]] = []
        for candidate in select_candidate_spans(
            text, known_entities, self._allowlist, declared_tools
        ):
            cached = self._cache.get(candidate)
            if cached is not None:
                results.append((candidate, cached))
                continue
            try:
                decision = self._adjudicator.adjudicate(candidate)
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
            self._cache.put(candidate, decision)
            results.append((candidate, decision))
        return results
