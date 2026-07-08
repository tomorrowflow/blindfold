"""Leak audit over a pinned PUPA subset: real-world prompt shapes we didn't invent.

Issue #77. Today the leak audit runs on synthetic/seeded fixtures (test_proxy_round_trip.py
etc.); this module adds breadth from **PUPA** (Columbia-NLP/PUPA on HuggingFace, MIT
licensed), the real user-LLM-interaction benchmark from the PAPILLON paper
(arXiv:2410.17127). The vendored subset (``tests/fixtures/pupa_subset.json``) is pinned to
one HuggingFace revision (sha in the fixture's ``_provenance``) with license, source, and
selection methodology recorded there — see that file for full provenance.

Entities to protect are seeded per record from the corpus's own ``pii_units`` annotations
(extracted at vendoring time — see the fixture's ``selection_methodology``), so the leak
audit is meaningful, not vacuous: each record drives at least one real entity through
``blindfold_payload`` -> stubbed provider -> ``restore_response``, at the engine seam
(the same functions the proxy calls; ADR-0020).

Leak-audit clauses covered:
- A: no seeded real entity value crosses egress, over the WHOLE bounded/full corpus.
- B: the client receives fully restored real values.
- C: closed-world restore, proven with one dedicated cross-record case (a surrogate from
  another record's session is not restored).
- D: verify pass (leak_gate pre-egress + resolution_gate post-restore) is clean on every
  record.

N/A this slice (stated explicitly, per the leak-audit skill):
- E surrogate invariants (reserved-namespace PII, coherent world): the corpus seeds L2
  dictionary entities (names/orgs/terms), not L1 PII or relationship graphs; those
  invariants are proven generically by test_leak_and_resolution_gates.py /
  test_proxy_round_trip.py and unaffected by corpus breadth.
- F fail-closed: this module calls ``blindfold_payload`` directly with no ``l3_detector``,
  the same as every other engine-level (non-HTTP) test in this repo — L3/fail-closed
  policy is app-layer (app.py's ``_mint_or_block``) and is exercised by
  test_proxy_fail_closed.py, orthogonal to corpus breadth.
- G mapping secrecy: the in-memory mapping here is plaintext by design this slice
  (issue #3/#10, ADR-0008 deferral), same as every other engine-level test.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from blindfold.engine import (
    ExchangeSession,
    blindfold_payload,
    leak_gate,
    resolution_gate,
    restore_response,
)
from blindfold.surrogates import SurrogateMapping

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pupa_subset.json"
# Bounded default so the suite stays CI-fast (acceptance criterion #3); the full pinned
# subset runs opt-in via BLINDFOLD_FULL_PUPA_CORPUS=1 (see the marker below).
_BOUNDED_RECORD_COUNT = 10
_FULL_CORPUS_ENV_VAR = "BLINDFOLD_FULL_PUPA_CORPUS"


def _load_pupa_fixture() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _pupa_records(limit: int | None = None) -> list[dict[str, Any]]:
    records = _load_pupa_fixture()["records"]
    return records if limit is None else records[:limit]


def _mapping_for_record(record: dict[str, Any]) -> SurrogateMapping:
    """Seed one SurrogateMapping per record from its extracted entity list.

    Surrogates are synthetic tokens salted with the record's ``conversation_hash`` so
    two different records never mint the same surrogate string — realism/coherent-world
    isn't the property under test here (clause E is N/A, see module docstring), but
    cross-record uniqueness is exactly what the closed-world restore test needs.
    """
    mapping = SurrogateMapping()
    salt = record["conversation_hash"][:8]
    entities = sorted(set(record["entities"]), key=str.lower)
    for index, entity in enumerate(entities):
        mapping.seed(entity, f"PupaSurrogate-{salt}-{index:03d}")
    return mapping


def _blindfold_and_round_trip(
    record: dict[str, Any],
) -> tuple[dict[str, Any], SurrogateMapping, ExchangeSession, dict[str, Any]]:
    """Run one PUPA record through blindfold -> stub provider -> restore.

    The stub provider "response" simply echoes every surrogate injected for this
    exchange (mirrors the scripted_response pattern in test_proxy_round_trip.py) —
    the most direct way to prove restore reverses exactly what blindfold injected.
    """
    mapping = _mapping_for_record(record)
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": record["user_query"]}],
    }
    blinded, session = blindfold_payload(payload, mapping)
    leak_gate(blinded, mapping)  # Clause A: raises if a real value would egress.
    scripted_response = {
        "content": [{"type": "text", "text": " ".join(session.injected.keys())}]
    }
    restored = restore_response(scripted_response, session)
    resolution_gate(restored, session)  # Clause D: raises if a surrogate is unresolved.
    return blinded, mapping, session, restored


def test_pupa_subset_fixture_has_pinned_provenance_and_mit_license():
    """The vendored corpus records exactly what it is, where it came from, and how it
    was selected — reproducible against one pinned HuggingFace revision (acceptance
    criterion #1)."""
    fixture = _load_pupa_fixture()
    provenance = fixture["_provenance"]

    assert provenance["dataset"] == "Columbia-NLP/PUPA"
    assert "MIT" in provenance["license"]
    assert provenance["huggingface_revision_sha"]
    assert provenance["source_file_sha256"]
    assert "2410.17127" in provenance["paper"]  # the PAPILLON arXiv id
    assert provenance["record_count"] == len(fixture["records"])
    # Bounded so CI stays fast, per acceptance criterion #3.
    assert len(fixture["records"]) < 100


def test_pupa_subset_entities_are_literal_substrings_of_their_user_query():
    """Every seeded entity actually occurs in its record's prompt (case-insensitive) —
    guards against a vendoring/extraction bug that would make the audit vacuous."""
    for record in _pupa_records():
        assert record["entities"], (
            f"record {record['conversation_hash']} has no entities to protect — "
            "the leak audit would be vacuous for it"
        )
        for entity in record["entities"]:
            assert entity.lower() in record["user_query"].lower(), (
                f"entity {entity!r} not found in record "
                f"{record['conversation_hash']}'s user_query"
            )


def _assert_record_round_trip_is_leak_clean(record: dict[str, Any]) -> None:
    """Assert clauses A, B, and D for one PUPA record's blindfold -> restore round trip.

    Shared by the bounded and full-corpus tests so the leak-audit assertions live in
    exactly one place. Clause D (clean verify pass) is enforced inside
    ``_blindfold_and_round_trip`` via ``leak_gate``/``resolution_gate``.
    """
    blinded, mapping, session, restored = _blindfold_and_round_trip(record)

    # Clause A: no real entity value is present in what was about to egress.
    blinded_text = json.dumps(blinded, ensure_ascii=False)
    for real in mapping.real_values():
        assert real not in blinded_text, (
            f"record {record['conversation_hash']}: real entity {real!r} "
            "present in the blindfolded outbound payload"
        )

    # Clause B: the client-visible restored response carries the real values back,
    # not the surrogates.
    restored_text = json.dumps(restored, ensure_ascii=False)
    for surrogate, real in session.injected.items():
        assert surrogate not in restored_text, (
            f"record {record['conversation_hash']}: surrogate {surrogate!r} "
            "still present after restore"
        )
        assert real in restored_text, (
            f"record {record['conversation_hash']}: real value {real!r} "
            "missing from the restored client response"
        )


def test_pupa_bounded_subset_round_trip_blindfolds_every_record_with_no_leak():
    """The primary corpus property (acceptance criterion #2), bounded for CI speed:
    over the first {_BOUNDED_RECORD_COUNT} PUPA records, every seeded real entity is
    blindfolded before egress (A), the client gets it back fully restored (B), and the
    verify pass is clean on every single record (D)."""
    records = _pupa_records(_BOUNDED_RECORD_COUNT)
    assert len(records) == _BOUNDED_RECORD_COUNT

    for record in records:
        _assert_record_round_trip_is_leak_clean(record)


def _full_corpus_skip_reason() -> str:
    return (
        f"full PUPA corpus run is opt-in — set {_FULL_CORPUS_ENV_VAR}=1 to run all "
        "vendored records (acceptance criterion #3)"
    )


@pytest.mark.full_pupa_corpus
@pytest.mark.skipif(
    not os.environ.get(_FULL_CORPUS_ENV_VAR), reason=_full_corpus_skip_reason()
)
def test_pupa_full_corpus_round_trip_is_opt_in_and_leak_clean():
    """Same property as the bounded test, over the WHOLE vendored subset. Opt-in via
    BLINDFOLD_FULL_PUPA_CORPUS=1 (env var) or ``pytest -m full_pupa_corpus``, so the
    default CI run stays fast (acceptance criterion #3) while the full pinned corpus
    is still exercisable on demand."""
    records = _pupa_records()
    assert len(records) > _BOUNDED_RECORD_COUNT  # actually exercises more than the default

    for record in records:
        _assert_record_round_trip_is_leak_clean(record)


def test_pupa_corpus_restore_is_closed_world_for_a_cross_record_surrogate():
    """Clause C over real corpus data: a surrogate minted for a DIFFERENT record's
    exchange is not restored just because its literal text shows up in this record's
    response — restore only reverses what THIS session actually injected."""
    records = _pupa_records(2)
    this_record, other_record = records[0], records[1]

    _, _, session, _ = _blindfold_and_round_trip(this_record)
    other_mapping = _mapping_for_record(other_record)
    foreign_surrogate = next(iter(other_mapping.known_surrogates()))
    foreign_real = other_mapping.real_values()[0]
    assert foreign_surrogate not in session.injected  # never injected THIS exchange

    provider_response = {
        "content": [
            {
                "type": "text",
                "text": f"Unrelated mention of {foreign_surrogate} appeared.",
            }
        ]
    }
    restored = restore_response(provider_response, session)
    restored_text = restored["content"][0]["text"]

    # The out-of-world surrogate is left untouched (not restored to the other record's
    # real value) — closed-world restore.
    assert foreign_surrogate in restored_text
    assert foreign_real not in restored_text
