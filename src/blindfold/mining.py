"""Historical-transcript mining → review inbox (ADR-0010, slice of #1).

Optional, out-of-band job: walk historical transcripts, reuse the L3 candidate-
span seam (ADR-0003) over each one, and route confirmed novel candidates to the
shared :class:`~blindfold.review.ReviewInbox`. From there the *existing* learning
loop handles them — confirm grows the entity graph; reject grows the allowlist —
so mined proposals are indistinguishable from proposals born of live requests.

Mining is **not** on the proxy hot path. It takes its own detector + inbox, never
touches the FastAPI app, and never egresses bytes. There is no upstream, no
restore, no streaming. The point is to grow the graph from past material so the
deterministic L1+L2 passes catch those entities the next time they appear live.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .engine import (
    augmented_known_values,
    is_contained_response_block,
    non_hop_keys_for_block_type,
)
from .l3 import L3Detector
from .policy import DEFAULT_WORKSPACE
from .review import ReviewInbox, ReviewItem
from .surrogates import SurrogateMapping

# A transcript is either a bare prose string (today's shape -- no role/block
# structure, so the structural contained-response rule below never applies to
# it) or a Messages-API-shaped ``messages`` list (the same shape
# ``blindfold_payload`` consumes), letting the structural rule recognise a
# mined provider result block exactly as the live request path does.
Transcript = str | Sequence[Mapping[str, Any]]


def _block_leaves(
    block: Any, hop_role: str | None, contained: bool = False
) -> Iterable[tuple[str, bool]]:
    """Yield ``(text, contained_response)`` for every string leaf of one
    content block -- mining's own read-only counterpart to the live blind
    pass's block walk (:func:`~blindfold.engine._blindfold_block`), for
    extracting scannable text rather than rewriting it in place. Protocol
    fields (:func:`~blindfold.engine.non_hop_keys_for_block_type`) are
    skipped -- the SAME closed set :func:`~blindfold.engine._blindfold_block`
    itself never treats as prose, not a second copy.

    ``contained`` is the enclosing block's own already-decided verdict,
    OR-combined with this block's own (:func:`~blindfold.engine.is_contained_response_block`
    against ``hop_role`` and THIS block's own ``type``) and carried into
    every nested block the same way :func:`~blindfold.engine._blindfold_block`
    carries it into every nested string leaf: a provider result block's own
    nested shape (e.g. a ``web_search_tool_result``'s nested
    ``web_search_result`` items) has its OWN block type, which would never
    itself match the structural rule -- the nested block is contained
    because its ENCLOSING block is, not because of its own type.
    """
    if not isinstance(block, dict):
        return
    block_type = block.get("type")
    contained = contained or is_contained_response_block(hop_role, block_type)
    non_hop_keys = non_hop_keys_for_block_type(block_type)
    for key, value in block.items():
        if key in non_hop_keys:
            continue
        if isinstance(value, str):
            yield value, contained
        elif isinstance(value, list):
            for item in value:
                yield from _block_leaves(item, hop_role, contained)


def _transcript_leaves(transcript: Transcript) -> Iterable[tuple[str, bool]]:
    """Yield ``(text, contained_response)`` for every scannable string in
    ``transcript`` -- a bare string yields itself once, never contained (the
    structural rule can never apply: there is no role/block to recognise it
    by). A structured ``messages`` list yields one leaf per block field (a
    text block's own ``text``, a tool-result's ``content``, a search
    result's ``title``/``url``, ...), each already resolved against the
    message's own role and the block's own type (see :func:`_block_leaves`).
    """
    if isinstance(transcript, str):
        yield transcript, False
        return
    for message in transcript:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, str):
            yield content, False
        elif isinstance(content, list):
            for block in content:
                yield from _block_leaves(block, role)


def _transcript_corpus_text(transcript: Transcript) -> str:
    """The whole transcript's own text, flattened -- issue #292/#337's
    pool-vs-corpus disjointness needs the FULL transcript, not just one
    leaf's local text, mirroring the engine's own per-hop ``corpus_text``
    (there, one hop; here, one transcript entry).
    """
    if isinstance(transcript, str):
        return transcript
    return "\n".join(text for text, _ in _transcript_leaves(transcript))


@dataclass(frozen=True)
class MiningReport:
    """Summary of one mining run, for CLI / SPA display.

    ``proposed`` lists each L3-confirmed candidate appearance in order, so a
    novel value found in multiple transcripts shows up multiple times here —
    but every appearance points to the **same** ``ReviewItem`` (same ``id`` and
    ``provisional_surrogate``), because ``ReviewInbox.upsert`` reuses entries
    by ``real`` (clause E-stable). The inbox itself therefore holds at most one
    row per novel value, however many times mining encounters it.
    """

    transcripts_scanned: int
    proposed: list[ReviewItem]


def mine_transcripts(
    transcripts: Iterable[Transcript],
    detector: L3Detector,
    mapping: SurrogateMapping,
    inbox: ReviewInbox,
    workspace: str = DEFAULT_WORKSPACE,
) -> MiningReport:
    """Scan ``transcripts`` and propose novel L3-confirmed entities to the inbox.

    Each transcript is run through the same candidate-span seam the live request
    path uses (selection pre-filters known entities and allowlist tokens, then L3
    adjudicates the leftovers). For every candidate L3 confirms, ``inbox.upsert``
    records the (real, provisional_surrogate, context) tuple — the same shape a
    live request would have produced.

    Mining runs out-of-band, with no request in context (issue #171) — ``workspace``
    defaults to the default workspace slug so a proposed candidate still lands
    somewhere confirm can grow, rather than dropping the field.

    ADR-0060 amendment 2026-10-05, decision point 11 (issue #448): a candidate
    recognised as coming from a contained response (a provider result block
    echoed back in assistant role, :func:`~blindfold.engine.is_contained_response_block`
    -- the SAME predicate the live request path calls, not a copy) is exempt
    from novelty minting here too. Only a *structured* transcript (a Messages-
    shaped ``messages`` list) carries the role/block-type information the
    structural rule needs; a bare prose string has none, so the rule never
    exempts anything from one -- "where the memory is unreachable (offline
    mining), only the structural rule applies" (point 11), and even that much
    needs the structure to be present at all.
    """
    # Mining never mutates ``mapping``, so recover the entity-graph record list once
    # rather than per transcript: ``mapping.entities()`` regroups every seeded pair by
    # surrogate, and that result is identical for every transcript in the batch.
    known_entities = mapping.entities()
    proposed: list[ReviewItem] = []
    scanned = 0
    for transcript in transcripts:
        scanned += 1
        corpus_text = _transcript_corpus_text(transcript)
        for text, contained_response in _transcript_leaves(transcript):
            for candidate, decision in detector.detect(text, known_entities):
                if not decision.is_entity:
                    continue
                if contained_response:
                    # Point 2: every word here is provider-originated -- never
                    # added to the review inbox, left unmentioned entirely
                    # (mirrors the engine's own skip at its mint call site).
                    continue
                item = inbox.upsert(
                    candidate.text,
                    candidate.context,
                    known_values=augmented_known_values(mapping, inbox),
                    context_offset=candidate.context_offset,
                    entity_type=decision.entity_type,
                    workspace=workspace,
                    adjudicator=decision.adjudicator,
                    suppression_trace=candidate.suppression_trace,
                    # Issue #292/#337: pool-vs-corpus disjointness, mirroring the
                    # engine's own mint call site -- the collision that matters can
                    # be anywhere in this transcript, not just this candidate's own
                    # local context window.
                    corpus_text=corpus_text,
                )
                if item is None:
                    # ADR-0052 (issue #330): the candidate matches the opaque
                    # reserved-namespace fallback shape -- the mint is refused,
                    # nothing to propose.
                    continue
                proposed.append(item)
    return MiningReport(transcripts_scanned=scanned, proposed=proposed)
