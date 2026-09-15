"""``blindfold`` CLI — the runnable entry point (issue #44, UX-2/SEC-11/SEC-2).

``[project.scripts]`` wires ``blindfold`` to ``main()`` here. Subcommands: ``serve``
runs the proxy under the bundled ASGI server (see ``serve.py``); ``connect
claude-desktop`` writes the Claude Desktop 3P Gateway profile (ADR-0057 D7, issue #377).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .claude_desktop_connect import (
    ManagedProfileError,
    NoBackupError,
    connect_claude_desktop,
    relaunch_claude_desktop,
    resolve_claude_desktop_base_dir,
    resolve_managed_plist_path,
    restore_claude_desktop,
)
from .config import get_settings
from .policy import DEFAULT_WORKSPACE
from .serve import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DevModeRequiredError,
    LocalOnlyModelRequiredError,
    run_server,
)

# ADR-0057 Consequences: Desktop in 3P Gateway mode has no signed-in Anthropic account,
# so subscription-backed inference is structurally unavailable there -- the credential
# this command's profile points at must be a Console API key, billed per token to
# whoever owns it. Surfaced wherever the credential step is named (--help here, and the
# post-write summary), per this issue's own added AC.
_CREDENTIAL_KIND_NOTE = (
    "The credential Claude Desktop needs here is a Console API key (not a claude.ai "
    "subscription -- 3P Gateway mode has no signed-in Anthropic account, ADR-0057 "
    "Consequences), billed per token to whoever owns it."
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

    connect_cmd = sub.add_parser("connect", help="Write a client's connection profile.")
    connect_sub = connect_cmd.add_subparsers(dest="target", required=True)

    desktop_cmd = connect_sub.add_parser(
        "claude-desktop",
        help="Write the Claude Desktop 3P Gateway profile (ADR-0057 D7). macOS only.",
        description=f"Write the Claude Desktop 3P Gateway profile (ADR-0057 D7). {_CREDENTIAL_KIND_NOTE}",
    )
    desktop_cmd.add_argument(
        "--restore",
        action="store_true",
        help="Reverse a previous `connect claude-desktop` from its backup, and exit.",
    )
    desktop_cmd.add_argument(
        "--host", default=None, help="Gateway host the profile points at (default: the configured serve host)."
    )
    desktop_cmd.add_argument(
        "--port", type=int, default=None, help="Gateway port the profile points at (default: the configured serve port)."
    )
    desktop_cmd.add_argument(
        "--workspace",
        default=DEFAULT_WORKSPACE,
        help=f"Workspace tag sent as the x-blindfold-workspace header (default: {DEFAULT_WORKSPACE!r}).",
    )
    desktop_cmd.add_argument(
        "--api-key-from-env",
        metavar="VAR",
        default=None,
        help=(
            "Name of an environment variable holding a Console API key to write into "
            "the profile -- the VALUE is read from that variable, never from this "
            f"argument, so it never appears in shell history. {_CREDENTIAL_KIND_NOTE} "
            "Omitted by default: the key is left blank and entered in the Desktop UI."
        ),
    )
    desktop_cmd.add_argument(
        "--relaunch",
        action="store_true",
        help="Quit, wait, and relaunch Claude Desktop after writing, so the profile "
        "is picked up (config is read once at launch). Default: do not touch the "
        "running app.",
    )
    return parser


def _connect_claude_desktop(args: argparse.Namespace) -> int:
    base_dir = resolve_claude_desktop_base_dir()

    if args.restore:
        try:
            result = restore_claude_desktop(base_dir=base_dir)
        except NoBackupError as exc:
            print(f"blindfold: {exc}", file=sys.stderr)
            return 1
        print(f"restored {base_dir} (removed profile {result.restored_profile_id})")
        return 0

    settings = get_settings()
    host = args.host if args.host is not None else settings.host
    port = args.port if args.port is not None else settings.port

    try:
        result = connect_claude_desktop(
            base_dir=base_dir,
            managed_plist_path=resolve_managed_plist_path(),
            host=host,
            port=port,
            workspace=args.workspace,
            api_key_env=args.api_key_from_env,
        )
    except (ManagedProfileError, ValueError) as exc:
        print(f"blindfold: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {result.profile_path}")
    if result.wrote_key:
        print(f"credential written from ${args.api_key_from_env}.")
    else:
        print(f"No credential written. {_CREDENTIAL_KIND_NOTE}")
        print("In Claude Desktop: Developer -> Configure Third-Party Inference, enter "
              "the key, then click Test connection.")

    if args.relaunch:
        relaunch_claude_desktop()

    return 0


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

    if args.command == "connect" and args.target == "claude-desktop":
        return _connect_claude_desktop(args)

    return 2  # pragma: no cover - unreachable, argparse enforces a valid subcommand


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
