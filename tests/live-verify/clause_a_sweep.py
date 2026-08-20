"""Clause-A sweep (issue #351): the offline twin of leak-audit clause A ("no
real entity egresses") over a real #74 live-verify run's own Exchange
captures, rather than a seeded pytest fixture.

Run 11's own sweep, the review-inbox precision report and the offline
projection script all lived in a session scratchpad and were destroyed with
it -- every run since run 5 rebuilt this apparatus by hand, so run-to-run
comparability rested on notes rather than committed code. This module (and
its sibling ``precision_report.py``) commit the apparatus instead.

Walks every string leaf of every ``outbound`` record (:mod:`blindfold_devtools
.capture`'s ``OutboundRecord`` -- the blindfolded payload actually sent
upstream) across every capture file in a run's output directory, and reports
a **word-boundary** match (:func:`blindfold.store._mint._real_value_pattern`,
the identical rule ``engine.leak_gate`` itself uses, so this sweep's notion of
"the value occurs" can never silently drift from the gate it audits) against:

- every seeded value in ``74-engagement-brief.md`` (:func:`extract_brief_seeded_values`);
- every real value the review inbox learned this run (an optional inbox fixture, the
  same ``{"items": [...]}`` shape ``GET /v1/management/review-inbox`` returns).

**Positive control** (the acceptance criterion's own wording: "a value that
MUST hit"): before scanning any real capture, :func:`run_self_check` proves
the matcher fires against a synthetic leaf built from one of the brief's own
seeded values. A sweep whose matcher (or brief parsing) is broken raises
:class:`SweepSelfCheckFailed` instead of silently reporting zero hits -- the
failure mode a clean-looking empty report invites.

A hit's real value is never printed in the report -- :class:`SweepHit` names
the origin and a short hash instead, mirroring
``blindfold_devtools.leak_check``'s own SEC-3 scrubbed-reference rule: this
report may be pasted somewhere with a wider audience than the raw captures
themselves.

Not collected by pytest (``tests/live-verify/README.md``): needs a real run's
captures. Regression tests for its pure functions live in
``tests/test_live_verify_clause_a_sweep.py``, loaded by file path since
``live-verify``'s hyphen makes it an invalid package name.

Usage, after a live-verify run has produced captures in ``<OUT-PATH>``:

    uv run python tests/live-verify/clause_a_sweep.py \\
        --captures-dir <OUT-PATH>/captures \\
        [--brief tests/live-verify/74-engagement-brief.md] \\
        [--inbox-file <review-inbox-fixture>.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from blindfold.store._mint import _real_value_pattern  # noqa: E402
from blindfold_devtools.capture import OutboundRecord, read_capture  # noqa: E402

CAPTURE_SUFFIX = ".jsonl"

_TABLE_ENTITY_COLUMNS = {"Person", "Employer", "Email", "Phone"}
_BOLD_CALLOUT_RE = re.compile(r"\*\*([^*]+)\*\*")


class SweepSelfCheckFailed(Exception):
    """The positive control did not fire -- the matcher (or brief parsing) is
    broken, so a zero-hit report cannot be trusted as a clean run."""


def extract_brief_seeded_values(brief_text: str) -> set[str]:
    """Every seeded real value in a live-verify engagement brief: the
    Person/Employer/Email/Phone table columns, plus any **bold** callout
    starting with an uppercase letter or digit (the engagement's client
    company, codename and billing IBAN) -- an emphasis-only bold span like
    "deliberately **novel**" starts lowercase and is excluded.

    Mirrors ``test_surrogate_component_remint_guard.py``'s own brief-derived
    vocabulary (issue #340) so this sweep's seeded set and that guard's
    forbidden-vocabulary set are built the same way, deliberately not shared
    code across a test file and a live-verify tool.
    """
    values: set[str] = set()
    lines = brief_text.splitlines()

    header_idx = None
    columns: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("|") and "Person" in stripped and "Employer" in stripped:
            header_idx = i
            columns = [cell.strip() for cell in stripped.strip("|").split("|")]
            break
    if header_idx is not None:
        col_indices = [i for i, name in enumerate(columns) if name in _TABLE_ENTITY_COLUMNS]
        for line in lines[header_idx + 2 :]:  # skip the "|---|...|" separator row
            stripped = line.strip()
            if not stripped.startswith("|"):
                break
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            for idx in col_indices:
                if idx < len(cells) and cells[idx]:
                    values.add(cells[idx])

    for match in _BOLD_CALLOUT_RE.finditer(brief_text):
        value = match.group(1).strip()
        if value and (value[0].isupper() or value[0].isdigit()):
            values.add(value)

    return values


def _walk_leaves_with_path(value: Any, path: str, fn: Callable[[str, str], None]) -> None:
    """Walk every string leaf of a nested JSON-shaped ``value``, calling
    ``fn(leaf_path, leaf_text)`` on each -- the path-tracking counterpart to
    ``blindfold.engine.walk_string_leaves``, which this sweep needs so a hit
    is reported with the leaf it was found at (the acceptance criterion's own
    "report each hit with its capture and leaf path")."""
    if isinstance(value, str):
        fn(path, value)
    elif isinstance(value, dict):
        for key, item in value.items():
            _walk_leaves_with_path(item, f"{path}.{key}" if path else key, fn)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _walk_leaves_with_path(item, f"{path}[{i}]", fn)


def _scrub_ref(origin: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{origin}:hash:{digest}"


@dataclass(frozen=True)
class SweepHit:
    """One real value found at a word boundary in an outbound leaf.

    ``value_ref`` never carries the plaintext value (mirrors
    ``blindfold_devtools.leak_check``'s SEC-3 scrubbed-reference rule).
    """

    capture_file: str
    leaf_path: str
    value_ref: str


def _hit_check(value: str, leaf_text: str) -> bool:
    if not value:
        return False
    return _real_value_pattern(value).search(leaf_text) is not None


def sweep_outbound_payloads(
    capture_file: str,
    payloads: Iterable[dict],
    seeded_values: Iterable[tuple[str, str]],
) -> list[SweepHit]:
    """Scan every string leaf of every outbound ``payloads`` entry for a
    word-boundary match against any ``(origin, value)`` pair in
    ``seeded_values``. One capture's payloads at a time, so a caller can
    attribute hits back to the capture file they came from."""
    hits: list[SweepHit] = []

    def on_leaf(leaf_path: str, leaf_text: str) -> None:
        for origin, value in seeded_values:
            if _hit_check(value, leaf_text):
                hits.append(
                    SweepHit(
                        capture_file=capture_file,
                        leaf_path=leaf_path,
                        value_ref=_scrub_ref(origin, value),
                    )
                )

    for payload in payloads:
        _walk_leaves_with_path(payload, "", on_leaf)

    return hits


def run_self_check(seeded_values: Iterable[tuple[str, str]]) -> None:
    """The positive control: prove the matcher fires against a synthetic leaf
    built from one of the sweep's own seeded values, before any real capture
    is scanned. Raises :class:`SweepSelfCheckFailed` -- loudly, not a warning
    -- if it doesn't, since that is exactly the shape of a broken sweep a
    zero-hit report would otherwise disguise as a clean run."""
    values = list(seeded_values)
    if not values:
        raise SweepSelfCheckFailed(
            "no seeded values to self-check against -- brief parsing produced an "
            "empty set, so a clean sweep result would be unattributable"
        )
    origin, control_value = values[0]
    canary_leaf = f"[clause-a-sweep positive control canary] {control_value} [/canary]"
    if not _hit_check(control_value, canary_leaf):
        raise SweepSelfCheckFailed(
            f"positive control ({origin}) did not fire against its own canary leaf -- "
            "the matcher is broken; a zero-hit report cannot be trusted"
        )


def _iter_capture_payloads(path: pathlib.Path) -> list[dict]:
    capture = read_capture(path)
    return [record.payload for record in capture.records if isinstance(record, OutboundRecord)]


def _load_review_inbox_real_values(inbox_file: pathlib.Path) -> set[str]:
    payload = json.loads(inbox_file.read_text())
    return {item["real"] for item in payload.get("items", []) if item.get("real")}


_DEFAULT_BRIEF = REPO_ROOT / "tests" / "live-verify" / "74-engagement-brief.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures-dir", required=True, help="a live-verify run's captures directory")
    parser.add_argument(
        "--brief",
        default=str(_DEFAULT_BRIEF),
        help="the engagement brief to extract seeded values from (default: %(default)s)",
    )
    parser.add_argument(
        "--inbox-file",
        default=None,
        help="optional review-inbox JSON fixture (the GET /v1/management/review-inbox {'items': [...]} shape)",
    )
    args = parser.parse_args(argv)

    brief_text = pathlib.Path(args.brief).read_text()
    seeded: list[tuple[str, str]] = [("brief", v) for v in sorted(extract_brief_seeded_values(brief_text))]
    if args.inbox_file:
        seeded.extend(
            ("review-inbox", v) for v in sorted(_load_review_inbox_real_values(pathlib.Path(args.inbox_file)))
        )

    run_self_check(seeded)

    captures_dir = pathlib.Path(args.captures_dir)
    capture_paths = sorted(captures_dir.glob(f"*{CAPTURE_SUFFIX}"))
    all_hits: list[SweepHit] = []
    for capture_path in capture_paths:
        payloads = _iter_capture_payloads(capture_path)
        all_hits.extend(sweep_outbound_payloads(capture_path.name, payloads, seeded))

    if not all_hits:
        print(
            f"clause-A sweep: positive control fired, {len(capture_paths)} capture(s) "
            f"scanned, 0 real value(s) found in outbound payloads"
        )
        return 0

    print(f"clause-A sweep: LEAK -- {len(all_hits)} hit(s):")
    for hit in all_hits:
        print(f"  {hit.capture_file} :: {hit.leaf_path} :: {hit.value_ref}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
