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
from .review import ReviewInbox, ReviewItem
from .surrogates import SurrogateMapping


@dataclass(frozen=True)
class MiningReport:
    """Summary of one mining run, for CLI / SPA display.

    ``proposed`` is the list of inbox items that landed during this run (in the
    order they were proposed). Re-mining the same novel value reuses the existing
    inbox entry (E-stable) — the same ``ReviewItem`` may appear here once per run
    but its ``id`` and ``provisional_surrogate`` are stable across runs.
    """

    transcripts_scanned: int
    proposed: list[ReviewItem]


def mine_transcripts(
    transcripts: Iterable[str],
    detector: L3Detector,
    mapping: SurrogateMapping,
    inbox: ReviewInbox,
) -> MiningReport:
    """Scan ``transcripts`` and propose novel L3-confirmed entities to the inbox.

    Each transcript is run through the same candidate-span seam the live request
    path uses (selection pre-filters known entities and allowlist tokens, then L3
    adjudicates the leftovers). For every candidate L3 confirms, ``inbox.upsert``
    records the (real, provisional_surrogate, context) tuple — the same shape a
    live request would have produced.
    """
    proposed: list[ReviewItem] = []
    scanned = 0
    for transcript in transcripts:
        scanned += 1
        for candidate, decision in detector.detect(transcript, mapping.entities()):
            if not decision.is_entity:
                continue
            proposed.append(inbox.upsert(candidate.text, candidate.context))
    return MiningReport(transcripts_scanned=scanned, proposed=proposed)
