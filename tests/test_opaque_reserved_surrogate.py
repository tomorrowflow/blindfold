"""ADR-0052 / issue #330: past provisional-pool exhaustion, the fallback surrogate
is an opaque reserved-namespace token (``BFX0000``, ``BFX0001``, ...), not the
natural-language ``"Provisional Surrogate {N}"`` label -- and a candidate real
matching that reserved form is never minted.

Root cause (#328, ADR-0052): the old fallback's own words ("Provisional",
"Surrogate") are ordinary project vocabulary, so it collided with a `CONTEXT.md`
glossary headword minted as a provisional real (#74 run 8) -- and the collision
was unfixable because the fallback is unreachable by the blinder by construction
(collect-then-apply, #325) and un-scannable a second time (#68/#292's own
injected-surrogate guard). The fix closes the collision class rather than
adjudicating it: the fallback is opaque (no natural-language word, no
free-standing integer, no whitespace/separator) and its own syntactic form is
reserved against ever being minted as a real.

Leak-audit clauses:
- A: proven directly by the run-8-inverted regression -- both the ordinary-prose
  occurrence and the fallback-label occurrence are absent from the blinded
  payload / present only as their surrogates, and leak_gate passes.
- D: the verify pass (leak_gate) stays clean on exactly the payload shape that
  used to 503 deterministically.
N/A this slice: B/C (restore/closed-world semantics unchanged besides the
opaque shape itself, reproven by the utf-8 regression), F (fail-closed policy
untouched), G (mapping secrecy, unrelated).
"""

from __future__ import annotations

import pytest

from blindfold import l3 as l3_module
from blindfold.engine import ExchangeSession, blindfold_payload, leak_gate, restore_response
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import _PROVISIONAL_POOL, ReviewInbox, _next_provisional
from blindfold.surrogates import SurrogateMapping

# Issue #338 enlarged review._PROVISIONAL_POOL from 8 to 32 entries, position-stable --
# these tests fill the WHOLE named pool (whatever its current size) to reach the
# opaque BFX fallback, rather than a hardcoded 8.
_POOL_SIZE = len(_PROVISIONAL_POOL)


def test_provisional_pool_exhaustion_falls_back_to_an_opaque_reserved_token():
    # AC1: past the named pool, the fallback is `^BFX\d{4,}$` -- no
    # natural-language word, no whitespace, no separator.
    inbox = ReviewInbox()
    for i in range(_POOL_SIZE):
        inbox.upsert(f"Filler Person {i}", context=f"...Filler Person {i}...", entity_type="person")

    item = inbox.upsert("Referent7", context="...Referent7 called...", entity_type="person")

    assert item.provisional_surrogate == f"BFX{_POOL_SIZE:04d}"


def test_a_reserved_form_candidate_real_is_never_minted():
    # AC2 regression: offering "BFX0008" as an L3-confirmed novel candidate --
    # e.g. Blindfold's own documentation, read back through the proxy -- must
    # produce no inbox row, closing the namespace by pattern match rather than
    # adjudicating the collision after the fact.
    inbox = ReviewInbox()

    item = inbox.upsert(
        "BFX0008", context="...BFX0008 appears in the doc...", entity_type="person"
    )

    assert item is None
    assert inbox.list() == []


class _ConfirmReservedFormToken:
    """Confirms the reserved-form token as a fresh person candidate -- stands
    in for L3 mistaking Blindfold's own documentation shape for a novel
    referent, the same way run 8's own vocabulary was mistaken for one.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text != "BFX0008":
            return L3Adjudication(is_entity=False)
        return L3Adjudication(is_entity=True, entity_type="person")


def test_request_path_never_crashes_or_mints_on_a_reserved_form_candidate(monkeypatch):
    # The engine's own mint call site (the seam ``ReviewInbox.upsert`` is
    # called from) must treat a refused mint as "nothing to blind here", not
    # crash on a ``None`` item -- reading Blindfold's own docs through the
    # proxy must round-trip cleanly, never 500. "BFX0008" itself can never
    # reach L3 as a real candidate through today's capitalized-token selector
    # (it isn't alphabetic) -- the guard is still asserted at the engine seam
    # directly, by forcing exactly that candidate through, as defense against
    # a future detector (e.g. a semantic/GLiNER cascade) that isn't limited to
    # that same alpha-only shape.
    text = "The reserved form is BFX0008, see the ADR."

    def _fake_select_candidate_spans(text_arg, known_entities, allowlist=None,
                                      declared_tools=frozenset(),
                                      system_confined_tokens=frozenset(),
                                      case_inconsistency=None):
        start = text_arg.find("BFX0008")
        end = start + len("BFX0008")
        return [
            CandidateSpan(
                text="BFX0008", start=start, end=end,
                context=text_arg, context_offset=start,
            )
        ]

    monkeypatch.setattr(l3_module, "select_candidate_spans", _fake_select_candidate_spans)

    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmReservedFormToken())
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": text}],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    assert inbox.list() == []
    assert blinded["messages"][0]["content"] == text


def test_run8_deadlock_inverted_prose_and_fallback_label_both_blind_and_leak_gate_passes():
    # AC3 + AC4 (ADR-0052 worked example, #328's own repro inverted): row 1 is a
    # real ("Surrogate", a CONTEXT.md glossary headword -- run 8's own trigger)
    # minted as a provisional person, drawing a plausible pool name. The rest of
    # the rows fill out the named pool. The next row is a second real
    # ("Referent7") minted past pool exhaustion, drawing the new opaque
    # fallback. A payload mentioning BOTH "Surrogate" in ordinary prose and
    # "Referent7" must blind both occurrences and pass leak_gate cleanly --
    # today (pre-fix) the fallback label itself would carry the word
    # "Surrogate" back into the payload and leak_gate would raise.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()

    surrogate_item = inbox.upsert(
        "Surrogate", context="...the Surrogate glossary entry...", entity_type="person"
    )
    assert surrogate_item.provisional_surrogate == "Alex Brenner"  # pool position 0

    for i in range(_POOL_SIZE - 1):
        inbox.upsert(f"Filler Person {i}", context=f"...Filler Person {i}...", entity_type="person")

    referent_item = inbox.upsert(
        "Referent7", context="...summarise Referent7...", entity_type="person"
    )
    assert referent_item.provisional_surrogate == f"BFX{_POOL_SIZE:04d}"

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": "Read the Surrogate glossary entry, then summarise Referent7.",
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, None, inbox)

    text = blinded["messages"][0]["content"]
    # AC4: the ordinary-prose occurrence is still blinded, not narrowed away.
    assert "Surrogate" not in text
    assert "Alex Brenner" in text
    # AC1/AC3: the fallback label carries no natural-language word to collide.
    assert f"BFX{_POOL_SIZE:04d}" in text

    # AC3/AC7 (leak-audit clause D): the verify pass is clean.
    leak_gate(blinded, mapping, inbox)


def test_opaque_fallback_admits_no_pass2_restore_key_and_utf8_survives_restore():
    # AC5 (#286's root case, now removed at the root rather than patched): the
    # fallback surrogate is whitespace-free, so it decomposes into no
    # surrogate components at all -- `_component_restore_map`'s word-count-
    # >=2 guard excludes it categorically, not merely its digit. A response
    # containing an ordinary "utf-8" must survive restore byte-identical.
    inbox = ReviewInbox()
    for i in range(_POOL_SIZE):
        inbox.upsert(f"Filler Person {i}", context=f"...Filler Person {i}...", entity_type="person")
    item = inbox.upsert(
        "Kestrel Dynamics Holdings",
        context="...Kestrel Dynamics Holdings reported record profits...",
        entity_type="person",
    )
    assert item.provisional_surrogate == f"BFX{_POOL_SIZE:04d}"

    session = ExchangeSession()
    session.record(item.provisional_surrogate, item.real)

    text = 'encoding="utf-8"'
    provider_response = {"content": [{"type": "text", "text": text}]}
    restored = restore_response(provider_response, session)

    assert restored["content"][0]["text"] == text
