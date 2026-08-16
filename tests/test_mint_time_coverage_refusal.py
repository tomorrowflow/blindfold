"""Issue #293's mint-time coverage question, resolved per issue #295.

The trusted-maintainer direction: narrowing ``leak_gate`` to word boundaries
(``tests/test_leak_gate_scope_symmetry.py``) is necessary but not sufficient -- it does
not fix the reported deadlock itself, because a bullet-initial capitalized common word
like ``"Prompt"`` recurs as a *whole word* elsewhere in the same transcript too, not just
as a sub-token of a longer compound. The actual repro is an L3 adjudicator that disagrees
with itself across two occurrences of the identical literal token (real hardware, not a
hand-scripted stub, can and does return a different verdict for the same word in two
different contexts): it confirms one occurrence as a novel entity and dismisses another.

#293 originally closed this by refusing the mint outright whenever the real value's
word-boundary occurrences in the whole hop text weren't fully covered by the confirmed
span(s) -- correct for a false positive (the confirmation itself is wrong and the word
was always going to recur in the clear regardless), but #295 found that for a *true*
positive this discarded the L3 confirmation and left the CONFIRMED occurrence in
plaintext too: a silent leak traded for what used to be a loud fail-closed 503.

The fix (ADR-0050 amendment): never refuse a confirmed candidate's mint. Mint it, then
blind every word-boundary occurrence of its real value anywhere in this hop -- not just
the span(s) L3 happened to confirm -- so the confirmation's own verdict about the
referent, not the character range, decides coverage. The outbound payload for this hop
then carries zero occurrences of the real value either way, whether L3 disagreed with
itself or agreed at every occurrence.
"""

from __future__ import annotations

from blindfold.engine import blindfold_payload, leak_gate, restore_response
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping

_MARKER = "=CONFIRM="


class _ConfirmOnlyWhenContextCarriesMarker:
    """Stub for a real adjudicator disagreeing with itself across two occurrences of
    the same literal token, driven by differing context -- exactly what a real
    model does (a hand-scripted confirm-list can't reproduce per-occurrence
    disagreement on the identical token text).
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=_MARKER in candidate.context)


def _repro_payload() -> dict:
    content = (
        f"- Prompt {_MARKER} appears at the start of this checklist entry for the audit.\n"
        + "Padding text to create separation. " * 6
        + "Meanwhile the Prompt handling logic elsewhere remains unchanged in this hop."
    )
    return {"model": "m", "messages": [{"role": "user", "content": content}]}


def test_a_confirmed_true_positive_is_blinded_at_every_occurrence_not_left_in_plaintext():
    # Issue #295's acceptance criterion: a hop where the same real value is confirmed
    # at one occurrence and not at another does not send that value's plaintext
    # upstream. Asserts on the outbound payload, not on inbox state.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlyWhenContextCarriesMarker())

    blindfolded, _session = blindfold_payload(_repro_payload(), mapping, detector, inbox)

    text = blindfolded["messages"][0]["content"]
    assert "Prompt" not in text


def test_a_confirmed_true_positives_surrogate_restores_to_the_real_value_everywhere_it_was_swept():
    # Leak-audit clause B: the client must see the real value back, fully restored,
    # at every position the sweep blinded -- not just the one L3 originally confirmed.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlyWhenContextCarriesMarker())

    blindfolded, session = blindfold_payload(_repro_payload(), mapping, detector, inbox)
    text = blindfolded["messages"][0]["content"]
    assert "Prompt" not in text

    provider_response = {"content": [{"type": "text", "text": text}]}
    restored = restore_response(provider_response, session)

    assert restored["content"][0]["text"].count("Prompt") == 2


def test_minting_a_confirmed_true_positive_never_deadlocks_the_very_next_leak_gate_check():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlyWhenContextCarriesMarker())

    blindfolded, _session = blindfold_payload(_repro_payload(), mapping, detector, inbox)
    text = blindfolded["messages"][0]["content"]

    # Should not raise: "Prompt" was minted, but every occurrence in this hop was
    # blinded along with it, so there is no un-blinded occurrence left for the gate
    # to fire on.
    leak_gate({"messages": [{"role": "user", "content": text}]}, mapping, inbox)


def test_a_genuinely_novel_real_with_no_leftover_occurrence_still_mints_normally():
    # No loss of detection: a candidate whose real value is fully covered by the
    # confirmed span(s) (no leftover occurrence anywhere else in the hop) must still
    # mint and blindfold exactly as before this guard existed.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()

    class _ConfirmEverySpan:
        def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
            return L3Adjudication(is_entity=True)

    detector = L3Detector(_ConfirmEverySpan())
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "Sarah Bergmann filed the report yesterday."}
        ],
    }

    blindfolded, _session = blindfold_payload(payload, mapping, detector, inbox)

    items = {item.real: item for item in inbox.list()}
    assert "Sarah Bergmann" in items
    text = blindfolded["messages"][0]["content"]
    assert "Sarah Bergmann" not in text
    assert items["Sarah Bergmann"].provisional_surrogate in text


def test_replaying_the_same_request_twice_is_never_blocked_by_a_row_the_first_attempt_minted():
    # Acceptance criterion: a single mint must not make the proxy permanently
    # unservable. Replay the exact run-5-shaped request twice against the same
    # (fresh-at-test-start) mapping/inbox, mirroring a client retrying or simply
    # continuing the same conversation -- the second attempt must not be blocked by
    # anything the first attempt minted.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlyWhenContextCarriesMarker())

    for attempt in range(2):
        blindfolded, _session = blindfold_payload(
            _repro_payload(), mapping, detector, inbox
        )
        text = blindfolded["messages"][0]["content"]
        # Should not raise on either attempt.
        leak_gate({"messages": [{"role": "user", "content": text}]}, mapping, inbox)

    # Dedup, not duplication: both attempts resolve to the one referent (issue #289's
    # referent-key reuse), not a second row minted on replay.
    assert sum(1 for item in inbox.list() if item.real == "Prompt") == 1


def test_the_same_real_confirmed_at_every_occurrence_still_mints_and_blinds_all_of_them():
    # If every occurrence of a repeated real value IS confirmed (full coverage), the
    # guard must not refuse the mint just because the word repeats.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()

    class _ConfirmEverySpan:
        def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
            return L3Adjudication(is_entity=True)

    detector = L3Detector(_ConfirmEverySpan())
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": "Nordwind Logistik called. Later, Nordwind Logistik called again.",
            }
        ],
    }

    blindfolded, _session = blindfold_payload(payload, mapping, detector, inbox)

    text = blindfolded["messages"][0]["content"]
    assert "Nordwind Logistik" not in text
    assert text.count("called") == 2
