"""``blindfold explain`` replay via the CLI (ADR-0047 §6, issue #269): a bare
payload/``--text`` input replays into a brand-new Exchange capture; an
existing capture id/``--last`` replays its own ``observed`` inbound payload
and appends the ``reconstructed`` section onto that same capture -- the two
meanings of ``explain <id>`` (#257's "render" and #255/#269's "replay")
reconciled by having replay populate what render always needed and was
waiting for (closes the loop with #257, AC6).
"""

import io
import json

import pytest

pytest.importorskip("rich")

from blindfold.detection import Entity  # noqa: E402
from blindfold.surrogates import SurrogateMapping  # noqa: E402
from blindfold_devtools.capture import (  # noqa: E402
    SECTION_OBSERVED,
    SECTION_RECONSTRUCTED,
    CaptureWriter,
    DetectionRecord,
    FooterRecord,
    HeaderRecord,
    read_capture,
)
from blindfold_devtools.capture_directory import CAPTURE_SUFFIX
from blindfold_devtools.cli import run  # noqa: E402


def _mapping():
    return SurrogateMapping.from_pairs([("Martin Bach", "Bernhard Vogt")])


def _graph():
    return [Entity(canonical="Martin Bach", variations=(), surrogate="Bernhard Vogt")]


def test_explain_text_replays_into_a_brand_new_capture_and_renders_it(tmp_path):
    text_file = tmp_path / "prompt.txt"
    text_file.write_text("Hi Martin Bach")
    out = io.StringIO()

    exit_code = run(
        ["explain", "--text", str(text_file)],
        capture_dir=tmp_path,
        mapping=_mapping(),
        graph_entities=_graph(),
        out=out,
    )

    assert exit_code == 0
    rendered = out.getvalue()
    assert "reconstructed" in rendered
    assert "exact" in rendered

    created = list(tmp_path.glob(f"*{CAPTURE_SUFFIX}"))
    assert len(created) == 1
    capture = read_capture(created[0])
    assert any(isinstance(r, DetectionRecord) and r.section == SECTION_RECONSTRUCTED for r in capture.records)
    footer = next(r for r in capture.records if isinstance(r, FooterRecord))
    # Nothing egressed -- no provider-response fields (no ProviderChunkRecord),
    # asserted structurally, not just by the no-egress transport test elsewhere.
    assert not any(r.record == "provider_chunk" for r in capture.records)
    assert footer.injected == {"Bernhard Vogt": "Martin Bach"}


def test_explain_payload_accepts_a_chat_completions_dialect_file(tmp_path):
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps({
        "messages": [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "Hi Martin Bach"},
        ]
    }))
    out = io.StringIO()

    exit_code = run(
        ["explain", "--payload", str(payload_file)],
        capture_dir=tmp_path,
        mapping=_mapping(),
        graph_entities=_graph(),
        out=out,
    )

    assert exit_code == 0
    created = list(tmp_path.glob(f"*{CAPTURE_SUFFIX}"))
    capture = read_capture(created[0])
    header = next(r for r in capture.records if isinstance(r, HeaderRecord))
    assert header.endpoint == "chat_completions"


def test_messages_chat_completions_and_text_inputs_produce_the_same_artifact_shape(tmp_path):
    def _record_shape(capture_dir):
        created = list(capture_dir.glob(f"*{CAPTURE_SUFFIX}"))
        assert len(created) == 1
        capture = read_capture(created[0])
        for p in created:
            p.unlink()
        return sorted({r.record for r in capture.records})

    messages_dir = tmp_path / "messages"
    messages_dir.mkdir()
    messages_file = messages_dir / "payload.json"
    messages_file.write_text(json.dumps(
        {"messages": [{"role": "user", "content": "Hi Martin Bach"}]}
    ))
    run(
        ["explain", "--payload", str(messages_file)],
        capture_dir=messages_dir, mapping=_mapping(), graph_entities=_graph(), out=io.StringIO(),
    )
    messages_shape = _record_shape(messages_dir)

    chat_dir = tmp_path / "chat"
    chat_dir.mkdir()
    chat_file = chat_dir / "payload.json"
    chat_file.write_text(json.dumps(
        {"messages": [{"role": "system", "content": "be helpful"}, {"role": "user", "content": "Hi Martin Bach"}]}
    ))
    run(
        ["explain", "--payload", str(chat_file)],
        capture_dir=chat_dir, mapping=_mapping(), graph_entities=_graph(), out=io.StringIO(),
    )
    chat_shape = _record_shape(chat_dir)

    text_dir = tmp_path / "text"
    text_dir.mkdir()
    text_file = text_dir / "prompt.txt"
    text_file.write_text("Hi Martin Bach")
    run(
        ["explain", "--text", str(text_file)],
        capture_dir=text_dir, mapping=_mapping(), graph_entities=_graph(), out=io.StringIO(),
    )
    text_shape = _record_shape(text_dir)

    assert messages_shape == chat_shape == text_shape
    assert "header" in messages_shape and "outbound" in messages_shape
    assert "detection" in messages_shape and "footer" in messages_shape
    assert "provider_chunk" not in messages_shape


def test_explain_by_id_replays_the_captures_own_inbound_payload_and_appends_reconstructed(tmp_path):
    capture_id = "20260812T000000000000Z-aaaa"
    path = tmp_path / f"{capture_id}{CAPTURE_SUFFIX}"
    writer = CaptureWriter(path)
    writer.write(HeaderRecord(
        section=SECTION_OBSERVED,
        ts="2026-08-12T00:00:00+00:00",
        capture_id=capture_id,
        endpoint="messages",
        streamed=False,
        workspace="default",
        inbound_payload={"messages": [{"role": "user", "content": "Hi Martin Bach"}]},
    ))
    writer.write(FooterRecord(
        section=SECTION_OBSERVED,
        ts="2026-08-12T00:00:01+00:00",
        outcome="passed",
        reason=None,
        duration_ms=1.0,
        upstream_duration_ms=1.0,
        injected={"Bernhard Vogt": "Martin Bach"},
    ))
    writer.close()
    out = io.StringIO()

    exit_code = run(
        ["explain", capture_id],
        capture_dir=tmp_path,
        mapping=_mapping(),
        graph_entities=_graph(),
        out=out,
    )

    assert exit_code == 0
    rendered = out.getvalue()
    assert "reconstructed" in rendered

    capture = read_capture(path)
    reconstructed = [
        r for r in capture.records if isinstance(r, DetectionRecord) and r.section == SECTION_RECONSTRUCTED
    ]
    assert len(reconstructed) == 1
    assert reconstructed[0].offsets == ((3, 14),)


def test_explaining_the_same_id_twice_does_not_duplicate_reconstructed_records(tmp_path):
    capture_id = "20260812T000000000000Z-bbbb"
    path = tmp_path / f"{capture_id}{CAPTURE_SUFFIX}"
    writer = CaptureWriter(path)
    writer.write(HeaderRecord(
        section=SECTION_OBSERVED,
        ts="2026-08-12T00:00:00+00:00",
        capture_id=capture_id,
        endpoint="messages",
        streamed=False,
        workspace="default",
        inbound_payload={"messages": [{"role": "user", "content": "Hi Martin Bach"}]},
    ))
    writer.write(FooterRecord(
        section=SECTION_OBSERVED,
        ts="2026-08-12T00:00:01+00:00",
        outcome="passed",
        reason=None,
        duration_ms=1.0,
        upstream_duration_ms=1.0,
        injected={"Bernhard Vogt": "Martin Bach"},
    ))
    writer.close()

    for _ in range(2):
        run(
            ["explain", capture_id],
            capture_dir=tmp_path,
            mapping=_mapping(),
            graph_entities=_graph(),
            out=io.StringIO(),
        )

    capture = read_capture(path)
    reconstructed = [
        r for r in capture.records if isinstance(r, DetectionRecord) and r.section == SECTION_RECONSTRUCTED
    ]
    assert len(reconstructed) == 1
