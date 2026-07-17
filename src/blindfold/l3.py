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
import logging
import re
import time
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
    A fourth suppression condition — the ADR-0033 positional case heuristic —
    runs after the three above: a token is suppressed when it has both
    vocabulary evidence (its lowercase form appears as a standalone word
    elsewhere in this hop) and positional evidence (it is never capitalized
    mid-sentence in this hop — only at a sentence, quotation, or heading
    start). The AND is load-bearing: vocabulary evidence alone would eat real
    names ("mark this as done" would suppress "Mark" the person too); the
    positional gate protects any token that is ever capitalized mid-sentence.
    """
    known_surfaces = _known_surfaces(known_entities)
    capitalized_positions = _capitalized_positions(text)
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
        if _is_positional_case_noise(token, text, capitalized_positions):
            continue
        start, end = match.start(), match.end()
        context = _context_window(text, start, end)
        candidates.append(
            CandidateSpan(text=token, start=start, end=end, context=context)
        )
    return candidates


def _capitalized_positions(text: str) -> dict[str, list[int]]:
    """Pre-scan: map each exact capitalized token to every start offset where it
    appears in ``text`` (ADR-0033). Built once per hop so the main candidate loop
    can check whether a token is *ever* capitalized mid-sentence, not just at the
    occurrence currently being filtered.
    """
    positions: dict[str, list[int]] = {}
    for match in _CAPITALIZED_RE.finditer(text):
        positions.setdefault(match.group(0), []).append(match.start())
    return positions


def _is_positional_case_noise(
    token: str, text: str, capitalized_positions: dict[str, list[int]]
) -> bool:
    """ADR-0033: suppress ``token`` only when both hold — (a) vocabulary evidence,
    its lowercase form appears as a standalone word elsewhere in ``text``, and
    (b) positional evidence, every capitalized occurrence of ``token`` in ``text``
    is at a sentence/quotation/heading start (never mid-sentence).
    """
    lowered = token.lower()
    if not re.search(rf"\b{re.escape(lowered)}\b", text):
        return False
    return all(
        _is_start_position(text, pos)
        for pos in capitalized_positions.get(token, [])
    )


def _is_start_position(text: str, pos: int) -> bool:
    return bool(_POSITION_START_RE.search(text[:pos]))


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
        dismissal_log_path: str | None = None,
        progress_log_interval: int = _DEFAULT_PROGRESS_LOG_INTERVAL,
    ) -> None:
        self._adjudicator = adjudicator
        self._cache = cache if cache is not None else L3ContentCache()
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
    ) -> list[tuple[CandidateSpan, L3Adjudication]]:
        if self._deterministic_only:
            return []
        results: list[tuple[CandidateSpan, L3Adjudication]] = []
        pass_started_at = time.monotonic()
        processed = 0
        for candidate in select_candidate_spans(
            text, known_entities, self._allowlist, declared_tools
        ):
            cached = self._cache.get(candidate)
            if cached is not None:
                decision = cached
            else:
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
            self._maybe_log_dismissal(candidate, decision)
            results.append((candidate, decision))
            processed += 1
            self._maybe_log_progress(processed, pass_started_at)
        return results

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
