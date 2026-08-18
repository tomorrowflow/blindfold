"""Issue #292: Blindfold's own surrogates get re-detected as novel real entities.

**Corrected root cause** (trusted-maintainer comment after cycle 2): the bug is
not that a surrogate *component* can't be told apart from a genuine real value
by string alone -- that ambiguity is real but only matters once a colliding
surrogate has already been minted. The actual defect is that the provisional
surrogate pool (``review._PROVISIONAL_POOL``) was never checked for
disjointness against the **live corpus** being processed -- only against other
pools (issue #80's pool-vs-pool checks) and the closed-world set of known real
values. This repository's own docs (``CONTEXT.md``, ``docs/adr/0036``) used a
literal pool entry as a worked example, so an agent reading either as a tool
result handed L3 a bare pool-name component as prose; L3 confirmed it as a
novel real; that string was simultaneously live as a surrogate for an
unrelated referent; ``leak_gate``'s ``item.real in outbound_text`` substring
check (issue #287) then fired on every subsequent payload, forever.

**The fix**: extend issue #80's mint-time disjointness from seeded reals to
the live corpus (``store._mint.pool_entry_collides_with_corpus``,
``review._next_provisional``) -- a pool entry already occurring in the hop's
own text is skipped, falling back to the next pool entry, exactly like an
entry colliding with a known real value already is. No candidate is ever
dismissed into plaintext: cycle 2's value-scoped ``surrogate_space_match``
dismissal in ``engine._blindfold_text`` is removed outright, not narrowed
further. Docs no longer use a literal pool entry as an example (see
``test_no_pool_entry_appears_as_a_literal_example_in_docs``).

**The accepted residual**: a real value in a *later* exchange can still
collide with a surrogate minted in an *earlier* one (or pre-seeded before this
exchange) -- pool-vs-corpus disjointness only guards the corpus being
processed *this* exchange. That tail stays fail-closed (blocked by
``leak_gate``, unchanged and out of scope), never fail-open -- see
``test_kurt_colliding_with_an_earlier_minted_surrogate_still_fails_closed``
and ``test_standalone_component_of_an_earlier_minted_surrogate_still_fails_closed``,
mirroring the reviewer's own cycle-2 findings.

Leak-audit clauses exercised:
- A: no candidate is ever left in plaintext by dismissal (removed); a real
  value colliding with corpus-disjoint-avoided surrogate space still mints
  and blindfolds normally; a real value colliding with an *earlier*-minted
  surrogate still fails closed (blocked), never egresses.
- F (fail-closed unchanged / restored): a genuinely novel real value that
  shares no span with surrogate-space still mints exactly as before; the
  accepted residual blocks rather than leaks.
N/A this slice: B/C/D/E/G -- no restore/store-schema/mapping-cipher changes;
the repair path (``purge_surrogate_collisions``) is unchanged and has its own
tests below.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from blindfold.engine import LeakError, blindfold_payload, leak_gate
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import _PROVISIONAL_POOL, ReviewInbox
from blindfold.store._mint import (
    _PERSON_POOL,
    _REPLACEMENT_POOL,
    _TERM_POOL,
    _ORG_POOL,
)
from blindfold.surrogates import SurrogateMapping

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class _ConfirmAnyNameShapedTokenAdjudicator:
    """Stub for a real local model: confirms EVERY candidate span as an entity.

    Mirrors ``test_l3_surrogate_reblindfold_guard.py``'s stub of the same name --
    a hand-scripted confirm-list can't reproduce this bug, since a real model
    has no such list: it says "yes, name-shaped" to a surrogate component
    exactly as readily as to a genuinely novel name.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=True)


def test_no_pool_entry_appears_as_a_literal_example_in_docs():
    # Acceptance criterion: docs no longer use literal pool entries as
    # examples -- a regression guard so this can't silently reappear. Scans
    # CONTEXT.md and every docs/adr/*.md file for any surrogate-pool entry
    # (from every pool -- seed-time and provisional alike), the actual
    # trigger for the reported deadlock (a pool entry doubling as a doc's
    # own worked example).
    from blindfold.review import _PROVISIONAL_ORG_POOL

    all_pool_entries = (
        list(_PERSON_POOL)
        + list(_TERM_POOL)
        + list(_ORG_POOL)
        + list(_REPLACEMENT_POOL)
        + list(_PROVISIONAL_POOL)
        + list(_PROVISIONAL_ORG_POOL)
    )

    doc_paths = [_REPO_ROOT / "CONTEXT.md", *sorted((_REPO_ROOT / "docs" / "adr").glob("*.md"))]
    offenders = []
    for path in doc_paths:
        text = path.read_text()
        for entry in all_pool_entries:
            if entry in text:
                offenders.append((path.name, entry))

    assert offenders == []


def test_no_live_verify_brief_entity_appears_as_a_literal_example_in_docs():
    # Issue #340: the same class as the pool-entry guard above, one namespace
    # over. docs/adr/0036 used `Northwind Analytics`/`Kestrel Dynamics` and
    # docs/adr/0022 used `Priya Nadkarni` as worked examples -- all three are
    # real protected entities in tests/live-verify/74-engagement-brief.md, the
    # fixture every #74 live-verify run is seeded with. The #74 brief instructs
    # the session to read ADR-0036 in full, so every run guarantees: brief
    # mints a surrogate for the entity -> session reads the ADR's prose
    # mentioning the *same* entity -> real-word component pairing (#306) maps
    # it onto the live surrogate's component, minting a novel string that
    # exists in no source file (the #339 artifact).
    #
    # Forbidden vocabulary is derived from the brief file itself -- its
    # Person/Employer table columns, plus any **bold** prose callouts -- not a
    # hardcoded list, so adding an entity to the brief extends this guard
    # automatically. An organisation's legal-form-stripped form is included
    # too (`_strip_legal_form_suffix`, issue #289's own bare-vs-full-legal-name
    # equivalence): the brief carries "Kestrel Dynamics GmbH", but the doc
    # occurrence that actually triggered this issue was the bare "Kestrel
    # Dynamics".
    from blindfold.review import _strip_legal_form_suffix

    brief_path = _REPO_ROOT / "tests" / "live-verify" / "74-engagement-brief.md"
    brief_text = brief_path.read_text()
    lines = brief_text.splitlines()

    entities: set[str] = set()

    header_idx = None
    columns: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("|") and "Person" in stripped and "Employer" in stripped:
            header_idx = i
            columns = [cell.strip() for cell in stripped.strip("|").split("|")]
            break
    if header_idx is not None:
        entity_columns = {"Person", "Employer"}
        col_indices = [i for i, name in enumerate(columns) if name in entity_columns]
        for line in lines[header_idx + 2 :]:  # skip the "|---|...|" separator row
            stripped = line.strip()
            if not stripped.startswith("|"):
                break
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            for idx in col_indices:
                if idx < len(cells) and cells[idx]:
                    entities.add(cells[idx])

    # Bold markdown is also used for plain emphasis (e.g. "deliberately
    # **novel**") -- an entity callout is always a proper noun or a code, so
    # require the match to start with an uppercase letter or a digit.
    for match in re.finditer(r"\*\*([^*]+)\*\*", brief_text):
        value = match.group(1).strip()
        if value and (value[0].isupper() or value[0].isdigit()):
            entities.add(value)

    entities |= {_strip_legal_form_suffix(entity) for entity in entities}

    doc_paths = [_REPO_ROOT / "CONTEXT.md", *sorted((_REPO_ROOT / "docs" / "adr").glob("*.md"))]
    offenders = []
    for path in doc_paths:
        text = path.read_text()
        for entity in entities:
            if entity in text:
                offenders.append((path.name, entity))

    assert offenders == []


def test_pool_vs_corpus_disjointness_prevents_the_reported_deadlock():
    # The observed deadlock's actual shape: a referent needing a *fresh*
    # surrogate this exchange, and a literal pool entry (+ its first
    # component) already present as plain prose in the *same* hop's corpus
    # -- e.g. a doc/glossary tool result read alongside real traffic. The
    # pool entry must never be assigned; the next pool entry is used
    # instead, so no candidate ever needs to be dismissed and no subsequent
    # payload carrying the newly-assigned surrogate is blocked.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())
    pool_entry = _PROVISIONAL_POOL[0]
    first_component = pool_entry.split()[0]
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Sarah Bergmann filed the report. By the way, a "
                    "surrogate component is a word token of a multi-word "
                    f"surrogate (e.g. {first_component} in {pool_entry})."
                ),
            }
        ],
    }

    blindfolded, session = blindfold_payload(payload, mapping, detector, inbox)

    items = {item.real: item for item in inbox.list()}
    assert "Sarah Bergmann" in items
    assert items["Sarah Bergmann"].provisional_surrogate != pool_entry

    text = blindfolded["messages"][0]["content"]
    # Not blocked -- the whole point of avoiding the collision at mint time.
    leak_gate({"messages": [{"role": "user", "content": text}]}, mapping, inbox)


def test_reading_context_and_adr0036_excerpts_as_tool_results_does_not_deadlock_the_gate():
    # The concrete reproduction from live-verify run 4: CONTEXT.md's
    # "Surrogate component" glossary entry and ADR-0036's worked example,
    # read as a tool result alongside genuine traffic in the same exchange,
    # must not deadlock the gate -- neither on this hop nor (the actual
    # reported symptom -- "every payload, forever") on a later one.
    context_md = _REPO_ROOT / "CONTEXT.md"
    context_lines = context_md.read_text().splitlines()
    glossary_excerpt = "\n".join(context_lines[253:271])

    adr_0036 = (
        _REPO_ROOT
        / "docs"
        / "adr"
        / "0036-component-restore-bounded-closed-world-sub-token.md"
    )
    adr_excerpt = "\n".join(adr_0036.read_text().splitlines()[:30])

    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Sarah Bergmann asked me to review these two doc "
                    f"excerpts:\n\n{glossary_excerpt}\n\n{adr_excerpt}"
                ),
            }
        ],
    }

    blindfolded, session = blindfold_payload(payload, mapping, detector, inbox)
    text = blindfolded["messages"][0]["content"]
    leak_gate({"messages": [{"role": "user", "content": text}]}, mapping, inbox)

    # A later, unrelated exchange carrying the same now-live surrogate(s)
    # must not be blocked either -- the reported symptom was every request
    # deadlocking, not just the one that read the docs.
    followup = {
        "model": "m",
        "messages": [{"role": "user", "content": "Please summarize the review."}],
    }
    blindfolded_followup, _ = blindfold_payload(followup, mapping, detector, inbox)
    followup_text = blindfolded_followup["messages"][0]["content"]
    leak_gate({"messages": [{"role": "user", "content": followup_text}]}, mapping, inbox)


def test_genuinely_novel_real_value_sharing_no_span_with_surrogate_space_still_mints():
    # Guard over-broadness check (no loss of detection): a real value that
    # shares NO span with any live surrogate must be detected and minted
    # exactly as before.
    mapping = SurrogateMapping.from_pairs([("Referent Real", "Erika Mustermann")])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": "Priya Nadkarni sent the update yesterday.",
            }
        ],
    }

    blindfolded, session = blindfold_payload(payload, mapping, detector, inbox)

    inbox_reals = {item.real for item in inbox.list()}
    assert "Priya Nadkarni" in inbox_reals


def test_real_value_that_is_a_bare_character_fragment_of_an_unrelated_surrogate_still_mints():
    # A genuinely novel real value that is merely a *character*-level
    # fragment of an unrelated live surrogate -- "Kurt" inside "Kurtis
    # Vale", no word boundary -- is not Blindfold's own output re-entering
    # the transcript; it must still be detected, minted, and blindfolded.
    mapping = SurrogateMapping.from_pairs([("Unrelated Referent", "Kurtis Vale")])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Unrelated Referent filed the report. Separately, Kurt "
                    "asked about the timeline."
                ),
            }
        ],
    }

    blindfolded, session = blindfold_payload(payload, mapping, detector, inbox)

    inbox_reals = {item.real for item in inbox.list()}
    assert "Kurt" in inbox_reals

    text = blindfolded["messages"][0]["content"]
    # No real "Kurt" token crosses egress -- only the surrogate "Kurtis Vale"
    # (whose first word is a superstring, never a match) may remain.
    assert not re.search(r"\bKurt\b", text)


def test_standalone_component_of_an_earlier_minted_surrogate_still_fails_closed():
    # Cycle 1's original repro, re-asserted against the new design: "Carla"
    # (now "Erika") standing alone, elsewhere in the same text as the
    # already-live surrogate "Erika Mustermann" *established before this
    # exchange* (pre-seeded, not this hop's own corpus -- pool-vs-corpus
    # disjointness only guards the corpus being processed this exchange).
    # This is the accepted residual: fails closed (blocked), never leaked.
    mapping = SurrogateMapping.from_pairs([("Referent Real", "Erika Mustermann")])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Referent Real asked me to loop in Erika about the "
                    "schedule."
                ),
            }
        ],
    }

    blindfolded, session = blindfold_payload(payload, mapping, detector, inbox)
    text = blindfolded["messages"][0]["content"]

    with pytest.raises(LeakError):
        leak_gate({"messages": [{"role": "user", "content": text}]}, mapping, inbox)


def test_kurt_colliding_with_an_earlier_minted_surrogate_still_fails_closed():
    # Cycle 2 reviewer's exact finding: a real seed surrogate "Kurt
    # Steinmetz" (live via L2 dict substitution of "Some Referent") and a
    # genuinely different real "Kurt" mentioned separately in the same hop.
    # Cycle 2's now-removed dismissal path leaked "Kurt" into plaintext
    # here (fail-open); with that path gone, this reverts to the pre-#292
    # fail-closed default -- blocked, not leaked. leak_gate itself is
    # untouched and out of scope for this issue.
    mapping = SurrogateMapping.from_pairs([("Some Referent", "Kurt Steinmetz")])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Please loop in Kurt about it. Some Referent already "
                    "signed off."
                ),
            }
        ],
    }

    blindfolded, session = blindfold_payload(payload, mapping, detector, inbox)
    text = blindfolded["messages"][0]["content"]

    with pytest.raises(LeakError) as exc_info:
        leak_gate({"messages": [{"role": "user", "content": text}]}, mapping, inbox)

    # SEC-3: the raised reason references the newly-minted item's own
    # provisional surrogate (issue #287's existing scrubbing), never the
    # plaintext real value "Kurt" that triggered it.
    assert "Kurt" not in str(exc_info.value)


def test_purge_surrogate_collisions_repairs_an_already_poisoned_persisted_inbox():
    # Acceptance criterion: a repair path for a store already poisoned before
    # this fix existed -- with mapping_cipher "none" the inbox is in-memory and
    # a restart clears it, but a persisted inbox carries the deadlock across
    # restarts with no way out except hand-rejecting every colliding item.
    mapping = SurrogateMapping.from_pairs([("Referent Real", "Erika Mustermann")])
    inbox = ReviewInbox()
    poisoned = inbox.upsert(
        "Erika",
        context="...abbreviates a full-name surrogate (Erika for Erika Mustermann)...",
    )
    legitimate = inbox.upsert(
        "Priya Nadkarni", context="Priya Nadkarni sent the update yesterday."
    )

    removed = inbox.purge_surrogate_collisions(mapping)

    assert [item.id for item in removed] == [poisoned.id]
    remaining_ids = {item.id for item in inbox.list()}
    assert remaining_ids == {legitimate.id}


def test_purge_surrogate_collisions_never_drops_a_genuinely_novel_item():
    # Guard over-broadness check, mirrored at the repair path: an item whose
    # real value merely shares a word with an unrelated surrogate never
    # mentioned in *its own* recorded context must survive the sweep --
    # scoped exactly like the mint-time guard, not the full process-global
    # surrogate vocabulary (issue #68's "Vogt" precedent).
    mapping = SurrogateMapping.from_pairs([("Martin Bach", "Bernhard Vogt")])
    inbox = ReviewInbox()
    genuinely_novel = inbox.upsert(
        "Petra Vogt", context="Please schedule a call with Petra Vogt tomorrow."
    )

    removed = inbox.purge_surrogate_collisions(mapping)

    assert removed == []
    assert genuinely_novel.id in {item.id for item in inbox.list()}


def test_purge_surrogate_collisions_never_drops_a_bare_character_fragment_match():
    # Repair-path counterpart to the mint-time false-positive fix above: an
    # item whose real value is a genuine referent ("Kurt") that merely shares
    # characters (not a whole word) with an unrelated live surrogate
    # ("Kurtis Vale" -- "Kurt" is a prefix of "Kurtis", never a standalone
    # word token of it) must survive the sweep. ``purge_surrogate_collisions``
    # shares ``surrogate_space_match`` with the mint-time guard, so this locks
    # the same word-boundary narrowing in at the repair path too.
    mapping = SurrogateMapping.from_pairs([("Unrelated Referent", "Kurtis Vale")])
    inbox = ReviewInbox()
    genuine = inbox.upsert(
        "Kurt",
        context=(
            "Kurtis Vale filed the report. Separately, Kurt asked about the "
            "timeline."
        ),
    )

    removed = inbox.purge_surrogate_collisions(mapping)

    assert removed == []
    assert genuine.id in {item.id for item in inbox.list()}
