"""CLI entity-graph curation (issue #8).

A command-line tool to inspect and curate the entity graph without the SPA: add an
entity, list current mappings, register coreference variations / aliases, and edit a
surrogate (preserving restorability of past exchanges per ADR-0005). Cold-start
curation lives here; the novel-candidate triage / learning loop (ADR-0010) is a
separate later slice.

The CLI talks to a curation store seam so the implementation can be either an
in-process store (for hermetic CLI tests) or a Postgres-backed store (production /
integration tests). Both honour the same contract: any pair returned by
``seeded_pairs()`` is a (real -> surrogate) record the proxy will use to blindfold
outbound prompts and restore inbound responses.
"""

from __future__ import annotations

import argparse
import sys
from typing import Iterable, Protocol


class EntityGraphStore(Protocol):
    """Write-side seam over the curated entity graph."""

    def add_referent(self, kind: str, name: str) -> str:
        """Persist a new referent of ``kind`` (``person`` / ``term``); mint + return its
        stable surrogate. Idempotent: re-adding the same referent keeps the same
        surrogate (leak-audit clause E-stable)."""

    def add_variation(self, kind: str, name: str, value: str) -> None:
        """Record ``value`` as a coreference variation of the referent ``name``."""

    def edit_surrogate(self, kind: str, name: str, new_surrogate: str) -> None:
        """Replace the active surrogate for ``name`` with ``new_surrogate``, retiring the
        previous value as a historical alias so past exchanges still restore."""

    def seeded_pairs(self) -> list[tuple[str, str]]:
        """(real value -> surrogate) for every recorded mapping, current AND historical.

        Includes canonical names, every variation, and any retired surrogate left over
        from an edit. Historical surrogates still restore to the same real referent
        (ADR-0005: editing a surrogate preserves restorability of past exchanges)."""

    def list_mappings(self, kind: str | None = None) -> list[dict[str, str]]:
        """Snapshot of the active mappings, optionally filtered by ``kind``.

        Each row carries ``kind`` / ``name`` / ``surrogate`` so the CLI can print
        human-readable listings (re-identification — gated by audit per ADR-0007;
        in this slice plaintext columns mean no decrypt event yet)."""


class _Referent:
    __slots__ = ("kind", "name", "surrogate", "variations", "retired_surrogates")

    def __init__(self, kind: str, name: str, surrogate: str) -> None:
        self.kind = kind
        self.name = name
        self.surrogate = surrogate
        self.variations: list[str] = []
        self.retired_surrogates: list[str] = []


class MemoryEntityGraphStore:
    """In-process curation store. Used by the CLI test suite (no DB)."""

    _PERSON_POOL = (
        "Bernhard Vogt", "Claudia Reinhardt", "Dieter Sommer", "Elena Fuchs",
        "Stefan Kaiser", "Gabriele Wirth", "Heinz Lorenz", "Iris Hartmann",
    )
    _TERM_POOL = (
        "Projekt Polarstern", "Vorgang Silberpfeil", "Initiative Tannwald",
        "Vorhaben Eichberg", "Programm Nordlicht", "Projekt Steinadler",
    )

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], _Referent] = {}

    def _mint(self, kind: str) -> str:
        pool = self._PERSON_POOL if kind == "person" else self._TERM_POOL
        index = sum(1 for k in self._by_key if k[0] == kind)
        if index < len(pool):
            return pool[index]
        return f"{kind.title()} Surrogate {index}"

    def add_referent(self, kind: str, name: str) -> str:
        key = (kind, name)
        existing = self._by_key.get(key)
        if existing is not None:
            return existing.surrogate
        surrogate = self._mint(kind)
        self._by_key[key] = _Referent(kind, name, surrogate)
        return surrogate

    def add_variation(self, kind: str, name: str, value: str) -> None:
        ref = self._by_key.get((kind, name))
        if ref is None:
            raise KeyError(f"unknown {kind} referent: {name!r}")
        if value not in ref.variations:
            ref.variations.append(value)

    def edit_surrogate(self, kind: str, name: str, new_surrogate: str) -> None:
        ref = self._by_key.get((kind, name))
        if ref is None:
            raise KeyError(f"unknown {kind} referent: {name!r}")
        if new_surrogate == ref.surrogate:
            return
        ref.retired_surrogates.append(ref.surrogate)
        ref.surrogate = new_surrogate

    def seeded_pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for ref in self._by_key.values():
            # Retired surrogates are emitted FIRST so a downstream SurrogateMapping
            # (last-write-wins per real value) ends up with the ACTIVE surrogate as
            # the one used to blindfold subsequent exchanges. The (real, retired)
            # pairs remain in the seam so past exchanges holding the old surrogate
            # are still re-identifiable at the store level (ADR-0005).
            for retired in ref.retired_surrogates:
                pairs.append((ref.name, retired))
            pairs.append((ref.name, ref.surrogate))
            for variation in ref.variations:
                pairs.append((variation, ref.surrogate))
        return pairs

    def list_mappings(self, kind: str | None = None) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for ref in self._by_key.values():
            if kind is not None and ref.kind != kind:
                continue
            rows.append({"kind": ref.kind, "name": ref.name, "surrogate": ref.surrogate})
        return rows


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blindfold-entitygraph",
        description="Curate the Blindfold entity graph from the command line (issue #8).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Add a person or term referent.")
    add.add_argument("--kind", required=True, choices=("person", "term"))
    add.add_argument("--name", required=True)
    add.add_argument(
        "--variation",
        action="append",
        default=[],
        help="Optional coreference variation; repeat for multiple aliases.",
    )

    list_cmd = sub.add_parser("list", help="List the active real -> surrogate mappings.")
    list_cmd.add_argument(
        "--kind",
        choices=("person", "term"),
        help="Restrict the listing to one kind of referent.",
    )

    variation = sub.add_parser(
        "variation",
        help="Register a coreference variation (alias) for an existing referent.",
    )
    variation.add_argument("--kind", required=True, choices=("person", "term"))
    variation.add_argument("--name", required=True)
    variation.add_argument("--value", required=True)

    edit = sub.add_parser(
        "edit-surrogate",
        help="Change a referent's active surrogate; retired surrogates remain "
             "restorable so past exchanges keep resolving (ADR-0005).",
    )
    edit.add_argument("--kind", required=True, choices=("person", "term"))
    edit.add_argument("--name", required=True)
    edit.add_argument("--to", required=True, help="The new surrogate value.")

    return parser


def run(argv: Iterable[str], *, store: EntityGraphStore) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv))

    if args.command == "add":
        surrogate = store.add_referent(args.kind, args.name)
        for variation in args.variation:
            store.add_variation(args.kind, args.name, variation)
        print(f"added {args.kind} {args.name!r} -> surrogate {surrogate!r}")
        return 0

    if args.command == "list":
        for row in store.list_mappings(kind=args.kind):
            print(f"{row['kind']}\t{row['name']}\t-> {row['surrogate']}")
        return 0

    if args.command == "variation":
        store.add_variation(args.kind, args.name, args.value)
        print(f"registered variation {args.value!r} for {args.kind} {args.name!r}")
        return 0

    if args.command == "edit-surrogate":
        store.edit_surrogate(args.kind, args.name, args.to)
        print(
            f"edited {args.kind} {args.name!r}: active surrogate is now {args.to!r}; "
            "the previous surrogate is retained for restoring past exchanges."
        )
        return 0

    return 2


def main() -> int:  # pragma: no cover - entry point glue
    raise SystemExit(
        "blindfold-entitygraph requires a curation store; "
        "the production wiring (Postgres + DSN) lands in a follow-up slice."
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
