"""Assertive tier: must/must_not corpus harness, two tiers, L1/L2 gating (ADR-0047 SS11,
issue #258).

A record carries ``text`` or ``payload`` (hop structure matters -- a token safe in a
system prompt and the same token in a tool_result/tool_use JSON argument are different
code paths through blindfold.engine) plus ``must``/``must_not`` lists of real values,
each optionally tagged ``layer`` (``L1``/``L2``/``L3``) and ``kind`` (descriptive only).

Two tiers (ADR-0047 SS11):
- shipped -- ``tests/fixtures/assertive_*.json``, committed, provenance-pinned, gates CI.
- private -- an operator's own corpus at an arbitrary path, named by
  ``BLINDFOLD_PRIVATE_ASSERTIVE_CORPUS_PATH``, never committed, never required.

Gating is L1/L2 only (#249/#259/#260, ADR-0048): CI has no local adjudicator, and until
#261 lands batch composition is a function of process history, not hop text, so an L3
verdict is not reproducible even with one wired. A ``must``/``must_not`` entry tagged
``layer: "L3"`` is therefore skipped, never failed and never silently passed.

Seeding rule: a ``must`` entry seeds :class:`SurrogateMapping` (L2 dictionary match)
unless tagged ``layer: "L1"`` (real L1 regex path, ``blindfold.detection.detect_pii`` /
``mint_pii`` -- seeding it would route it through L2 instead and never exercise the
regex) or ``layer: "L3"`` (must stay unseeded -- it is a *novel* entity by definition;
seeding it would make L2 catch it and the L3 skip would prove nothing). A ``must_not``
entry is never seeded -- it must remain undetected on its own, not because the entity
graph doesn't know it.

Comparand reuse (issue #256, this issue's own "blocked by"): the primitive that answers
"is this real value present in the outbound payload" is ``blindfold.engine.walk_string_
leaves``, the same one ``blindfold_devtools.leak_check`` uses for its offline leak scan
-- not reimplemented here. ``capture_comparison.compare()``'s severity ladder (defect/
expected/unknown) answers a different question (observed vs. reconstructed *capture
sections*) and does not apply to a direct must/must_not assertion against one live
``blindfold_payload`` call, so it is not reused for that reason, not by oversight.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from blindfold.engine import blindfold_payload, walk_string_leaves
from blindfold.surrogates import SurrogateMapping

_SHIPPED_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "assertive_corpus_shipped.json"
_VENDORED_SEED_PATH = (
    Path(__file__).parent.parent / "src" / "blindfold" / "store" / "vendored_seed.json"
)
_PRIVATE_CORPUS_ENV_VAR = "BLINDFOLD_PRIVATE_ASSERTIVE_CORPUS_PATH"

_LAYER_L1 = "L1"
_LAYER_L3 = "L3"


def _load_corpus(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_assertive_shipped_corpus_has_complete_provenance():
    """Acceptance criterion: every shipped-corpus file carries a complete
    ``_provenance`` block, pinned against the actual current content of the Sample
    data (``vendored_seed.json``, ADR-0012) it's built on -- not just present, but
    verifiably accurate."""
    corpus = _load_corpus(_SHIPPED_FIXTURE_PATH)
    provenance = corpus["_provenance"]

    for key in (
        "dataset",
        "dataset_url",
        "license",
        "pinned_reference",
        "source_file",
        "source_file_sha256",
        "record_count",
        "selection_methodology",
    ):
        assert provenance.get(key), f"_provenance missing or empty {key!r}"

    assert provenance["record_count"] == len(corpus["records"])
    actual_sha256 = hashlib.sha256(_VENDORED_SEED_PATH.read_bytes()).hexdigest()
    assert provenance["source_file_sha256"] == actual_sha256, (
        "pinned source_file_sha256 no longer matches vendored_seed.json -- "
        "the Sample data this corpus is built on has drifted since it was pinned"
    )


def _record_payload(record: dict[str, Any]) -> dict[str, Any]:
    """The Messages-shaped payload a record's ``text`` or ``payload`` field describes."""
    if "payload" in record:
        return record["payload"]
    return {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": record["text"]}],
    }


def _collect_text(payload: Any) -> str:
    parts: list[str] = []
    walk_string_leaves(payload, parts.append)
    return "\x00".join(parts)


def _mapping_for_record(record: dict[str, Any]) -> SurrogateMapping:
    """Seed one SurrogateMapping from a record's own ``must`` entries.

    Only entries whose ``layer`` is L2 (the default when unspecified) are seeded --
    an L1 entry must go through the real regex/mint_pii path, not the dictionary
    match seeding would substitute for it; an L3 entry is a *novel* entity by
    definition and must stay unseeded, or the skip it's meant to exercise would
    prove nothing. ``must_not`` entries are never seeded -- they must stay
    undetected on their own, not because the entity graph doesn't know them.
    """
    mapping = SurrogateMapping()
    for index, entry in enumerate(record.get("must", [])):
        if entry.get("layer") in (_LAYER_L1, _LAYER_L3):
            continue
        mapping.seed(entry["value"], f"AssertiveSurrogate-{record['id']}-{index:03d}")
    return mapping


def _blindfolded_text_for_record(record: dict[str, Any]) -> str:
    mapping = _mapping_for_record(record)
    blinded, _session = blindfold_payload(_record_payload(record), mapping)
    return _collect_text(blinded)


def _assert_entry(record_id: str, direction: str, entry: dict[str, Any], blinded_text: str) -> None:
    """Assert one must/must_not entry against a record's already-blindfolded text.

    A ``layer: "L3"`` entry is skipped, not failed and not silently passed
    (ADR-0047 SS11's L1/L2-only gating decision, hardened by #259/#260/ADR-0048:
    CI has no local adjudicator, and until #261 lands an L3 verdict is a function
    of process history, not hop text, so it would not be reproducible even with
    one wired).
    """
    if entry.get("layer") == _LAYER_L3:
        pytest.skip(
            f"record {record_id!r}: {direction} entry {entry['value']!r} is L3-gated -- "
            "no adjudicator wired in this run (ADR-0047 SS11, L1/L2-only gating)"
        )
    if direction == "must":
        assert entry["value"] not in blinded_text, (
            f"record {record_id!r}: must-detect value {entry['value']!r} was not "
            "blindfolded before egress"
        )
    else:
        assert entry["value"] in blinded_text, (
            f"record {record_id!r}: must_not value {entry['value']!r} was wrongly "
            "blindfolded (over-redaction)"
        )


def _build_cases(corpus: dict[str, Any]) -> list[tuple[str, str, dict[str, Any], str]]:
    """One (record_id, direction, entry, blinded_text) tuple per must/must_not entry
    in ``corpus`` -- each becomes its own parametrized test node, so a miss/over-
    redaction/skip is reported against the exact record and value, not aggregated."""
    cases: list[tuple[str, str, dict[str, Any], str]] = []
    for record in corpus["records"]:
        blinded_text = _blindfolded_text_for_record(record)
        for entry in record.get("must", []):
            cases.append((record["id"], "must", entry, blinded_text))
        for entry in record.get("must_not", []):
            cases.append((record["id"], "must_not", entry, blinded_text))
    return cases


def _case_id(case: tuple[str, str, dict[str, Any], str]) -> str:
    record_id, direction, entry, _blinded_text = case
    return f"{record_id}::{direction}::{entry['value']}"


_SHIPPED_CORPUS = _load_corpus(_SHIPPED_FIXTURE_PATH)
_SHIPPED_CASES = _build_cases(_SHIPPED_CORPUS)


@pytest.mark.parametrize("case", _SHIPPED_CASES, ids=[_case_id(c) for c in _SHIPPED_CASES])
def test_shipped_assertive_corpus_entry(case):
    """The gating suite (acceptance criteria #1/#2/#3): every must/must_not entry in
    the shipped corpus, one test node per entry."""
    record_id, direction, entry, blinded_text = case
    _assert_entry(record_id, direction, entry, blinded_text)


def test_l3_marked_must_entry_raises_skip_not_assertion_error():
    """Unit-level proof that _assert_entry's L3 gate is a real pytest skip, distinct
    from the pass/fail assertion paths -- the corpus-level parametrized test above
    shows this as a SKIPPED row; this pins the exact exception type/reason."""
    corpus = _SHIPPED_CORPUS
    record = next(r for r in corpus["records"] if r["id"] == "novel_entity_requires_l3_and_is_gated")
    entry = record["must"][0]
    blinded_text = _blindfolded_text_for_record(record)
    assert entry["value"] in blinded_text  # sanity: genuinely undetected, not a false pass

    with pytest.raises(pytest.skip.Exception, match="L3-gated"):
        _assert_entry(record["id"], "must", entry, blinded_text)


def test_a_must_entry_that_is_not_detected_fails_naming_the_record_and_the_value():
    """Acceptance criterion: a must entry that is NOT detected fails the suite,
    naming the record and the value. Uses a real (unseeded, non-PII-shaped) value
    through the real blindfold_payload call -- not a fabricated blinded_text -- so
    this proves the assertion actually fires on a genuine miss, not on a hand-typed
    string that happens to satisfy the test."""
    record_id = "synthetic_undetected_referent"
    entry = {"value": "Casper Lindqvist", "layer": "L2"}
    # An empty mapping -- unlike _mapping_for_record, which would seed a must entry
    # and mask the very miss this test needs to prove the assertion catches.
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Please loop in Casper Lindqvist on this thread."}],
    }
    blinded, _session = blindfold_payload(payload, SurrogateMapping())
    blinded_text = _collect_text(blinded)
    assert entry["value"] in blinded_text  # sanity: genuinely undetected, not a false pass

    with pytest.raises(AssertionError) as excinfo:
        _assert_entry(record_id, "must", entry, blinded_text)
    assert record_id in str(excinfo.value)
    assert "Casper Lindqvist" in str(excinfo.value)


def test_a_must_not_entry_that_is_blindfolded_fails_naming_the_record_and_the_value():
    """Acceptance criterion: a must_not entry that IS blindfolded fails the suite,
    naming the record and the value. The value is deliberately seeded (as a must
    entry so _mapping_for_record picks it up) so the real engine actually replaces
    it, then re-checked as a must_not to prove the over-redaction assertion fires."""
    record = {
        "id": "synthetic_over_redacted_referent",
        "text": "The Widget project ships next week.",
        "must": [{"value": "Widget", "layer": "L2"}],
    }
    blinded_text = _blindfolded_text_for_record(record)
    assert "Widget" not in blinded_text  # sanity: genuinely blindfolded, not a false failure

    must_not_entry = {"value": "Widget", "layer": "L2"}
    with pytest.raises(AssertionError) as excinfo:
        _assert_entry(record["id"], "must_not", must_not_entry, blinded_text)
    assert record["id"] in str(excinfo.value)
    assert "Widget" in str(excinfo.value)


def test_private_corpus_at_an_arbitrary_path_runs_identically_to_the_shipped_tier(tmp_path):
    """Acceptance criterion: a private corpus at an arbitrary path runs identically.

    Synthetic fixture, written to tmp_path and discarded at teardown -- never
    committed, exactly the private tier's own contract. Reuses the exact same
    _load_corpus/_build_cases/_assert_entry functions the shipped tier's parametrize
    list is built from, so "runs identically" is a fact about the code path, not an
    assertion repeated by hand."""
    private_corpus = {
        "records": [
            {
                "id": "operators_own_referent",
                "text": "Jordan Blake signed off on the change; Blakeley Street was mentioned separately.",
                "must": [{"value": "Jordan Blake", "layer": "L2", "kind": "person"}],
                "must_not": [{"value": "Blakeley", "layer": "L2", "kind": "term"}],
            }
        ]
    }
    private_path = tmp_path / "operator_corpus.json"
    private_path.write_text(json.dumps(private_corpus), encoding="utf-8")

    corpus = _load_corpus(private_path)
    for case in _build_cases(corpus):
        _assert_entry(*case)


def test_operator_private_corpus_if_configured():
    """The actual opt-in private-tier test: skipped (not failed) when the operator
    hasn't pointed BLINDFOLD_PRIVATE_ASSERTIVE_CORPUS_PATH at their own corpus --
    proving the private tier is never required for the suite to pass. When set, it
    runs the identical must/must_not sweep the shipped tier runs."""
    path = os.environ.get(_PRIVATE_CORPUS_ENV_VAR)
    if not path:
        pytest.skip(
            f"no private assertive corpus configured -- set {_PRIVATE_CORPUS_ENV_VAR} "
            "to an operator-owned corpus file to run this tier locally"
        )
    corpus = _load_corpus(Path(path))
    for case in _build_cases(corpus):
        _assert_entry(*case)


def test_assertive_shipped_corpus_entries_are_literal_substrings_of_their_record():
    """Guards against a vendoring bug that would make the assertive check vacuous: every
    must/must_not value actually occurs, verbatim, in the record it's attached to."""
    for record in _SHIPPED_CORPUS["records"]:
        source_text = _collect_text(_record_payload(record))
        for entry in record.get("must", []) + record.get("must_not", []):
            assert entry["value"] in source_text, (
                f"record {record['id']!r}: {entry['value']!r} not found verbatim in "
                "the record's own text/payload"
            )
