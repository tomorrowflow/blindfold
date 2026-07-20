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

from collections.abc import Iterable
from dataclasses import dataclass

from .l3 import L3Detector
from .policy import DEFAULT_WORKSPACE
from .review import ReviewInbox, ReviewItem
from .surrogates import SurrogateMapping


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
    transcripts: Iterable[str],
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
    """
    # Mining never mutates ``mapping``, so recover the entity-graph record list once
    # rather than per transcript: ``mapping.entities()`` regroups every seeded pair by
    # surrogate, and that result is identical for every transcript in the batch.
    known_entities = mapping.entities()
    proposed: list[ReviewItem] = []
    scanned = 0
    for transcript in transcripts:
        scanned += 1
        for candidate, decision in detector.detect(transcript, known_entities):
            if not decision.is_entity:
                continue
            proposed.append(
                inbox.upsert(
                    candidate.text,
                    candidate.context,
                    known_values=mapping.real_values(),
                    context_offset=candidate.context_offset,
                    entity_type=decision.entity_type,
                    workspace=workspace,
                )
            )
    return MiningReport(transcripts_scanned=scanned, proposed=proposed)
