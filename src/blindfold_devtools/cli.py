"""``blindfold captures`` / ``blindfold explain`` / ``blindfold serve`` -- the
Diagnostic session's own CLI (ADR-0047 §6/§7, issues #257/#271).

Source-run only, like every devtools entry point: there is no ``[project.scripts]``
wiring here, since that would install a console script pointing at a module absent
from the release wheel (ADR-0047 §2). Run as ``python -m blindfold_devtools captures``
/ ``... explain --last`` / ``... serve`` from a source checkout with the ``devtools``
dependency group installed.

``run()`` takes an already-resolved capture directory / mapping / graph (this
module's own testable seam, mirroring ``blindfold.cli``'s ``run(argv, *, store)``)
for ``captures``/``explain``; ``serve`` is dispatched directly from ``main()`` to
:func:`blindfold_devtools.diagnostic_entry.run_diagnostic_server`, which resolves
its own settings and carries its own refusals (root-Transit-token, shared store,
missing capture directory, override drift). ``main()`` wires ``captures``/``explain``
from the real environment -- ``BLINDFOLD_EXCHANGE_CAPTURE_DIR``, the shared-store
refusal every devtools entry point carries (ADR-0047 §7), and the vendored-seed
mapping the shipped proxy itself blindfolds with by default.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TextIO

from blindfold.config import DEFAULT_HOST, DEFAULT_PORT
from blindfold.detection import Entity
from blindfold.serve import DevModeRequiredError
from blindfold.surrogates import SurrogateMapping
from rich.console import Console
from rich.table import Table

from .capture import read_capture
from .capture_listing import CaptureSummary, list_captures
from .capture_render import CaptureNotFoundError, render_capture, resolve_capture
from .diagnostic_entry import MissingCaptureDirectoryError, run_diagnostic_server
from .override_targets import OverrideDriftError
from .shared_store_refusal import SharedStoreRefusalError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blindfold",
        description="Diagnostic session: list and render Exchange captures (ADR-0047).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("captures", help="List the Exchange capture directory.")

    explain = sub.add_parser("explain", help="Render one Exchange capture.")
    explain.add_argument(
        "id", nargs="?", default=None,
        help="Capture id (a capture's filename, minus .jsonl).",
    )
    explain.add_argument("--last", action="store_true", help="Resolve the most recent capture.")

    serve = sub.add_parser("serve", help="Run a Diagnostic session's capturing proxy.")
    serve.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Bind address (default: {DEFAULT_HOST} -- loopback-only, same as `blindfold serve`).",
    )
    serve.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Bind port (default: {DEFAULT_PORT})."
    )

    return parser


def _print_captures_table(summaries: Iterable[CaptureSummary], *, out: TextIO) -> None:
    # A wide, fixed console width: this table is meant to be piped/grepped
    # (ADR-0047 §6, "greppable and pasteable"), so a column must never wrap
    # mid-id or mid-excerpt just because the terminal happened to be narrow.
    console = Console(file=out, width=240)
    table = Table()
    for column in ("id", "time", "endpoint", "hops", "detected", "outcome", "excerpt"):
        table.add_column(column, overflow="fold", no_wrap=True)
    for summary in summaries:
        table.add_row(
            summary.id,
            summary.ts or "",
            summary.endpoint or "",
            "" if summary.hop_count is None else str(summary.hop_count),
            "" if summary.detected_count is None else str(summary.detected_count),
            summary.outcome,
            summary.excerpt,
        )
    console.print(table)


def run(
    argv: Sequence[str],
    *,
    capture_dir: Path,
    mapping: SurrogateMapping,
    graph_entities: Iterable[Entity],
    out: TextIO | None = None,
) -> int:
    """Run ``captures``/``explain`` against an already-resolved capture
    directory, mapping and entity graph -- the testable seam ``main()`` wires
    from the real environment.

    ``out`` defaults to ``sys.stdout`` read at call time, not at import time --
    a default bound at definition would keep a stale reference once a caller
    (e.g. pytest's ``capsys``) swaps ``sys.stdout`` out from under it.
    """
    if out is None:
        out = sys.stdout
    parser = _build_parser()
    args = parser.parse_args(list(argv))

    if args.command == "captures":
        _print_captures_table(list_captures(capture_dir), out=out)
        return 0

    if args.command == "explain":
        try:
            path = resolve_capture(capture_dir, args.id, last=args.last)
        except CaptureNotFoundError as exc:
            print(f"blindfold explain: {exc}", file=out)
            return 1
        capture = read_capture(path)
        print(render_capture(capture.records, graph_entities=graph_entities, mapping=mapping), file=out)
        return 0

    return 2  # pragma: no cover - unreachable, argparse enforces a valid subcommand


def main(argv: Sequence[str] | None = None) -> int:
    from blindfold.config import get_settings
    from blindfold.store import vendored_seed_repository

    from .settings import load_devtools_settings

    argv = list(argv) if argv is not None else sys.argv[1:]
    args = _build_parser().parse_args(argv)

    if args.command == "serve":
        try:
            run_diagnostic_server(host=args.host, port=args.port)
        except (
            DevModeRequiredError,
            SharedStoreRefusalError,
            MissingCaptureDirectoryError,
            OverrideDriftError,
        ) as exc:
            print(f"blindfold: {exc}", file=sys.stderr)
            return 1
        return 0

    from .shared_store_refusal import refuse_if_shared_store

    settings = get_settings()
    try:
        refuse_if_shared_store(settings)
    except SharedStoreRefusalError as exc:
        print(f"blindfold: {exc}", file=sys.stderr)
        return 1

    devtools_settings = load_devtools_settings()
    if not devtools_settings.exchange_capture_dir:
        print(
            "blindfold: BLINDFOLD_EXCHANGE_CAPTURE_DIR is not set, so there is no "
            "capture directory to read (ADR-0047 §5).",
            file=sys.stderr,
        )
        return 1

    mapping = SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())
    return run(
        list(argv) if argv is not None else sys.argv[1:],
        capture_dir=Path(devtools_settings.exchange_capture_dir),
        mapping=mapping,
        graph_entities=mapping.entities(),
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
