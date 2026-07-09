"""Surrogate mapping: the real <-> surrogate registry.

A surrogate is the fake stand-in assigned to an entity. Surrogates are *stable*
(a given entity maps to the same surrogate everywhere) and minting is *idempotent*
(minting an entity that already has a surrogate returns the existing one).

For contactable PII (emails, phones, IBANs, IDs) minting draws from **reserved /
non-routable namespaces** (ADR-0005, leak-audit clause E) — `.invalid` / `.example`
domains, NANPA `555-01XX` fictional range, the unassigned ISO 3166 country code for
IBANs, an explicit ``RESERVED`` ID prefix — so Blindfold never generates a routable
lookalike of a real third party's contact value.

This slice keeps the mapping in-memory and in plaintext on purpose: persistence and
Transit-backed mapping secrecy (leak-audit clause G) are out of scope (issues #3/#10).
"""

from __future__ import annotations

from collections.abc import Iterable

from .detection import Entity
from .store._mint import next_replacement_surrogate


def _mint_pii_surrogate(kind: str, index: int) -> str:
    """Return the ``index``-th reserved-namespace surrogate for a PII ``kind``.

    Reservations (deliberately non-routable):
      - ``email`` -> RFC 2606 ``.invalid`` TLD (never deliverable).
      - ``phone`` -> NANPA fictional range ``+1-555-0100..0199`` (reserved for fiction).
      - ``iban``  -> ISO 3166 unassigned country code ``XX`` (no real bank routes it).
      - ``id``    -> explicit ``ID-RESERVED-XXXX`` prefix (clearly synthetic).
    """
    if kind == "email":
        return f"pii-user-{index:04d}@blindfold.invalid"
    if kind == "phone":
        # 100..199 keeps the surrogate inside the NANPA fictional `555-01XX` range
        # (index wraps deterministically past 100 fakes — collisions inside one
        # exchange are vanishingly unlikely and the closed-world session still
        # records the (surrogate -> real) pair for the exchange).
        return f"+1-555-{100 + (index % 100):04d}"
    if kind == "iban":
        # `XX` is unassigned in ISO 3166-1; `99` keeps the format-valid 2+2 prefix
        # without colliding with any real country IBAN range.
        return f"XX99 0000 0000 0000 0000 {index:04d}"
    if kind == "id":
        return f"ID-RESERVED-{index:06d}"
    return f"PII-RESERVED-{kind}-{index:04d}"


class SurrogateMapping:
    """In-memory registry of real -> surrogate assignments."""

    def __init__(self) -> None:
        self._by_real: dict[str, str] = {}
        self._known_surrogates: set[str] = set()
        self._pii_counters: dict[str, int] = {}
        # Learn-time disjointness (issue #81): cursor into store._mint's
        # replacement pool, shared across every seed() call on this mapping so a
        # skipped/assigned entry is never reused for a later retirement.
        self._replacement_pool_position: int = 0

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[str, str]]) -> "SurrogateMapping":
        """Build a mapping from (real -> surrogate) pairs supplied by the entity-graph
        repository seam (replaces the retired hardcoded ``_SEED`` dict)."""
        mapping = cls()
        for real, surrogate in pairs:
            mapping.seed(real, surrogate)
        return mapping

    def seed(self, real: str, surrogate: str) -> list[tuple[str, str, str]]:
        """Seed a real -> surrogate pair; retire any active surrogate ``real``
        newly invalidates (issue #81, learn-time disjointness).

        When ``real`` is a value this mapping has never seen before (a newly
        learned entity or Variation, via the learning loop's confirm or a
        curation edit such as merge), any *other* referent's currently active
        surrogate that contains ``real`` as a substring is now stale -- the
        same closed-world set the pre-egress leak gate consults via
        :meth:`real_values` would flag it the next time that surrogate is
        injected. Such a surrogate is retired (kept recognized, never reused)
        and the affected referent is re-minted a disjoint replacement.

        Returns the list of ``(affected_real, retired_surrogate, replacement)``
        triples so callers/tests can observe what was invalidated; empty when
        ``real`` isn't new or invalidates nothing.
        """
        is_new_real = real not in self._by_real
        self._by_real[real] = surrogate
        self._known_surrogates.add(surrogate)

        invalidated: list[tuple[str, str, str]] = []
        if not is_new_real or not real:
            return invalidated

        for other_real, other_surrogate in list(self._by_real.items()):
            if other_real == real or real not in other_surrogate:
                continue
            replacement, self._replacement_pool_position = next_replacement_surrogate(
                self._replacement_pool_position, self._by_real.keys()
            )
            self._known_surrogates.add(other_surrogate)  # retire: stay recognized
            self._by_real[other_real] = replacement
            self._known_surrogates.add(replacement)
            invalidated.append((other_real, other_surrogate, replacement))
        return invalidated

    def mint_pii(self, kind: str, value: str) -> str:
        """Return a stable reserved-namespace surrogate for L1-detected PII.

        Stable: the same ``value`` always returns the same surrogate within this
        mapping (idempotent). The surrogate is drawn from a reserved namespace per
        :func:`_mint_pii_surrogate` so Blindfold never generates a routable lookalike
        of a real third party's contact value (leak-audit clause E reserved-namespace).
        """
        if value not in self._by_real:
            index = self._pii_counters.get(kind, 0)
            self._pii_counters[kind] = index + 1
            surrogate = _mint_pii_surrogate(kind, index)
            self._by_real[value] = surrogate
            self._known_surrogates.add(surrogate)
        return self._by_real[value]

    def surrogate_for(self, real: str) -> str | None:
        return self._by_real.get(real)

    def is_known_surrogate(self, value: str) -> bool:
        """True if ``value`` is itself a surrogate this mapping has already issued.

        Used by the engine to skip L1 spans whose value is one of L1's own
        (PII-shaped) surrogates from a prior hop — re-blindfolding them would mint a
        second surrogate for the same real entity and break clause E-stable.
        """
        return value in self._known_surrogates

    def known_surrogates(self) -> frozenset[str]:
        """Every surrogate this mapping has issued (seeded + PII-minted).

        Exposed for the L3 candidate guard (ADR-0022, issue #68): a surrogate
        already minted for one hop must never be handed to L3 as a fresh novel
        candidate on a later hop.
        """
        return frozenset(self._known_surrogates)

    def retire_surrogate(self, surrogate: str) -> None:
        """Keep a retired surrogate recognized so it is not re-blindfolded if encountered.

        After a merge, the loser's old surrogate is retired: it will never be the active
        surrogate for a future blindfold pass, but it must stay in _known_surrogates so
        the engine does not attempt to re-blindfold it if seen in an outbound prompt
        (e.g. carried over from a past exchange).
        """
        self._known_surrogates.add(surrogate)

    def real_values(self) -> list[str]:
        return list(self._by_real.keys())

    def entities(self) -> list[Entity]:
        """Group seeded real values by surrogate to recover entity-graph records.

        Same surrogate ⇒ same referent (ADR-0007). The first real value seeded for a
        given surrogate is taken as the **canonical** form; the rest are variations
        (coreference, ADR-0004). Dict insertion order is preserved (Python 3.7+), so
        the order seeded via :py:meth:`from_pairs` is the order recovered here.
        """
        by_surrogate: dict[str, list[str]] = {}
        for real, surrogate in self._by_real.items():
            by_surrogate.setdefault(surrogate, []).append(real)
        return [
            Entity(canonical=reals[0], variations=tuple(reals[1:]), surrogate=surrogate)
            for surrogate, reals in by_surrogate.items()
        ]
