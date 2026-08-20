"""Precision report (issue #351): regression tests for
``tests/live-verify/precision_report.py``, the review-inbox precision figure
and per-mint provenance apparatus run 11's own report lived only in a session
scratchpad and was destroyed with it.

Loaded by file path, same reason as ``test_live_verify_preflight.py``:
``live-verify``'s hyphen makes it an invalid Python package name.

Leak-audit clause analysis: N/A -- this module classifies and prints
management-API review-inbox fixture data that is already ``viewer``-gated
plaintext (the same sensitivity class ``GET /v1/management/review-inbox``
itself already exposes); no request-path egress/restore/fail-closed code is
touched.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_LIVE_VERIFY_DIR = pathlib.Path(__file__).parent / "live-verify"


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, _LIVE_VERIFY_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


precision_report = _load_module("precision_report")

_SEEDED_VALUES = {"Mara Ostrowski", "Voltbridge Systems", "Rindawa Holdings AG", "Project Kettlebrook"}


def test_classify_mint_genuine_when_real_value_matches_a_seeded_value():
    assert precision_report.classify_mint("Mara Ostrowski", _SEEDED_VALUES) == "genuine"


def test_classify_mint_genuine_for_a_fragment_of_a_seeded_multi_word_name():
    # #60's own known non-bug: a novel two-token name can mint two surrogates
    # (one per token) rather than one -- a fragment is still genuine, not a
    # false positive, since it is a real substring of a seeded value.
    assert precision_report.classify_mint("Ostrowski", _SEEDED_VALUES) == "genuine"


def test_classify_mint_false_positive_when_no_seeded_value_matches():
    assert precision_report.classify_mint("some prose word", _SEEDED_VALUES) == "false_positive"


def test_precision_summary_matches_adr_0023s_own_numeric_bar_phrasing():
    # ADR-0023's own wording: "6 genuine of 14 mints -- up from run 10's 22%".
    assert precision_report.precision_summary(6, 14) == "#59 precision: 43% (6 genuine of 14 mints)"


def test_precision_summary_handles_an_empty_inbox_without_dividing_by_zero():
    assert precision_report.precision_summary(0, 0) == "#59 precision: no mints in the review inbox"


def test_mint_provenance_line_carries_entity_type_and_adjudicator():
    item = {"id": "1", "real": "Mara Ostrowski", "entity_type": None, "adjudicator": "inner_llm"}
    line = precision_report.mint_provenance_line(item, "genuine")
    assert "entity_type=None" in line
    assert "adjudicator=inner_llm" in line
    assert "classification=genuine" in line


def test_mint_provenance_line_omits_suppression_trace_when_absent():
    item = {"id": "1", "real": "some prose word", "entity_type": None, "adjudicator": None}
    line = precision_report.mint_provenance_line(item, "false_positive")
    assert "suppression_trace" not in line


def test_mint_provenance_line_includes_suppression_trace_when_present():
    # Issue #350 (per-candidate suppression trace) isn't shipped yet -- this
    # proves precision_report.py is forward-compatible with it landing later,
    # reading the field defensively rather than assuming it's always there.
    item = {
        "id": "1",
        "real": "some prose word",
        "entity_type": None,
        "adjudicator": None,
        "suppression_trace": {"case_inconsistency": "conjunctive_run_survived"},
    }
    line = precision_report.mint_provenance_line(item, "false_positive")
    assert "suppression_trace=" in line
    assert "conjunctive_run_survived" in line


def test_main_reads_a_fixture_inbox_file_and_prints_the_precision_summary(tmp_path, capsys):
    brief_path = tmp_path / "brief.md"
    brief_path.write_text(
        "## The engagement\n\n"
        "| Person | Role | Employer | Email | Phone |\n"
        "|---|---|---|---|---|\n"
        "| Mara Ostrowski | Lead architect | Voltbridge Systems | m@voltbridge-systems.example | +41 79 555 0111 |\n"
    )
    inbox_path = tmp_path / "inbox.json"
    inbox_path.write_text(
        precision_report.json.dumps(
            {
                "items": [
                    {
                        "id": "1",
                        "real": "Mara Ostrowski",
                        "provisional_surrogate": "surrogate-a",
                        "context": "...",
                        "context_offset": 0,
                        "entity_type": None,
                        "adjudicator": "inner_llm",
                        "kind": "term",
                    },
                    {
                        "id": "2",
                        "real": "unrelated prose word",
                        "provisional_surrogate": "surrogate-b",
                        "context": "...",
                        "context_offset": 0,
                        "entity_type": None,
                        "adjudicator": "gliner",
                        "kind": "term",
                    },
                ]
            }
        )
    )

    exit_code = precision_report.main(
        ["--inbox-file", str(inbox_path), "--brief", str(brief_path)]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "#59 precision: 50% (1 genuine of 2 mints)" in out
    assert "id=1" in out and "classification=genuine" in out
    assert "id=2" in out and "classification=false_positive" in out
