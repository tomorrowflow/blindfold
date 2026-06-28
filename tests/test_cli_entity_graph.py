"""CLI entity-graph curation (in-process, no DB): add/list/variation/edit-surrogate.

This is the public interface for cold-start curation without the SPA (issue #8). The CLI
talks to a curation store seam; tests exercise it via an in-process store so the suite
stays hermetic. A Postgres-backed implementation of the same seam — asserted to honour
the same contract — lands with the follow-up Postgres wiring slice (see ``cli.main``).

Leak-audit clauses exercised here:
- A precondition: a minted surrogate is never the real entity value.
- E-stable: a referent's minted surrogate stays the same across re-loads of the same store.
- Edit-surrogate (ADR-0005): editing a surrogate preserves restorability of past
  exchanges — the OLD surrogate still resolves back to the real referent, and the NEW
  surrogate becomes the active one used in subsequent blindfold/restore.
N/A this slice (no request path): A-egress / B / C / D verify pass / F fail-closed.
N/A this slice (stated): G mapping-secrecy — real-value storage is plaintext; Transit
encryption + blind index land in #10 (ADR-0008).
"""

from __future__ import annotations

from blindfold.cli import MemoryEntityGraphStore, run


def test_cli_add_person_persists_a_real_surrogate_pair():
    store = MemoryEntityGraphStore()

    run(["add", "--kind", "person", "--name", "Greta Schmidt"], store=store)

    pairs = dict(store.seeded_pairs())
    assert "Greta Schmidt" in pairs
    surrogate = pairs["Greta Schmidt"]
    # Clause A precondition: the surrogate must never be the real entity value.
    assert surrogate
    assert surrogate != "Greta Schmidt"


def test_cli_list_prints_current_real_to_surrogate_mappings(capsys):
    store = MemoryEntityGraphStore()
    run(["add", "--kind", "person", "--name", "Greta Schmidt"], store=store)
    run(["add", "--kind", "term", "--name", "Projekt Aurora"], store=store)
    capsys.readouterr()  # discard the add-command output

    run(["list"], store=store)

    out = capsys.readouterr().out
    person_surrogate = dict(store.seeded_pairs())["Greta Schmidt"]
    term_surrogate = dict(store.seeded_pairs())["Projekt Aurora"]
    assert "Greta Schmidt" in out
    assert person_surrogate in out
    assert "Projekt Aurora" in out
    assert term_surrogate in out


def test_cli_list_filters_by_kind(capsys):
    store = MemoryEntityGraphStore()
    run(["add", "--kind", "person", "--name", "Greta Schmidt"], store=store)
    run(["add", "--kind", "term", "--name", "Projekt Aurora"], store=store)
    capsys.readouterr()

    run(["list", "--kind", "person"], store=store)

    out = capsys.readouterr().out
    assert "Greta Schmidt" in out
    assert "Projekt Aurora" not in out


def test_cli_variation_registers_alias_against_existing_referent():
    # Curators often add variations later (post-add, e.g. after seeing a new spelling
    # in a request); the dedicated `variation` subcommand is the entry point for that.
    store = MemoryEntityGraphStore()
    run(["add", "--kind", "person", "--name", "Greta Schmidt"], store=store)

    run(
        ["variation", "--kind", "person", "--name", "Greta Schmidt", "--value", "Greta"],
        store=store,
    )

    pairs = dict(store.seeded_pairs())
    assert pairs["Greta"] == pairs["Greta Schmidt"]


def test_cli_edit_surrogate_drives_subsequent_blindfold_and_restore():
    # Changes are reflected in subsequent blindfold/restore behavior: the NEW surrogate
    # is what egresses, and the new restore reverses it back to the real entity.
    from blindfold.engine import blindfold_payload, restore_response
    from blindfold.surrogates import SurrogateMapping

    store = MemoryEntityGraphStore()
    run(["add", "--kind", "person", "--name", "Greta Schmidt"], store=store)

    run(
        [
            "edit-surrogate",
            "--kind", "person",
            "--name", "Greta Schmidt",
            "--to", "Hannah Becker",
        ],
        store=store,
    )

    mapping = SurrogateMapping.from_pairs(store.seeded_pairs())
    payload = {"messages": [{"role": "user", "content": "Hello Greta Schmidt."}]}

    blinded, session = blindfold_payload(payload, mapping)
    egressed_text = blinded["messages"][0]["content"]
    # Subsequent blindfold uses the NEW surrogate.
    assert "Hannah Becker" in egressed_text
    assert "Greta Schmidt" not in egressed_text

    # Subsequent restore reverses the new surrogate back to the real value.
    response = {"content": [{"type": "text", "text": "Hello Hannah Becker."}]}
    restored = restore_response(response, session)
    assert restored["content"][0]["text"] == "Hello Greta Schmidt."


def test_cli_edit_surrogate_preserves_restorability_of_past_exchanges():
    # ADR-0005: editing a surrogate must preserve restorability of past exchanges.
    # A past exchange already egressed and was logged with the OLD surrogate; the
    # store must still know what real referent that old surrogate stood for, so
    # the past exchange remains re-identifiable (audited re-identification per
    # ADR-0007). At the seam, this means seeded_pairs() retains the (real,
    # old_surrogate) record alongside the new active pair.
    from blindfold.surrogates import SurrogateMapping

    store = MemoryEntityGraphStore()
    run(["add", "--kind", "person", "--name", "Greta Schmidt"], store=store)
    old_surrogate = dict(store.seeded_pairs())["Greta Schmidt"]
    assert old_surrogate != "Hannah Becker"

    run(
        [
            "edit-surrogate",
            "--kind", "person",
            "--name", "Greta Schmidt",
            "--to", "Hannah Becker",
        ],
        store=store,
    )

    pairs_after = store.seeded_pairs()
    # The store retains the historical (real -> old_surrogate) record AND has the
    # new (real -> new_surrogate) record.
    assert ("Greta Schmidt", old_surrogate) in pairs_after
    assert ("Greta Schmidt", "Hannah Becker") in pairs_after

    # Built into a SurrogateMapping (last-write-wins), the active surrogate is the new
    # one so subsequent blindfold uses it.
    mapping = SurrogateMapping.from_pairs(pairs_after)
    assert mapping.surrogate_for("Greta Schmidt") == "Hannah Becker"


def test_cli_add_with_variations_records_coreference_aliases():
    # ADR-0004 coreference: every variation maps to its referent's single surrogate.
    store = MemoryEntityGraphStore()

    run(
        [
            "add", "--kind", "person", "--name", "Greta Schmidt",
            "--variation", "Greta", "--variation", "Schmidt",
        ],
        store=store,
    )

    pairs = dict(store.seeded_pairs())
    canonical_surrogate = pairs["Greta Schmidt"]
    for variation in ("Greta", "Schmidt"):
        assert pairs[variation] == canonical_surrogate
        # Clause A precondition holds for variations too.
        assert pairs[variation] != variation
