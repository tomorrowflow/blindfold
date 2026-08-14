"""``blindfold explain`` reads the operator's real entity graph, read-only --
never the vendored seed by default (ADR-0047 §6, issue #269): validating
against Sample data answers the wrong question. ``main()`` wires the same real
SQLite-backed store ``blindfold.app`` reads from (mapping-cipher-wired), and a
replay run must leave that store's entities byte-identical -- asserted against
a second, independently-opened instance of the same store, not assumed.
"""

import base64
import os

import pytest

pytest.importorskip("rich")

from blindfold_devtools.cli import main  # noqa: E402


def _make_store_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_explain_uses_the_real_entity_graph_not_the_vendored_seed(tmp_path, monkeypatch):
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    db_path = tmp_path / "entity_graph.sqlite3"
    dsn = f"sqlite:///{db_path}"
    store_key = _make_store_key()
    cipher = LocalKeyCipher(store_key)

    store = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store.add_entity(
        kind="person",
        workspace="default",
        canonical_name="Zdenka Priborsky",
        variations=[],
        surrogate="Fictional-Person-001",
    )
    before = store.list_entities("default")

    from blindfold.app import get_review_inbox

    review_inbox_before = list(get_review_inbox().list())

    monkeypatch.delenv("BLINDFOLD_OPENBAO_TOKEN", raising=False)
    monkeypatch.setenv("BLINDFOLD_DATABASE_URL", dsn)
    monkeypatch.setenv("BLINDFOLD_STORE_KEY", store_key)
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("BLINDFOLD_EXCHANGE_CAPTURE_DIR", str(capture_dir))
    text_file = tmp_path / "prompt.txt"
    text_file.write_text("Hi Zdenka Priborsky")

    exit_code = main(["explain", "--text", str(text_file)])

    assert exit_code == 0
    created = list(capture_dir.glob("*.jsonl"))
    assert len(created) == 1

    from blindfold_devtools.capture import read_capture
    from blindfold_devtools.capture import OutboundRecord

    capture = read_capture(created[0])
    outbound = next(r for r in capture.records if isinstance(r, OutboundRecord))
    assert outbound.payload["messages"][0]["content"] == "Hi Fictional-Person-001"

    # Byte-identical: re-open the SAME store independently and confirm no growth.
    after = PostgresEntityGraphStore(dsn, mapping_cipher=cipher).list_entities("default")
    assert [(e.canonical_name, e.active_surrogate, e.variations) for e in after] == [
        (e.canonical_name, e.active_surrogate, e.variations) for e in before
    ]

    # The real (process-wide) review inbox is untouched -- inbox=None, always.
    assert list(get_review_inbox().list()) == review_inbox_before


def test_explain_with_a_wired_l3_detector_still_leaves_the_store_and_inbox_untouched(
    tmp_path, monkeypatch
):
    """Issue #274: with route (a) landed, a wired L3 detector actually adjudicates
    during ``explain`` (``inbox=None`` no longer blocks it from running at all) --
    so the byte-identical guarantee above can no longer be trusted on the strength
    of L3 never having run at all. Stubs an always-confirming adjudicator (the
    network boundary L3Detector itself defines, no real Ollama) so a genuinely
    novel candidate IS confirmed and minted, then re-asserts the same
    byte-identical/inbox-untouched properties against that harder case -- the
    load-bearing new property issue #274 itself names: an L3 mint under
    ``inbox=None`` must be provably incapable of reaching the inbox, the entity
    graph, or any store.
    """
    from blindfold.l3 import L3Adjudication, L3Detector
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    class _AlwaysConfirmAdjudicator:
        def adjudicate(self, candidate):
            return L3Adjudication(is_entity=True)

    monkeypatch.setattr(
        "blindfold_devtools.cli._operator_l3_detector",
        lambda settings: L3Detector(_AlwaysConfirmAdjudicator()),
    )

    db_path = tmp_path / "entity_graph.sqlite3"
    dsn = f"sqlite:///{db_path}"
    store_key = _make_store_key()
    cipher = LocalKeyCipher(store_key)

    store = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    before = store.list_entities("default")

    from blindfold.app import get_review_inbox

    review_inbox_before = list(get_review_inbox().list())

    monkeypatch.delenv("BLINDFOLD_OPENBAO_TOKEN", raising=False)
    monkeypatch.setenv("BLINDFOLD_DATABASE_URL", dsn)
    monkeypatch.setenv("BLINDFOLD_STORE_KEY", store_key)
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("BLINDFOLD_EXCHANGE_CAPTURE_DIR", str(capture_dir))
    text_file = tmp_path / "prompt.txt"
    text_file.write_text("Hi Completely Novel Person")

    exit_code = main(["explain", "--text", str(text_file)])

    assert exit_code == 0
    created = list(capture_dir.glob("*.jsonl"))
    assert len(created) == 1

    from blindfold_devtools.capture import OutboundRecord, read_capture

    capture = read_capture(created[0])
    outbound = next(r for r in capture.records if isinstance(r, OutboundRecord))
    # L3 actually ran and minted: the real value is gone from the outbound payload.
    assert "Completely Novel Person" not in outbound.payload["messages"][0]["content"]

    # Byte-identical: re-open the SAME store independently and confirm no growth.
    after = PostgresEntityGraphStore(dsn, mapping_cipher=cipher).list_entities("default")
    assert [(e.canonical_name, e.active_surrogate, e.variations) for e in after] == [
        (e.canonical_name, e.active_surrogate, e.variations) for e in before
    ]

    # The real (process-wide) review inbox is untouched -- inbox=None, always.
    assert list(get_review_inbox().list()) == review_inbox_before
