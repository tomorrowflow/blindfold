"""Clause-A sweep (issue #351): regression tests for the committed apparatus at
``tests/live-verify/clause_a_sweep.py`` -- run 11's own sweep lived only in a
session scratchpad and was destroyed with it (issue #351's own motivation).

Loaded by file path, same reason as ``test_live_verify_preflight.py``:
``live-verify``'s hyphen makes it an invalid Python package name.

Leak-audit clause analysis: N/A -- this module operates entirely on
already-written Exchange captures and a fixture brief/review-inbox; it never
touches request-path egress/restore/fail-closed code.
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
    # Dataclass field-type resolution looks up sys.modules[cls.__module__], so
    # a module loaded by file path (never `import`ed) must be registered here
    # first -- the same reason blindfold_devtools' own capture.py dataclasses
    # need this when a test loads a live-verify script the same way.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


clause_a_sweep = _load_module("clause_a_sweep")


_FIXTURE_BRIEF = """\
## The engagement

| Person | Role | Employer | Email | Phone |
|---|---|---|---|---|
| Mara Ostrowski | Lead architect (ours) | Voltbridge Systems | m.ostrowski@voltbridge-systems.example | +41 79 555 0111 |

- Client company: **Rindawa Holdings AG** (Zurich).
- The engagement's internal codename is **Project Kettlebrook** (do not disclose externally).
"""


def test_extract_brief_seeded_values_finds_table_and_bold_callout_entities():
    values = clause_a_sweep.extract_brief_seeded_values(_FIXTURE_BRIEF)

    assert "Mara Ostrowski" in values
    assert "Voltbridge Systems" in values
    assert "m.ostrowski@voltbridge-systems.example" in values
    assert "Rindawa Holdings AG" in values
    assert "Project Kettlebrook" in values


def test_extract_brief_seeded_values_excludes_lowercase_emphasis_bold_spans():
    # "deliberately **novel**" is plain emphasis, not an entity callout --
    # excluded because it starts lowercase (mirrors the guard test's own rule).
    brief = _FIXTURE_BRIEF + "\nThese values are deliberately **novel**.\n"
    values = clause_a_sweep.extract_brief_seeded_values(brief)
    assert "novel" not in values


def test_sweep_outbound_payloads_reports_capture_and_leaf_path_on_a_hit():
    payloads = [{"messages": [{"role": "user", "content": "cc Mara Ostrowski"}]}]

    hits = clause_a_sweep.sweep_outbound_payloads(
        "20260820-abcd.jsonl", payloads, [("brief", "Mara Ostrowski")]
    )

    assert len(hits) == 1
    hit = hits[0]
    assert hit.capture_file == "20260820-abcd.jsonl"
    assert hit.leaf_path == "messages[0].content"
    # SEC-3 scrubbed-reference rule: the real value itself never appears in the finding.
    assert "Mara Ostrowski" not in hit.value_ref
    assert hit.value_ref.startswith("brief:hash:")


def test_sweep_outbound_payloads_is_word_boundary_only_not_bare_substring():
    # "Ostrowskiego" (a plausible inflected form) must not false-positive on
    # the bare "Ostrowski" match -- same word-boundary rule the real leak
    # gate uses (issue #293), not raw substring containment.
    payloads = [{"messages": [{"role": "user", "content": "Ostrowskiego reported the finding"}]}]

    hits = clause_a_sweep.sweep_outbound_payloads(
        "20260820-abcd.jsonl", payloads, [("brief", "Ostrowski")]
    )

    assert hits == []


def test_sweep_outbound_payloads_clean_when_no_seeded_value_present():
    payloads = [{"messages": [{"role": "user", "content": "cc surrogate-name only"}]}]

    hits = clause_a_sweep.sweep_outbound_payloads(
        "20260820-abcd.jsonl", payloads, [("brief", "Mara Ostrowski")]
    )

    assert hits == []


def test_run_self_check_passes_silently_with_a_working_matcher():
    clause_a_sweep.run_self_check([("brief", "Mara Ostrowski")])


def test_run_self_check_raises_loudly_when_there_are_no_seeded_values_to_check():
    # Broken brief parsing producing an empty seeded set must not be read as
    # "nothing to find" -- it is the exact failure mode the positive control
    # exists to catch.
    with pytest.raises(clause_a_sweep.SweepSelfCheckFailed):
        clause_a_sweep.run_self_check([])


def test_run_self_check_raises_loudly_when_the_matcher_itself_is_broken(monkeypatch):
    # Simulate a broken matcher (e.g. a regressed word-boundary rule) by
    # forcing every hit check to report no match -- the self-check must fail
    # loudly instead of the sweep silently reporting zero hits as clean.
    monkeypatch.setattr(clause_a_sweep, "_hit_check", lambda value, leaf_text: False)
    with pytest.raises(clause_a_sweep.SweepSelfCheckFailed):
        clause_a_sweep.run_self_check([("brief", "Mara Ostrowski")])


def _write_fixture_capture(path, *, outbound_content: str) -> None:
    from blindfold_devtools.capture import SECTION_OBSERVED, CaptureWriter, FooterRecord, HeaderRecord, OutboundRecord

    with CaptureWriter(path) as writer:
        writer.write(
            HeaderRecord(
                section=SECTION_OBSERVED,
                ts="2026-08-20T00:00:00+00:00",
                capture_id=path.stem,
                endpoint="messages",
                streamed=False,
                workspace="default",
                inbound_payload={"messages": [{"role": "user", "content": "irrelevant"}]},
            )
        )
        writer.write(
            OutboundRecord(
                section=SECTION_OBSERVED,
                ts="2026-08-20T00:00:00.5+00:00",
                payload={"messages": [{"role": "user", "content": outbound_content}]},
            )
        )
        writer.write(
            FooterRecord(
                section=SECTION_OBSERVED,
                ts="2026-08-20T00:00:01+00:00",
                outcome="passed",
                reason=None,
                duration_ms=1.0,
                upstream_duration_ms=1.0,
                injected={},
            )
        )


def test_main_reports_clean_on_a_fully_blindfolded_capture_directory(tmp_path, capsys):
    brief_path = tmp_path / "brief.md"
    brief_path.write_text(_FIXTURE_BRIEF)

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    _write_fixture_capture(captures_dir / "20260820T000000000000Z-abcd.jsonl", outbound_content="surrogate-name only")

    exit_code = clause_a_sweep.main(["--captures-dir", str(captures_dir), "--brief", str(brief_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "0 real value" in out


def test_main_reports_a_leak_with_capture_and_leaf_path_when_a_seeded_value_egresses(tmp_path, capsys):
    brief_path = tmp_path / "brief.md"
    brief_path.write_text(_FIXTURE_BRIEF)

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    _write_fixture_capture(
        captures_dir / "20260820T000000000000Z-abcd.jsonl", outbound_content="cc Mara Ostrowski"
    )

    exit_code = clause_a_sweep.main(["--captures-dir", str(captures_dir), "--brief", str(brief_path)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "LEAK" in out
    assert "20260820T000000000000Z-abcd.jsonl" in out
    assert "messages[0].content" in out
    # SEC-3: the real value itself must never appear in the printed report.
    assert "Mara Ostrowski" not in out
