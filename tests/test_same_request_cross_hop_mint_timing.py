"""Issue #386 (parent #372 defect 1, governed by ADR-0050/ADR-0051): a bare surname
that is also a sub-span of several longer minted entities produced a **permanent**
503 deadlock -- unrecoverable without abandoning the conversation.

**The maintainer's own hypothesis, verified and replaced.** The issue's hypothesis was
that the blinder and the leak gate disagree about a word-boundary occurrence inside a
URL slug (``.../wiki/Ernst_Vogt``): an underscore is a word character, so no boundary
exists before the token there, while ``"Ernst Vogt"`` in prose does have one. Read
against the code, this does not hold: both sides resolve "does a known value occur
here" through the *identical* function, ``engine._real_value_pattern`` (imported from
``store._mint``), whose ``(?<!\\w)...(?!\\w)`` boundaries treat an underscore as a word
character on *both* the blinding pass (``_collect_provisional_pair_spans``) and
``leak_gate`` (``engine.py:2834``/``2846``). A slug-adjacent occurrence is therefore a
*symmetric miss* -- invisible to both sides alike -- which egresses silently in the
clear; it can never be the source of a *disagreement*, and a disagreement is what a
503 requires. (That symmetric miss is real and is a distinct, separate residual --
recorded in this issue's PR notes as out of scope here, since fixing it does not
touch ADR-0051's blinder/gate symmetry at all.)

**The actual mechanism**, found by constructing the live shape and inspecting the
outbound capture at the point of block (the artifact #385 exists to make visible):
``engine.blindfold_payload`` processes a request's hops **strictly in order** --
``system``, then each message, then ``tools`` last (``engine.py:285-306``). A
referent is minted into the review inbox the moment L3 confirms it, mid-pass, in
whichever hop first carries enough context to convince the adjudicator (here: the
disambiguation-cluster tool result, where four different "X Vogt" names anchor the
bare surname "Vogt" as a genuine referent). An *earlier* hop in the very same
request that also mentions the bare surname -- without that disambiguating
neighbourhood, so L3 declines to confirm it *there* -- is blindfolded and frozen
**before** the later hop's mint ever happens. ``leak_gate`` then scans the fully
assembled payload once, at the end, and finds the earlier hop's untouched literal
occurrence: a genuine gate/blinder disagreement, and a fail-closed 503 (privacy
held -- zero bytes reached the provider, exactly as the issue reports).

ADR-0051 already has a working answer to this **across requests**: stage 2
(``_collect_provisional_pair_spans``) reapplies every already-minted provisional pair
to every hop of every *subsequent* payload, and a mint is persisted the instant it
happens (``review.ReviewInbox.upsert`` persists synchronously, not transactionally
against the exchange's outcome) -- so on a plain retry the earlier hop *would* pick
up the pair. What ADR-0051's stage 2 never covered is the symmetric gap **within one
and the same request**: the hop-processing loop never revisits a hop once a *later*
hop of the same pass has minted the value that hop needed. That is the sub-span
this issue closes.

Fix: after every hop of a request has had its chance to mint (system, messages,
tools -- ``inbox.list()`` now complete for this request), sweep every hop's own,
already-blinded text through the identical deterministic-only substitution once
more (``_blindfold_system``/``_blindfold_content`` with ``l3_detector=None``) --
idempotent for any hop that was already fully covered, and closes the gap for one
that was not. The request that used to need a retry (or, per the issue, never
recovered at all) now clears leak_gate on the very first pass.

Leak-audit clauses:
- A: proven directly below -- the earlier hop's literal occurrence of the bare
  referent never reaches ``leak_gate``'s view of the assembled payload, on the
  first and only attempt (no retry).
- F: ``leak_gate`` (unmodified) no longer fires on a same-request hop-ordering gap
  it used to catch (correctly, per its own fail-closed contract) and that a client
  could not recover from without abandoning the exchange.
N/A this slice: B/C/D/E/G -- no restore/mapping-store/mint-uniqueness change; the
sweep only ever applies an *already-decided* provisional pair, never mints a new one.
"""

from __future__ import annotations

import pytest

from blindfold.engine import (
    LeakError,
    blindfold_chat_completions_payload,
    blindfold_payload,
    leak_gate,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _DisambiguationClusterAdjudicator:
    """Deterministic, stateless, context-sensitive -- confirms the bare surname only
    where its own local context shows the disambiguating neighbourhood a real
    adjudicator would need (siblings sharing the surname), exactly like a
    confidence-scoring L3 verdict would. The *same* candidate in the *same* context
    always gets the *same* verdict (CONTEXT.md's "Detection is reproducible"), so a
    retry with an unchanged payload can never itself flip the outcome.
    """

    _SIBLINGS = ("Ernst Vogt", "Werner Vogt", "Bernhard Vogt", "Peter Vogt")

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text == "Vogt":
            if any(sibling in candidate.context for sibling in self._SIBLINGS):
                return L3Adjudication(is_entity=True, entity_type="person")
            return L3Adjudication(is_entity=False)
        if candidate.text in ("Ernst", "Werner", "Bernhard", "Peter"):
            return L3Adjudication(is_entity=True, entity_type="person")
        return L3Adjudication(is_entity=False)


def _live_shaped_payload() -> dict:
    # An earlier hop mentions the bare surname with no disambiguating neighbours --
    # L3 declines to confirm it here (the shape of the issue's "2 in prose" bare
    # mentions that never anchor to the cluster). A later hop is the disambiguation
    # cluster itself: four longer entities sharing the surname (the issue's "sub-span
    # of four longer minted entities"), plus URL-slug occurrences of the same surname
    # (the issue's "11 of those inside URL slugs") -- included for shape fidelity;
    # this test does not assert they are protected (see the module docstring: that
    # underscore-adjacency miss is real, symmetric, and out of this issue's scope).
    return {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": "A caller named Vogt left a voicemail before the search ran.",
            },
            {
                "role": "user",
                "content": (
                    "Search results: Ernst Vogt wrote the initial report. "
                    "Werner Vogt disagreed in committee. Bernhard Vogt confirmed the "
                    "figures. Peter Vogt denied any involvement. Vogt was referenced "
                    "again in the closing summary. See https://example.org/wiki/Ernst_Vogt, "
                    "https://example.org/wiki/Werner_Vogt, "
                    "https://example.org/wiki/Bernhard_Vogt and "
                    "https://example.org/wiki/Peter_Vogt for the source pages."
                ),
            },
        ],
    }


def test_an_earlier_hops_bare_referent_is_blindfolded_on_the_first_attempt_no_retry_needed():
    # Acceptance criterion 3: the same payload must not block indefinitely -- strengthened
    # here to "must not block at all": the fix closes the gap within one request, so no
    # retry is needed in the first place.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_DisambiguationClusterAdjudicator())

    blinded, _session = blindfold_payload(_live_shaped_payload(), mapping, detector, inbox)

    earlier_hop_text = blinded["messages"][0]["content"]
    assert "Vogt" not in earlier_hop_text

    # Must not raise -- this is ADR-0051's symmetry invariant re-asserted over the
    # specific disagreement this issue found (acceptance criterion 4): before the fix,
    # this call raises LeakError on the very first attempt, with zero bytes ever having
    # reached the provider (fail-closed held; the defect is availability, not privacy).
    leak_gate(blinded, mapping, inbox)


def test_the_bare_referents_own_surrogate_appears_in_the_earlier_hop():
    # Strengthens the above: not merely "the real value is gone" (which a corrupting
    # half-substitution could also achieve) but "the referent's own, correct surrogate
    # is what replaced it" -- the earlier hop is coherently protected, not just gate-clean.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_DisambiguationClusterAdjudicator())

    blinded, _session = blindfold_payload(_live_shaped_payload(), mapping, detector, inbox)

    bare_item = next(item for item in inbox.list() if item.real == "Vogt")
    earlier_hop_text = blinded["messages"][0]["content"]
    assert bare_item.provisional_surrogate in earlier_hop_text


def test_without_the_fix_the_first_attempt_blocks_reproducing_the_issues_own_capture_shape():
    # Documents the failure this issue reports, directly: with today's single hop
    # pass (no closing sweep), leak_gate raises on the very first attempt over the
    # earlier hop's untouched literal occurrence -- privacy held (fail-closed), but
    # the request never proceeds. Pins the mechanism this issue's PR identifies,
    # independent of whether the fix above is applied to blindfold_payload itself:
    # a bare literal occurrence an item's own pattern would match, in a hop that was
    # blinded before the item existed, is exactly what leak_gate is *supposed* to
    # catch -- this test proves that the pre-fix hop loop actually produces one.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_DisambiguationClusterAdjudicator())

    # Reproduce the pre-fix single-pass shape directly against the engine's own
    # per-hop primitives, bypassing the (now fixed) closing sweep in
    # blindfold_payload, so this test keeps documenting the underlying defect even
    # after the fix lands (it does not regress when engine.py changes).
    from blindfold import engine

    session = engine.ExchangeSession()
    payload = _live_shaped_payload()
    out = {"messages": [dict(m) for m in payload["messages"]]}
    for message in out["messages"]:
        message["content"] = engine._blindfold_content(
            message.get("content"), mapping, session, detector, inbox
        )

    with pytest.raises(LeakError):
        leak_gate(out, mapping, inbox)


def test_the_chat_completions_endpoint_gets_the_same_closing_sweep():
    # ADR-0051's own rollout covered both wire formats in lockstep (issue #300's
    # own chat-completions test alongside its Messages one) -- this issue's fix
    # must too, or the OpenAI-compatible path keeps the exact deadlock the
    # Messages path no longer has.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_DisambiguationClusterAdjudicator())
    payload = _live_shaped_payload() | {"model": "gpt-4o"}

    blinded, _session = blindfold_chat_completions_payload(
        payload, mapping, detector, inbox
    )

    earlier_hop_text = blinded["messages"][0]["content"]
    assert "Vogt" not in earlier_hop_text
    leak_gate(blinded, mapping, inbox)
