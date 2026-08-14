"""Confirmed phone-shaped candidate -> reserved-namespace mint (issue #277).

A candidate the phone-shaped producer (l3.py) flags is only ever a *proposal* --
L3 adjudication decides whether it's a genuine phone number. Once confirmed, the
mint pass must treat it exactly like an L1-detected international number: mint a
stable reserved-namespace surrogate via ``SurrogateMapping.mint_pii`` (ADR-0005),
never the name-shaped provisional-review-inbox pool a confirmed person/org
candidate goes through. There is no "is this actually PII" curation step for a
contactable-PII kind the way there is for a name (merge, coreference) -- once L3
has made the contextual call, it is L1-equivalent PII from here on.

Leak-audit clauses:
- A: the stub upstream sees only the surrogate, never the real phone number.
- B: the client receives the fully restored real value.
- E: the minted surrogate is reserved-namespace (never a routable lookalike).
N/A this slice: C/D/F/G -- generic restore/verify-pass/fail-closed/mapping-secrecy
machinery, already proven elsewhere and untouched by this routing decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from blindfold.engine import blindfold_payload, restore_response
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping, is_reserved_namespace_surrogate


@dataclass
class _Call:
    text: str


class _ConfirmAllAdjudicator:
    """Confirms every candidate it's handed, tagging phone-shaped ones "phone"."""

    def __init__(self) -> None:
        self.calls: list[_Call] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(_Call(text=candidate.text))
        is_phone_shaped = candidate.text[0].isdigit() or candidate.text.startswith("(")
        return L3Adjudication(
            is_entity=True,
            entity_type="phone" if is_phone_shaped else None,
        )


def test_l3_confirmed_phone_candidate_mints_a_reserved_namespace_surrogate():
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAllAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "the on-call pager is 555-0142 today."}
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    text = blinded["messages"][0]["content"]
    assert "555-0142" not in text
    surrogate = mapping.surrogate_for("555-0142")
    assert surrogate is not None
    assert is_reserved_namespace_surrogate(surrogate)
    assert surrogate in text


def test_l3_confirmed_phone_candidate_never_lands_in_the_review_inbox():
    # Distinguishes the phone-shaped path from the person/org path: a confirmed
    # phone is L1-equivalent PII (mint_pii, ADR-0005), not a novel entity awaiting
    # human curation -- there is no merge/coreference concept for a phone number.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAllAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "the on-call pager is 555-0142 today."}
        ],
    }

    blindfold_payload(payload, mapping, detector, inbox)

    assert inbox.list() == []


def test_l3_confirmed_phone_candidate_restores_closed_world():
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAllAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "the on-call pager is 555-0142 today."}
        ],
    }

    blinded, session = blindfold_payload(payload, mapping, detector, inbox)
    surrogate = mapping.surrogate_for("555-0142")
    upstream_reply = {
        "content": [{"type": "text", "text": f"Noted, I'll call {surrogate}."}]
    }

    restored = restore_response(upstream_reply, session)

    assert "555-0142" in restored["content"][0]["text"]
    assert surrogate not in restored["content"][0]["text"]


class _PhoneVsConfusableAdjudicator:
    """Confirms exactly the whitelisted phone-shaped texts as "phone"; dismisses
    everything else -- a stand-in for the contextual judgement (phone vs. invoice
    reference vs. version fragment vs. dimension) a real L3 adjudicator makes,
    mirroring test_l3_surrogate_coalescing.py's `_TypedStubAdjudicator` pattern.
    """

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text not in self._confirm:
            return L3Adjudication(is_entity=False)
        return L3Adjudication(is_entity=True, entity_type="phone")


def test_l3_rejects_a_nanpa_shaped_confusable_while_confirming_the_real_phone():
    # Acceptance criteria: the must_not false-positive surface (an invoice
    # reference sharing the exact NANPA area-exchange-line digit grouping) must
    # NOT be blindfolded when L3 rejects it, alongside a genuine phone in the
    # same text that L3 does confirm -- the matcher alone cannot tell them apart
    # (both are candidates); only L3's contextual verdict can.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_PhoneVsConfusableAdjudicator(confirm={"555-0142"}))
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "the on-call pager is 555-0142; "
                    "invoice reference 205-118-4471 is overdue."
                ),
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    text = blinded["messages"][0]["content"]
    assert "555-0142" not in text
    assert "205-118-4471" in text
