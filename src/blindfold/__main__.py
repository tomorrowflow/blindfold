"""``blindfold`` CLI — the runnable entry point (issue #44, UX-2/SEC-11/SEC-2).

``[project.scripts]`` wires ``blindfold`` to ``main()`` here. Today's only subcommand is
``serve``, which runs the proxy under the bundled ASGI server (see ``serve.py``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .serve import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DevModeRequiredError,
    LocalOnlyModelRequiredError,
    run_server,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blindfold")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_cmd = sub.add_parser("serve", help="Run the Blindfold proxy under the bundled ASGI server.")
    serve_cmd.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Bind address (default: {DEFAULT_HOST} — loopback-only; "
        "binding elsewhere is an explicit opt-in).",
    )
    serve_cmd.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Bind port (default: {DEFAULT_PORT})."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    if args.command == "serve":
        try:
            run_server(host=args.host, port=args.port)
        except (DevModeRequiredError, LocalOnlyModelRequiredError) as exc:
            print(f"blindfold: {exc}", file=sys.stderr)
            return 1
        return 0

    return 2  # pragma: no cover - unreachable, argparse enforces a valid subcommand


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
