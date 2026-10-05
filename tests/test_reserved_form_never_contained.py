"""ADR-0060 amendment point 6 (issue #447): a reserved-form string is never
contained, never minted, and never re-substituted.

ADR-0052's closed syntactic class (opaque ``PREFIX\\d{4,}`` token, any of the
family's prefixes including the containment prefix ``BFW``) already forbids
*minting* a candidate of reserved form (``review.ReviewInbox.upsert`` refuses
and returns ``None``, issue #330). This slice extends the same refusal to
*containment*: before this fix, the ADR-0060 §3 world-acting branch called
``ExchangeSession.contain(real)`` with no reserved-form check at all, so a
reserved token the model copies into a later fan-out could be contained into
a second reserved token.

Two layers, matching the issue body:

- A graceful skip at L3 candidate filtering (the mint-or-contain gate inside
  ``engine.py``'s group loop), mirroring how ``select_phone_candidate_spans``
  already drops the reserved *phone* range before it ever becomes a
  candidate: a reserved-form ``real`` is dropped outright -- no mint, no
  containment, the literal text reaches the provider unchanged.
- ``ExchangeSession.contain`` itself fails closed (ADR-0009 shape, scrubbed
  reason -- :class:`~blindfold.l3.L3DetectionInternalError`, the existing
  "Blindfold's own internal-invariant violation" exception, issue #315) if it
  is ever handed a reserved-form value regardless of caller -- the backstop
  for every OTHER ``.contain()`` call site (``_collect_containment_spans`` and
  its slug/component siblings), not just the world-acting mint loop.

Leak-audit for this slice: clause A (pre-egress) is the direct subject -- a
reserved-form string must reach the provider unchanged, never replaced by a
second reserved-form token, and a genuine co-occurring referent must still be
fully substituted (no real value egresses). Clause C (closed-world restore) is
exercised too: the pass-through token is never recorded into
``session.injected``, so it is never a restore target, exactly like #411's own
exemption, and ``resolution_gate`` must not fail-close on it. Clause B is N/A
for the pass-through token itself (never restored, by design, per #411); the
co-occurring genuine referent's own restore is covered by the existing
#410/#411 suites, not re-proven here. Fail-closed (F) is the subject of the
``contain()`` backstop itself.
"""

from __future__ import annotations

from blindfold.engine import (
    ExchangeSession,
    blindfold_payload,
    leak_gate,
    resolution_gate,
    restore_response,
)
from blindfold.l3 import (
    CandidateSpan,
    L3Adjudication,
    L3DetectionInternalError,
    L3Detector,
)
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
import pytest


class _ProposesReservedTokenItselfAsPerson:
    """Stands in for an L3 pathway that proposes the reserved-form text itself
    as a confirmed novel person.

    Neither of today's two candidate producers (``select_candidate_spans``'s
    capitalized-token pass, ``select_phone_candidate_spans``'s digit-dash
    pass) can shape such a candidate -- ADR-0052's family always carries a
    digit run, so it can never be Title-Case-alpha or phone-shaped. That is
    exactly why this is a defense-in-depth fix, not a reachable-today exploit:
    the mint/contain pipeline must refuse a reserved-form ``real`` on its own
    terms, not rely on it being structurally unreachable from current
    producers staying true forever. This double substitutes for the whole
    ``L3Detector`` seam (production wires a real one; tests substitute a
    recording stub, same discipline ``L3Adjudicator`` test doubles already
    use) to exercise that refusal directly, the same way #410's own
    ``_ConfirmCapitalizedAsPerson`` stands in for a real adjudicator.
    """

    provider_name = "fake"

    def __init__(self, reserved_token: str) -> None:
        self._reserved_token = reserved_token

    def detect(self, text, known_entities, *args, **kwargs):
        start = text.index(self._reserved_token)
        end = start + len(self._reserved_token)
        return [
            (
                CandidateSpan(
                    text=self._reserved_token,
                    start=start,
                    end=end,
                    context=text,
                    context_offset=start,
                ),
                L3Adjudication(is_entity=True, entity_type="person"),
            )
        ]


def test_a_reserved_form_token_in_world_acting_prose_is_never_contained():
    # AC1: reaches the provider with that token unchanged, and no second
    # reserved token is issued for it -- a graceful skip, not a block.
    mapping = SurrogateMapping()
    detector = _ProposesReservedTokenItselfAsPerson("BFW0000")
    payload = {
        "tools": [{"name": "web_search_20250101"}],
        "messages": [{"role": "user", "content": "Please look up BFW0000 again."}],
    }

    blinded, session = blindfold_payload(
        payload, mapping, detector, None, world_acting=True
    )

    text = blinded["messages"][0]["content"]
    assert text == "Please look up BFW0000 again."
    assert session.contained_reals() == {}


def test_a_reserved_form_token_in_non_world_acting_prose_is_never_minted():
    # AC2: the same holds off world-acting -- no inbox item, no substitution.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = _ProposesReservedTokenItselfAsPerson("BFX0042")
    payload = {
        "messages": [{"role": "user", "content": "Please look up BFX0042 again."}]
    }

    blinded, _session = blindfold_payload(
        payload, mapping, detector, inbox, world_acting=False
    )

    text = blinded["messages"][0]["content"]
    assert text == "Please look up BFX0042 again."
    assert inbox.list() == []


class _ConfirmCapitalizedAsPerson:
    """Mirrors ``test_world_acting_containment.py``'s own stub of the same name:
    confirms any capitalized candidate as a novel person, standing in for a
    real L3 adjudicator. Unlike :class:`_ProposesReservedTokenItselfAsPerson`
    above, this exercises the REAL, unmodified candidate pipeline end to end
    (``select_candidate_spans``) -- a reserved-form token never becomes a
    candidate for it in the first place (ADR-0052's family always carries a
    digit run, so it can never be Title-Case), which is exactly why this test
    proves AC1 against the realistic path, alongside the adversarial
    defense-in-depth path above.
    """

    def adjudicate(self, candidate):
        return L3Adjudication(is_entity=True, entity_type="person")


def test_a_reserved_form_token_coexists_with_a_genuine_referent_leak_clean():
    # Leak-audit clause A: a genuine, brand-new referent in the SAME
    # world-acting request still egresses only its reserved-namespace
    # containment surrogate -- never the real name -- while the unrelated,
    # already-present reserved-form token (e.g. echoed from an earlier
    # exchange) reaches the provider byte-for-byte unchanged. Uses a
    # different family prefix (``BFX``) than containment's own (``BFW``) so
    # this test isn't coupled to ADR-0060 amendment point 7's separate,
    # not-yet-landed per-referent numbering-stability decision -- out of
    # this issue's scope (#447 is point 6 only).
    mapping = SurrogateMapping()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    payload = {
        "tools": [{"name": "web_search_20250101"}],
        "messages": [
            {
                "role": "user",
                "content": (
                    "please also look up Mira Dalgaard, regarding the "
                    "BFX0099 note from before."
                ),
            }
        ],
    }

    blinded, session = blindfold_payload(
        payload, mapping, detector, None, world_acting=True
    )

    text = blinded["messages"][0]["content"]
    assert "Mira Dalgaard" not in text
    assert "regarding the BFX0099 note from before" in text  # untouched
    assert "Mira Dalgaard" in session.contained_reals()

    leak_gate(blinded, mapping)  # clause A: must not raise

    # Clause C (closed-world): the pass-through token was never recorded as
    # injected, so the stub provider echoing it back is not a restore target
    # and must not trip the post-restore resolution gate either.
    assert "BFX0099" not in session.injected
    provider_response = {
        "content": [{"type": "text", "text": "Noted, still re BFX0099."}]
    }
    restored = restore_response(provider_response, session)
    assert restored["content"][0]["text"] == "Noted, still re BFX0099."
    resolution_gate(restored, session)  # must not raise


def test_contain_fails_closed_on_a_reserved_form_value():
    # AC3: the contain() backstop -- ADR-0009 fail-closed shape, scrubbed
    # reason (the exception carries no real value, just like every other
    # internal-invariant violation this exception type already covers).
    session = ExchangeSession()

    with pytest.raises(L3DetectionInternalError):
        session.contain("BFW0007")


def test_contain_fails_closed_on_every_reserved_prefix_in_the_family():
    # Not just the containment prefix itself (BFW) -- the WHOLE closed
    # syntactic class (ADR-0052), any prefix a sibling fallback path can mint.
    session = ExchangeSession()

    for reserved in ("BFX0001", "BFR0002", "BFP0003", "BFT0004", "BFO0005", "BFK0006"):
        with pytest.raises(L3DetectionInternalError):
            session.contain(reserved)
