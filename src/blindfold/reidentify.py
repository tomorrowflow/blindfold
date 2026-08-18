"""Re-identification store seam (ADR-0015 / issue #10).

Provides the (surrogate, workspace) → ciphertext lookup that backs the
re-identify endpoint. Two implementations share the seam:

- :class:`InMemoryReIdentificationStore` — in-process dict, used in tests and as the
  process-lifetime fallback when no database is configured.
- :class:`~blindfold.store.reidentify_store.PostgresReIdentificationStore` (issue
  #105) — persists the mapping so a surrogate minted before a restart still resolves
  afterward. ``app.py``'s ``get_reidentify_store()`` chooses between the two based on
  ``BLINDFOLD_DATABASE_URL``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ReIdentificationStore(Protocol):
    """Look up the ciphertext for a (surrogate, workspace) pair."""

    async def surrogate_to_ciphertext(self, surrogate: str, workspace: str) -> str | None:
        """Return the ciphertext for ``surrogate`` scoped to ``workspace``, or None."""
        ...

    def seed(self, surrogate: str, workspace: str, ciphertext: str) -> None:
        """Upsert (surrogate, workspace) -> ciphertext (issue #172).

        Both concrete implementations already expose this as an idempotent
        upsert (``ON CONFLICT ... DO UPDATE`` on the Postgres side); declaring
        it on the Protocol makes the write side of the seam honest and lets a
        recording double stand in for it in unit tests.
        """
        ...

    def all_entries(self) -> list[tuple[str, str, str]]:
        """Return every (surrogate, workspace, ciphertext) triple (issue #343).

        The read side of the startup-rehydration seam: declared here for the
        same reason as ``seed`` -- both concrete implementations already expose
        it, and :func:`blindfold.app.hydrate_mapping_from_reidentify_store`
        consults it through this Protocol to reseed the request path's
        ``SurrogateMapping`` so a confirmed entity's surrogate is stable across
        a process restart.
        """
        ...


class InMemoryReIdentificationStore:
    """In-process store backed by a pre-seeded dict of (surrogate, workspace) → ciphertext.

    Entries are scoped by workspace: a surrogate from workspace A is NOT visible when
    queried with workspace B (ADR-0015 workspace-scoped re-identification).
    A multi-workspace referent has one entry per workspace it is tagged to.
    """

    def __init__(self, entries: dict[tuple[str, str], str] | None = None) -> None:
        self._entries: dict[tuple[str, str], str] = entries or {}

    def seed(self, surrogate: str, workspace: str, ciphertext: str) -> None:
        self._entries[(surrogate, workspace)] = ciphertext

    async def surrogate_to_ciphertext(self, surrogate: str, workspace: str) -> str | None:
        return self._entries.get((surrogate, workspace))

    def all_entries(self) -> list[tuple[str, str, str]]:
        """Return every (surrogate, workspace, ciphertext) triple (issue #343):
        the source :func:`blindfold.app.hydrate_mapping_from_reidentify_store`
        reads to rehydrate the request path's ``SurrogateMapping`` at startup."""
        return [
            (surrogate, workspace, ciphertext)
            for (surrogate, workspace), ciphertext in self._entries.items()
        ]
