"""``blindfold captures`` / ``blindfold explain`` CLI (ADR-0047 §6, issue #257).

``run()`` takes an already-resolved capture directory / mapping / graph so it
is testable without touching the real store or environment (mirrors
``blindfold.cli``'s own ``run(argv, *, store)`` seam) -- ``main()`` is the
thin wiring that resolves those from the real environment (settings,
BLINDFOLD_EXCHANGE_CAPTURE_DIR, the shared-store refusal).
"""

import io

import pytest

# blindfold_devtools.cli imports rich at module scope, and rich lives only in the
# `devtools` dependency group (ADR-0047 §2: source-run-only, never the wheel or the
# frozen binary) -- a default `uv sync` uninstalls it. Without this guard the
# unconditional import below raises ModuleNotFoundError during *collection*, which
# Interrupts the whole pytest session: the canonical `uv run pytest` exits 2 with
# zero tests run, so every leak-audit assertion in the repo silently never executes.
# Mirrors the "PyInstaller not installed" skip in test_absence_gate.py -- a skip here
# means these CLI tests have not run, not that they passed; `uv run --group devtools
# pytest` exercises them.
pytest.importorskip("rich")

from blindfold.detection import Entity  # noqa: E402
from blindfold.surrogates import SurrogateMapping  # noqa: E402
from blindfold_devtools.capture import (  # noqa: E402
    SECTION_OBSERVED,
    CaptureWriter,
    FooterRecord,
    HeaderRecord,
)
from blindfold_devtools.cli import main, run  # noqa: E402


def _write_header_only(path, *, capture_id: str) -> None:
    writer = CaptureWriter(path)
    writer.write(
        HeaderRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:00+00:00",
            capture_id=capture_id,
            endpoint="messages",
            streamed=False,
            workspace="default",
            inbound_payload={"messages": [{"role": "user", "content": "Hi Martin Bach"}]},
        )
    )
    writer.close()


def test_captures_command_lists_an_in_flight_capture(tmp_path):
    _write_header_only(tmp_path / "20260812T000000000000Z-aaaa.jsonl", capture_id="20260812T000000000000Z-aaaa")
    out = io.StringIO()

    exit_code = run(
        ["captures"], capture_dir=tmp_path, mapping=SurrogateMapping(), graph_entities=[], out=out
    )

    assert exit_code == 0
    rendered = out.getvalue()
    assert "20260812T000000000000Z-aaaa" in rendered
    assert "in-flight" in rendered


def test_explain_last_renders_the_most_recent_capture(tmp_path):
    _write_header_only(tmp_path / "20260812T000000000000Z-aaaa.jsonl", capture_id="20260812T000000000000Z-aaaa")
    path = tmp_path / "20260812T000000010000Z-bbbb.jsonl"
    writer = CaptureWriter(path)
    writer.write(
        HeaderRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:01+00:00",
            capture_id="20260812T000000010000Z-bbbb",
            endpoint="messages",
            streamed=False,
            workspace="default",
            inbound_payload={"messages": [{"role": "user", "content": "Hi"}]},
        )
    )
    writer.write(
        FooterRecord(
            section=SECTION_OBSERVED,
            ts="2026-08-12T00:00:02+00:00",
            outcome="passed",
            reason=None,
            duration_ms=1.0,
            upstream_duration_ms=1.0,
            injected={},
        )
    )
    writer.close()
    out = io.StringIO()

    exit_code = run(
        ["explain", "--last"],
        capture_dir=tmp_path,
        mapping=SurrogateMapping(),
        graph_entities=[],
        out=out,
    )

    assert exit_code == 0
    assert "20260812T000000010000Z-bbbb" in out.getvalue()
    assert "20260812T000000000000Z-aaaa" not in out.getvalue()


def test_explain_with_an_unknown_id_reports_the_error_and_exits_nonzero(tmp_path):
    out = io.StringIO()

    exit_code = run(
        ["explain", "does-not-exist"],
        capture_dir=tmp_path,
        mapping=SurrogateMapping(),
        graph_entities=[],
        out=out,
    )

    assert exit_code == 1
    assert "does-not-exist" in out.getvalue()


def test_main_wires_the_real_environment_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BLINDFOLD_DATABASE_URL", raising=False)
    monkeypatch.delenv("BLINDFOLD_OPENBAO_TOKEN", raising=False)
    monkeypatch.setenv("BLINDFOLD_EXCHANGE_CAPTURE_DIR", str(tmp_path))
    _write_header_only(tmp_path / "20260812T000000000000Z-aaaa.jsonl", capture_id="20260812T000000000000Z-aaaa")

    exit_code = main(["captures"])

    assert exit_code == 0
    assert "20260812T000000000000Z-aaaa" in capsys.readouterr().out


def test_main_refuses_with_no_capture_directory_configured(monkeypatch, capsys):
    monkeypatch.delenv("BLINDFOLD_DATABASE_URL", raising=False)
    monkeypatch.delenv("BLINDFOLD_OPENBAO_TOKEN", raising=False)
    monkeypatch.delenv("BLINDFOLD_EXCHANGE_CAPTURE_DIR", raising=False)

    exit_code = main(["captures"])

    assert exit_code == 1
    assert "BLINDFOLD_EXCHANGE_CAPTURE_DIR" in capsys.readouterr().err
