"""Exchange capture comparison: severity ladder, derived classification (ADR-0047 §9,
issue #256, Grill outcome #247).

The comparable set is **which real values were replaced**, never surrogate identity:
surrogate identity is process-dependent for anything not already in the entity graph
(a novel entity enters the graph only on human confirm, and ``mint_pii`` allocates from
in-process counters), so comparing surrogates directly would manufacture noise on
nearly every exchange (ADR-0047 §9, "Comparing surrogates ... rejected").

Concretely this compares the **set of surrogates actually used**, per section:

- ``observed`` -- the footer's witnessed ``injected`` pair table (surrogate -> real),
  built during the live run.
- ``reconstructed`` -- every surrogate replay's own detection recorded, aggregated
  from the ``surrogates`` field of every ``DetectionRecord`` replay appends
  (section=reconstructed) -- the same shared-vocabulary field ``observed``
  ``DetectionRecord``s would carry, deliberately not ``offsets``/``pass_name``,
  which attribute detail rather than membership.

Classification is derived, never a curated list, from two structural facts (never a
hand-maintained exception list, per this issue's own acceptance criteria):

1. Is the real value in the entity graph? If yes its surrogate is stable by
   construction (ADR-0007) -- any divergence is a ``defect``. If no, it is novel and
   divergence is ``expected``.
2. Does the surrogate have reserved-namespace shape
   (:func:`blindfold.surrogates.is_reserved_namespace_surrogate`)? That identifies an
   L1 PII mint from the string alone, so a divergence there is ``expected`` (PII
   counter-position -- ``mint_pii``'s per-kind counter is in-process and differs
   between the live run and replay), even though PII was never part of the curated
   entity graph either.

A surrogate this cannot attribute to either fact is ``unknown`` -- the tool admitting
it cannot classify rather than guessing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from blindfold.detection import Entity
from blindfold.surrogates import is_reserved_namespace_surrogate

from .capture import SECTION_RECONSTRUCTED, DetectionRecord, FooterRecord

SEVERITY_DEFECT = "defect"
SEVERITY_EXPECTED = "expected"
SEVERITY_UNKNOWN = "unknown"

_REASONS = {
    SEVERITY_DEFECT: "graph-known entity: surrogate must be stable across observed and reconstructed",
    SEVERITY_EXPECTED: "novel/unconfirmed referent or PII counter-position: divergence is expected",
    SEVERITY_UNKNOWN: "cannot attribute this surrogate to the entity graph or a reserved-namespace mint",
}


@dataclass(frozen=True)
class Divergence:
    """One surrogate detected on exactly one side of the observed/reconstructed
    comparison. ``ref`` is the surrogate itself -- never the real value, whether or
    not this divergence could be attributed to a real value at all."""

    severity: str
    ref: str
    reason: str


def _observed_injected(records: Iterable) -> dict[str, str]:
    footer = next((r for r in records if isinstance(r, FooterRecord)), None)
    return dict(footer.injected) if footer is not None else {}


def _reconstructed_surrogates(records: Iterable) -> frozenset[str]:
    surrogates: set[str] = set()
    for record in records:
        if isinstance(record, DetectionRecord) and record.section == SECTION_RECONSTRUCTED:
            surrogates.update(record.surrogates)
    return frozenset(surrogates)


def compare(
    records: Iterable, *, graph_entities: Iterable[Entity]
) -> tuple[Divergence, ...]:
    """Compare an Exchange capture's observed and reconstructed sections and
    classify every divergent surrogate on the severity ladder (``defect`` >
    ``expected`` > ``unknown`` -- ``leak`` is the offline leak check's own,
    higher-ranked classification; see ``leak_check.py``).
    """
    entities = list(graph_entities)
    graph_surrogate_to_real = {entity.surrogate: entity.canonical for entity in entities}
    graph_reals = {entity.canonical for entity in entities} | {
        variation for entity in entities for variation in entity.variations
    }

    observed = _observed_injected(records)
    observed_surrogates = frozenset(observed)
    reconstructed_surrogates = _reconstructed_surrogates(records)

    divergent = observed_surrogates.symmetric_difference(reconstructed_surrogates)

    divergences = []
    for surrogate in sorted(divergent):
        real = observed.get(surrogate) or graph_surrogate_to_real.get(surrogate)
        if real is not None:
            severity = SEVERITY_DEFECT if real in graph_reals else SEVERITY_EXPECTED
        elif is_reserved_namespace_surrogate(surrogate):
            severity = SEVERITY_EXPECTED
        else:
            severity = SEVERITY_UNKNOWN
        divergences.append(
            Divergence(severity=severity, ref=surrogate, reason=_REASONS[severity])
        )
    return tuple(divergences)
