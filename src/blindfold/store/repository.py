"""Entity-graph repository seam: the proxy's source of seeded (real -> surrogate) pairs.

The proxy depends on this seam (dependency-injected like ``get_mapping`` /
``get_upstream_client``) to build its ``SurrogateMapping`` from the entity graph instead
of a hardcoded dict. Two implementations share the ``seeded_pairs()`` seam:

- :class:`VendoredSeedRepository` — in-process, reads the vendored seed artifact, NO DB.
  Keeps the fast request-path test hermetic.
- the Postgres-backed repository (see :mod:`blindfold.store.postgres`) — reads the same
  graph after the ETL has loaded it.
"""

from __future__ import annotations

from typing import Any

from ._mint import mint_surrogate
from ._seed import load_vendored_seed


class VendoredSeedRepository:
    """In-process repository over the vendored seed artifact (no database)."""

    def __init__(self, seed: dict[str, Any]) -> None:
        self._seed = seed

    def seeded_pairs(self) -> list[tuple[str, str]]:
        """(real value -> stable surrogate) for every seeded referent.

        One canonical surrogate per referent (ADR-0007). Every variation (coreference,
        ADR-0004) is paired with that referent's surrogate, so detecting any alias
        restores to the same real value.
        """
        pairs: list[tuple[str, str]] = []
        for kind, key in (("person", "persons"), ("term", "terms")):
            for index, referent in enumerate(self._seed.get(key, [])):
                surrogate = mint_surrogate(kind, index)
                pairs.append((referent["canonical_name"], surrogate))
                for variation in referent.get("variations", []):
                    pairs.append((variation, surrogate))
        return pairs


def vendored_seed_repository() -> VendoredSeedRepository:
    return VendoredSeedRepository(load_vendored_seed())
