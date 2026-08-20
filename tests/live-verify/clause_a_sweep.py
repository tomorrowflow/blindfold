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

**Mint-aware classification (issue #357):** a review item carries no detection timestamp,
so a false-positive mint's own pre-mint occurrences -- ordinary text that predates the
capture where L3 actually confirmed it as an entity -- used to read as egress on a clean
run. :func:`find_mint_capture_indices` derives each review-inbox value's mint point as the
first capture whose outbound payload contains its provisional surrogate; a hit strictly
before that capture is reported separately as ``pre_mint`` rather than folded into the
``LEAK`` count. A brief-seeded value has no mint point (it was a known referent before the
run even started), so every occurrence of one is still a leak.

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


# A review-inbox value whose mint capture can't be established in this run's
# own captures (its surrogate never appears) is treated the same fail-closed
# way as a brief-seeded value: every capture counts as "at or after" it, so
# nothing is exempted as pre-mint on the strength of an absence.
MINT_UNKNOWN = -1


@dataclass(frozen=True)
class SweepHit:
    """One real value found at a word boundary in an outbound leaf.

    ``value_ref`` never carries the plaintext value (mirrors
    ``blindfold_devtools.leak_check``'s SEC-3 scrubbed-reference rule).

    ``pre_mint`` (issue #357) is True when this occurrence's capture precedes
    the value's own mint point -- the value egressed before it was ever a
    known entity, so detection missing its first hop is not a clause-A
    violation the way a later occurrence would be. A brief-seeded value has
    no mint point at all and is therefore never ``pre_mint``.
    """

    capture_file: str
    leaf_path: str
    value_ref: str
    pre_mint: bool = False


def _hit_check(value: str, leaf_text: str) -> bool:
    if not value:
        return False
    return _real_value_pattern(value).search(leaf_text) is not None


def _payload_contains(payloads: Iterable[dict], value: str) -> bool:
    found = False

    def on_leaf(_leaf_path: str, leaf_text: str) -> None:
        nonlocal found
        if _hit_check(value, leaf_text):
            found = True

    for payload in payloads:
        if found:
            break
        _walk_leaves_with_path(payload, "", on_leaf)
    return found


def find_mint_capture_indices(
    capture_order: list[str],
    payloads_by_capture: dict[str, list[dict]],
    seeded_values: Iterable[tuple[str, str, str | None]],
) -> dict[str, int]:
    """The index into ``capture_order`` of the first capture whose outbound
    payloads contain each review-inbox value's provisional surrogate -- the
    mint point issue #357 needs to tell a pre-mint occurrence from a clause-A
    violation. A value with no ``mint_surrogate`` (a brief-seeded value, which
    has no mint point) is absent from the result; so is a review-inbox value
    whose surrogate never appears in this run's own captures, deliberately --
    see :data:`MINT_UNKNOWN` at the call site."""
    mint_index: dict[str, int] = {}
    for _origin, value, mint_surrogate in seeded_values:
        if mint_surrogate is None or value in mint_index:
            continue
        for index, capture_file in enumerate(capture_order):
            if _payload_contains(payloads_by_capture[capture_file], mint_surrogate):
                mint_index[value] = index
                break
    return mint_index


def sweep_outbound_payloads(
    capture_file: str,
    payloads: Iterable[dict],
    seeded_values: Iterable[tuple[str, str, str | None]],
    capture_index: int = 0,
    mint_index_by_value: dict[str, int] | None = None,
) -> list[SweepHit]:
    """Scan every string leaf of every outbound ``payloads`` entry for a
    word-boundary match against any ``(origin, value, mint_surrogate)`` triple
    in ``seeded_values``. One capture's payloads at a time, so a caller can
    attribute hits back to the capture file they came from.

    ``capture_index``/``mint_index_by_value`` (issue #357) let a hit be
    classified ``pre_mint`` when it occurs strictly before the value's own
    mint capture (see :func:`find_mint_capture_indices`) -- omitted, a hit is
    never pre-mint, matching a brief-seeded value's own "no mint point" rule.
    """
    mint_index_by_value = mint_index_by_value or {}
    hits: list[SweepHit] = []

    def on_leaf(leaf_path: str, leaf_text: str) -> None:
        for origin, value, _mint_surrogate in seeded_values:
            if _hit_check(value, leaf_text):
                mint_at = mint_index_by_value.get(value, MINT_UNKNOWN)
                hits.append(
                    SweepHit(
                        capture_file=capture_file,
                        leaf_path=leaf_path,
                        value_ref=_scrub_ref(origin, value),
                        pre_mint=capture_index < mint_at,
                    )
                )

    for payload in payloads:
        _walk_leaves_with_path(payload, "", on_leaf)

    return hits


def run_self_check(seeded_values: Iterable[tuple[str, str, str | None]]) -> None:
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
    origin, control_value, _mint_surrogate = values[0]
    canary_leaf = f"[clause-a-sweep positive control canary] {control_value} [/canary]"
    if not _hit_check(control_value, canary_leaf):
        raise SweepSelfCheckFailed(
            f"positive control ({origin}) did not fire against its own canary leaf -- "
            "the matcher is broken; a zero-hit report cannot be trusted"
        )


def _iter_capture_payloads(path: pathlib.Path) -> list[dict]:
    capture = read_capture(path)
    return [record.payload for record in capture.records if isinstance(record, OutboundRecord)]


def _load_review_inbox_items(inbox_file: pathlib.Path) -> list[tuple[str, str]]:
    """Every ``(real, provisional_surrogate)`` pair the review inbox learned
    this run. The surrogate is what :func:`find_mint_capture_indices` looks
    for to establish this value's mint point (issue #357) -- without it, a
    pre-mint occurrence of a false-positive mint has no way to be told apart
    from a genuine clause-A violation."""
    payload = json.loads(inbox_file.read_text())
    items: dict[str, str] = {}
    for item in payload.get("items", []):
        real = item.get("real")
        provisional_surrogate = item.get("provisional_surrogate")
        if real and provisional_surrogate:
            items.setdefault(real, provisional_surrogate)
    return sorted(items.items())


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
    seeded: list[tuple[str, str, str | None]] = [
        ("brief", v, None) for v in sorted(extract_brief_seeded_values(brief_text))
    ]
    if args.inbox_file:
        seeded.extend(
            ("review-inbox", real, provisional_surrogate)
            for real, provisional_surrogate in _load_review_inbox_items(pathlib.Path(args.inbox_file))
        )

    run_self_check(seeded)

    captures_dir = pathlib.Path(args.captures_dir)
    capture_paths = sorted(captures_dir.glob(f"*{CAPTURE_SUFFIX}"))
    capture_order = [capture_path.name for capture_path in capture_paths]
    payloads_by_capture = {
        capture_path.name: _iter_capture_payloads(capture_path) for capture_path in capture_paths
    }

    mint_index_by_value = find_mint_capture_indices(capture_order, payloads_by_capture, seeded)

    all_hits: list[SweepHit] = []
    for capture_index, capture_name in enumerate(capture_order):
        all_hits.extend(
            sweep_outbound_payloads(
                capture_name,
                payloads_by_capture[capture_name],
                seeded,
                capture_index=capture_index,
                mint_index_by_value=mint_index_by_value,
            )
        )

    leak_hits = [hit for hit in all_hits if not hit.pre_mint]
    pre_mint_hits = [hit for hit in all_hits if hit.pre_mint]

    def _print_pre_mint_section() -> None:
        if pre_mint_hits:
            print(
                f"  pre-mint occurrences (not a clause-A violation -- egressed before "
                f"the value's own mint): {len(pre_mint_hits)}"
            )
            for hit in pre_mint_hits:
                print(f"    {hit.capture_file} :: {hit.leaf_path} :: {hit.value_ref}")

    if not leak_hits:
        print(
            f"clause-A sweep: positive control fired, {len(capture_paths)} capture(s) "
            f"scanned, 0 real value(s) found in outbound payloads"
        )
        _print_pre_mint_section()
        return 0

    print(f"clause-A sweep: LEAK -- {len(leak_hits)} hit(s):")
    for hit in leak_hits:
        print(f"  {hit.capture_file} :: {hit.leaf_path} :: {hit.value_ref}")
    _print_pre_mint_section()
    return 1


if __name__ == "__main__":
    sys.exit(main())
