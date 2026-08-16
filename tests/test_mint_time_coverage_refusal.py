"""Issue #293, option 3: refuse the mint that creates an unservable state.

The trusted-maintainer direction: narrowing ``leak_gate`` to word boundaries
(``tests/test_leak_gate_scope_symmetry.py``) is necessary but not sufficient -- it does
not fix the reported deadlock itself, because a bullet-initial capitalized common word
like ``"Prompt"`` recurs as a *whole word* elsewhere in the same transcript too, not just
as a sub-token of a longer compound. The actual repro is an L3 adjudicator that disagrees
with itself across two occurrences of the identical literal token (real hardware, not a
hand-scripted stub, can and does return a different verdict for the same word in two
different contexts): it confirms one occurrence as a novel entity and dismisses another.
Minting the confirmed one guarantees the un-blinded occurrence keeps the plaintext word
live in every future outbound payload that carries this hop's text back around --
deadlocking leak_gate permanently, unrecoverable without a restart (see #294).

The fix: before minting a provisional entity for a confirmed candidate, check whether its
real value's word-boundary occurrences in the *whole hop text* are fully covered by the
span(s) this pass is about to blind. If some occurrence would be left standing, the mint
is refused entirely -- the candidate is left un-blinded (never partially blinded), so the
review inbox never grows a row that later deadlocks the gate. This never dismisses a
candidate into plaintext that the blinder would otherwise have protected: a common-word
false positive was already going to recur elsewhere in the clear regardless of whether
this one occurrence got a surrogate, so refusing to mint is not a new loss of protection.
"""

from __future__ import annotations

from blindfold.engine import blindfold_payload, leak_gate
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


def test_mint_is_refused_when_the_reals_bare_word_would_be_left_unblinded_elsewhere():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlyWhenContextCarriesMarker())

    blindfolded, _session = blindfold_payload(_repro_payload(), mapping, detector, inbox)

    assert not any(item.real == "Prompt" for item in inbox.list())
    text = blindfolded["messages"][0]["content"]
    # Never partially blinded either -- both occurrences are left exactly as they were.
    assert text.count("Prompt") == 2


def test_a_declined_mint_never_deadlocks_the_very_next_leak_gate_check():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlyWhenContextCarriesMarker())

    blindfolded, _session = blindfold_payload(_repro_payload(), mapping, detector, inbox)
    text = blindfolded["messages"][0]["content"]

    # Should not raise: nothing was minted for "Prompt", so there is no inbox row (and
    # no mapping entry) whose real value the gate could fire on.
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
    # Acceptance criterion: a single blocked exchange must not make the proxy
    # permanently unservable. Replay the exact run-5-shaped request twice against
    # the same (fresh-at-test-start) mapping/inbox, mirroring a client retrying or
    # simply continuing the same conversation -- the second attempt must not be
    # blocked by anything the first attempt minted.
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

    assert not any(item.real == "Prompt" for item in inbox.list())


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
