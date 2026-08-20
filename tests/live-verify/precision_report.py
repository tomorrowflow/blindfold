"""Precision report (issue #351): the review-inbox precision figure and
per-mint provenance apparatus run 11's own report lived only in a session
scratchpad and was destroyed with it.

Reads a review inbox (an offline JSON fixture in the exact
``GET /v1/management/review-inbox`` ``{"items": [...]}`` shape, or that same
endpoint called live against a running proxy), classifies each mint
**genuine** vs **false positive** against ``74-engagement-brief.md``'s own
seeded set (:func:`clause_a_sweep.extract_brief_seeded_values`), and prints
the #59 precision figure (ADR-0023's own numeric bar: "N genuine of M mints
-- P%") plus per-mint provenance -- issue #348's ``adjudicator``/
``entity_type``, and issue #350's per-candidate suppression trace **when
present** (that issue is still open at the time this script is committed, so
the trace's field name isn't settled yet; read defensively via ``.get(...)``
rather than assuming one).

A mint is classified **genuine** if its ``real`` value occurs, at a word
boundary, inside any seeded value or vice versa (:func:`classify_mint`) -- the
"vice versa" half is #60's own known non-bug: a novel two-token name can mint
two surrogates, one per token, so a lone fragment ("Ostrowski") of a seeded
full name ("Mara Ostrowski") is still a true positive, not a false one.

Not collected by pytest (``tests/live-verify/README.md``): needs a real
run's review inbox. Regression tests for its pure functions live in
``tests/test_live_verify_precision_report.py``, loaded by file path since
``live-verify``'s hyphen makes it an invalid package name.

Usage, after a live-verify run's review inbox has candidates in it:

    uv run python tests/live-verify/precision_report.py \\
        --inbox-file <review-inbox-fixture>.json \\
        [--brief tests/live-verify/74-engagement-brief.md]

    # or, against a running proxy:
    uv run python tests/live-verify/precision_report.py \\
        --base-url http://localhost:25463 --workspace default \\
        --auth-token <management-token>
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

_LIVE_VERIFY_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_LIVE_VERIFY_DIR))

from blindfold.store._mint import _real_value_pattern  # noqa: E402
from clause_a_sweep import extract_brief_seeded_values  # noqa: E402

GENUINE = "genuine"
FALSE_POSITIVE = "false_positive"

_DEFAULT_BRIEF = REPO_ROOT / "tests" / "live-verify" / "74-engagement-brief.md"
_DEFAULT_BASE_URL = "http://localhost:25463"  # config.py's DEFAULT_PORT


def classify_mint(real: str, seeded_values: set[str]) -> str:
    """genuine if ``real`` occurs at a word boundary inside a seeded value, or
    a seeded value occurs at a word boundary inside ``real`` (the #60
    fragmentation case) -- false_positive otherwise."""
    for seeded in seeded_values:
        if _real_value_pattern(real).search(seeded) or _real_value_pattern(seeded).search(real):
            return GENUINE
    return FALSE_POSITIVE


def precision_summary(genuine_count: int, total: int) -> str:
    if total == 0:
        return "#59 precision: no mints in the review inbox"
    pct = round(100 * genuine_count / total)
    return f"#59 precision: {pct}% ({genuine_count} genuine of {total} mints)"


def mint_provenance_line(item: dict[str, Any], classification: str) -> str:
    parts = [
        f"id={item.get('id')}",
        f"real={item.get('real')!r}",
        f"classification={classification}",
        f"entity_type={item.get('entity_type')}",
        f"adjudicator={item.get('adjudicator')}",
    ]
    # Issue #350 (per-candidate suppression trace) is still open as of this
    # script's commit -- its field name isn't settled, so this is read
    # defensively and simply omitted until that issue lands.
    suppression_trace = item.get("suppression_trace")
    if suppression_trace is not None:
        parts.append(f"suppression_trace={suppression_trace}")
    return "  " + " ".join(parts)


def load_inbox_items_from_file(inbox_file: pathlib.Path) -> list[dict[str, Any]]:
    payload = json.loads(inbox_file.read_text())
    return list(payload.get("items", []))


def fetch_inbox_items_live(base_url: str, workspace: str, auth_token: str | None) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/v1/management/review-inbox?workspace={workspace}"
    request = urllib.request.Request(url)
    if auth_token:
        request.add_header("Authorization", f"Bearer {auth_token}")
    with urllib.request.urlopen(request, timeout=5) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return list(payload.get("items", []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--brief",
        default=str(_DEFAULT_BRIEF),
        help="the engagement brief to derive the seeded (genuine) set from (default: %(default)s)",
    )
    parser.add_argument("--inbox-file", default=None, help="offline review-inbox JSON fixture")
    parser.add_argument("--base-url", default=None, help="a running proxy's base URL, instead of --inbox-file")
    parser.add_argument("--workspace", default="default", help="workspace query param for --base-url (default: %(default)s)")
    parser.add_argument("--auth-token", default=None, help="management bearer token for --base-url")
    args = parser.parse_args(argv)

    if not args.inbox_file and not args.base_url:
        parser.error("one of --inbox-file or --base-url is required")

    brief_text = pathlib.Path(args.brief).read_text()
    seeded_values = extract_brief_seeded_values(brief_text)

    if args.inbox_file:
        items = load_inbox_items_from_file(pathlib.Path(args.inbox_file))
    else:
        items = fetch_inbox_items_live(args.base_url, args.workspace, args.auth_token)

    genuine_count = 0
    lines = []
    for item in items:
        classification = classify_mint(item.get("real", ""), seeded_values)
        if classification == GENUINE:
            genuine_count += 1
        lines.append(mint_provenance_line(item, classification))

    print(precision_summary(genuine_count, len(items)))
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
