"""``blindfold captures`` / ``blindfold explain`` / ``blindfold serve`` -- the
Diagnostic session's own CLI (ADR-0047 §6/§7, issues #257/#269/#271).

Source-run only, like every devtools entry point: there is no ``[project.scripts]``
wiring here, since that would install a console script pointing at a module absent
from the release wheel (ADR-0047 §2). Run as ``python -m blindfold_devtools captures``
/ ``... explain --last`` / ``... serve`` from a source checkout with the ``devtools``
dependency group installed.

``explain`` carries two meanings reconciled onto one verb, per issue #269's own
instruction to say which spelling won and why: **replaying** a payload and
**rendering** a capture are different verbs on the same noun (an Exchange
capture), but ``explain`` is deliberately the one command for both, because
replay's whole point is to be usable identically on a fresh payload and on an
already-captured live exchange (ADR-0047 §6: "so replay is literally the same
command as corpus-explain"). Concretely:

- ``explain --payload FILE`` / ``--text FILE`` -- no capture yet exists; replay
  creates a brand-new one (``observed`` inbound + blindfolded outbound, no
  provider-response fields -- nothing was ever sent) and renders it.
- ``explain <id>`` / ``--last`` -- a capture already exists (from a live run, or
  a prior replay); its own ``observed`` inbound payload is replayed and the
  ``reconstructed`` section is appended onto that *same* file (a no-op if it
  already carries one, so repeating the command doesn't duplicate detections),
  then rendered. This is what makes ``explain <id>`` do double duty as #257's
  original "render" and #255/#269's "replay" -- the first call on a given
  capture populates what render always needed and was waiting for.

``run()`` takes an already-resolved capture directory / mapping / graph / L3
detector (this module's own testable seam, mirroring ``blindfold.cli``'s
``run(argv, *, store)``) for ``captures``/``explain``; ``serve`` is dispatched
directly from ``main()`` to
:func:`blindfold_devtools.diagnostic_entry.run_diagnostic_server`, which resolves
its own settings and carries its own refusals (root-Transit-token, shared store,
missing capture directory, override drift). ``main()`` wires ``captures``/``explain``
from the real environment -- ``BLINDFOLD_EXCHANGE_CAPTURE_DIR``, the shared-store
refusal every devtools entry point carries (ADR-0047 §7), and the operator's own
real entity graph (read-only) and L3 detector, never the vendored seed the shipped
proxy blindfolds with by default -- validating replay against Sample data would
answer the wrong question (ADR-0047 §6).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from blindfold.config import DEFAULT_HOST, DEFAULT_PORT
from blindfold.detection import Entity
from blindfold.l3 import L3Detector
from blindfold.serve import DevModeRequiredError
from blindfold.surrogates import SurrogateMapping
from rich.console import Console
from rich.table import Table

from .capture import (
    SECTION_OBSERVED,
    SECTION_RECONSTRUCTED,
    CaptureWriter,
    DetectionRecord,
    FooterRecord,
    HeaderRecord,
    OutboundRecord,
    read_capture,
)
from .capture_directory import CAPTURE_SUFFIX, CaptureDirectory
from .capture_listing import CaptureSummary, list_captures
from .capture_render import CaptureNotFoundError, render_capture, resolve_capture
from .diagnostic_entry import MissingCaptureDirectoryError, run_diagnostic_server
from .override_targets import OverrideDriftError
from .replay import REPLAY_OUTCOME, replay, wrap_text_payload
from .shared_store_refusal import SharedStoreRefusalError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blindfold",
        description="Diagnostic session: list and render Exchange captures (ADR-0047).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("captures", help="List the Exchange capture directory.")

    explain = sub.add_parser(
        "explain", help="Replay a payload (or a capture's own inbound payload) and render it."
    )
    explain.add_argument(
        "id", nargs="?", default=None,
        help="Capture id (a capture's filename, minus .jsonl).",
    )
    explain.add_argument("--last", action="store_true", help="Resolve the most recent capture.")
    explain.add_argument(
        "--payload", metavar="FILE",
        help="Replay a request payload from FILE (either dialect, auto-detected by shape).",
    )
    explain.add_argument(
        "--text", metavar="FILE",
        help="Replay FILE's bare text, wrapped in a minimal single-hop payload.",
    )

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _replay_into_new_capture(
    payload: dict,
    *,
    capture_dir: Path,
    mapping: SurrogateMapping,
    l3_detector: L3Detector | None,
) -> Path:
    """Replay a bare payload (``--payload``/``--text``) into a brand-new
    Exchange capture: ``observed`` holds the inbound payload and the
    blindfolded payload this run produced, with no provider-response fields
    (nothing was ever sent) -- ADR-0047 §6."""
    result = replay(payload, mapping=mapping, l3_detector=l3_detector)
    directory = CaptureDirectory(capture_dir)
    capture_id, writer = directory.start_capture()
    with writer:
        writer.write(HeaderRecord(
            section=SECTION_OBSERVED,
            ts=_now_iso(),
            capture_id=capture_id,
            endpoint=result.endpoint,
            streamed=bool(payload.get("stream")),
            workspace="default",
            inbound_payload=payload,
        ))
        writer.write(OutboundRecord(section=SECTION_OBSERVED, ts=_now_iso(), payload=result.payload))
        for detection in result.detections:
            writer.write(detection)
        writer.write(FooterRecord(
            section=SECTION_OBSERVED,
            ts=_now_iso(),
            outcome=REPLAY_OUTCOME,
            reason=None,
            duration_ms=0.0,
            upstream_duration_ms=None,
            injected=dict(result.session.injected),
        ))
    return directory.path / f"{capture_id}{CAPTURE_SUFFIX}"


def _replay_and_append(
    path: Path,
    *,
    mapping: SurrogateMapping,
    l3_detector: L3Detector | None,
) -> None:
    """Replay an existing capture's own ``observed`` inbound payload and
    append the ``reconstructed`` section onto that same file -- a no-op if it
    already carries one (idempotent: replaying twice must not double the
    offsets a render would then show twice)."""
    capture = read_capture(path)
    header = next((r for r in capture.records if isinstance(r, HeaderRecord)), None)
    if header is None:
        return
    already_replayed = any(
        isinstance(r, DetectionRecord) and r.section == SECTION_RECONSTRUCTED for r in capture.records
    )
    if already_replayed:
        return
    result = replay(header.inbound_payload, mapping=mapping, l3_detector=l3_detector)
    # A freshly-constructed CaptureWriter starts its own size-cap accounting at
    # zero, blind to the bytes this file already holds from the live run -- a
    # capture already near DEFAULT_MAX_BYTES could exceed the nominal cap by
    # the appended reconstructed detail's size before this writer's own
    # truncation would trigger. Bounded (one DetectionRecord per hop, never
    # unbounded growth) and not privacy-relevant (no real value involved), so
    # left as a known gap rather than threading the prior byte count through.
    with CaptureWriter(path) as writer:
        for detection in result.detections:
            writer.write(detection)


def run(
    argv: Sequence[str],
    *,
    capture_dir: Path,
    mapping: SurrogateMapping,
    graph_entities: Iterable[Entity],
    l3_detector: L3Detector | None = None,
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
        if args.payload or args.text:
            if args.payload:
                payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
            else:
                payload = wrap_text_payload(Path(args.text).read_text(encoding="utf-8"))
            path = _replay_into_new_capture(
                payload, capture_dir=capture_dir, mapping=mapping, l3_detector=l3_detector
            )
        else:
            try:
                path = resolve_capture(capture_dir, args.id, last=args.last)
            except CaptureNotFoundError as exc:
                print(f"blindfold explain: {exc}", file=out)
                return 1
            _replay_and_append(path, mapping=mapping, l3_detector=l3_detector)

        capture = read_capture(path)
        print(render_capture(capture.records, graph_entities=graph_entities, mapping=mapping), file=out)
        return 0

    return 2  # pragma: no cover - unreachable, argparse enforces a valid subcommand


def _operator_mapping(workspace: str) -> SurrogateMapping:
    """Build a :class:`SurrogateMapping` from the operator's real entity graph
    (read-only) -- never the vendored seed: validating replay against Sample
    data would answer the wrong question (ADR-0047 §6). Mirrors
    :meth:`~blindfold.store.repository.VendoredSeedRepository.seeded_pairs`'
    own (real -> surrogate) pair shape, one canonical + one per variation, all
    sharing the entity's one active surrogate (ADR-0007).
    """
    from blindfold.app import get_entity_graph

    pairs: list[tuple[str, str]] = []
    for record in get_entity_graph().list_entities(workspace):
        if not record.active_surrogate:
            continue
        pairs.append((record.canonical_name, record.active_surrogate))
        for variation in record.variations:
            pairs.append((variation, record.active_surrogate))
    return SurrogateMapping.from_pairs(pairs)


def _operator_l3_detector(settings) -> L3Detector | None:
    """The real L3 detector, only when actually configured (ADR-0047 §6): an
    unwired/unprovisioned adjudicator is never handed to replay, since replay's
    mandatory ``inbox=None`` would otherwise raise ``L3Unavailable`` the first
    time a novel candidate span appeared (the request-path fail-closed
    behavior, wrong for a capability with no egress to protect) -- see
    ``replay.py``'s own module docstring for why ``inbox=None`` makes this
    detector's presence purely a stamp, not something that ever actually runs.
    Same settings check as :func:`blindfold.app._build_inner_l3_adjudicator` /
    the ``/v1/status`` health probe, a fourth consistent instance of it.
    """
    if settings.l3_provider == "gliner":
        from blindfold.gliner_provisioning import is_gliner_model_ready

        if not is_gliner_model_ready(settings.l3_gliner_model_path):
            return None
    elif not settings.l3_model:
        return None

    from blindfold.app import get_l3_detector

    return get_l3_detector()


def main(argv: Sequence[str] | None = None) -> int:
    from blindfold.config import get_settings
    from blindfold.policy import DEFAULT_WORKSPACE

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

    mapping = _operator_mapping(DEFAULT_WORKSPACE)
    return run(
        argv,
        capture_dir=Path(devtools_settings.exchange_capture_dir),
        mapping=mapping,
        graph_entities=mapping.entities(),
        l3_detector=_operator_l3_detector(settings),
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
