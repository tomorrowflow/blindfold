"""Issue #440 (confirmed live 2026-09-29, ADR-0060 re-verification #413): a mapped
real value inside a URL in **slug form** -- words joined by ``_`` (wiki style) or
``-`` (blog/CMS style), often lowercased, and/or percent-encoded -- escaped both the
deterministic blinder and the pre-egress leak gate. Blindfold recognised and
substituted the WRITTEN form only, so the written occurrence blinded while the slug
occurrence next to it (or in a later exchange carrying the same URL back) reached
the provider unchanged. ``test_confirmed_component_blinding.py`` had already named
and deliberately accepted exactly this gap ("the URL slug's underscore-glued
'Jane_Doe' ... left untouched deliberately") as the #386-shaped residual; this issue
closes it.

Recognised forms (named explicitly, per the issue's own instruction not to chase
every conceivable encoding): for a real/surrogate pair with two or more words,
``_``-joined and ``-``-joined, each in original case and lowercased, and the
percent-encoded rendering of each (``urllib.parse.quote``'s standard uppercase-hex
encoding -- a lowercase-hex percent-encoding convention is not separately covered).
A single-word value has no join convention and is out of scope. The substitution
renders the surrogate in the SAME shape the real was found in (same joiner, same
casing, same percent-encoded-or-not), so the URL stays well-formed; restore
reverses it back to the identical slug text, never to the canonical space-joined
form.

Leak-audit clauses:
- A/D: proven directly below -- no recognised slug/percent-encoded form of a
  mapped real reaches the stub provider, across confirmed entities, provisional
  same-exchange mints, and a non-ASCII name.
- B/C: restore is proven closed-world over these forms too -- a coincidental
  slug-shaped lookalike is not planted here (out of this issue's minimal scope),
  but the ordinary closed-world guarantee (only ``session``-recorded pairs are
  reversed) is exercised by every round-trip test below.
- F: ``leak_gate`` is proven to independently fail closed on a hand-built outbound
  payload carrying a mapped real in slug form ONLY, one test per recognised form --
  the gate's checked set, not just the blinder's rewritten set.
N/A this slice: E (no PII/reserved-namespace surrogate involved), G (fail-closed
L3-unavailable degrade -- this issue's mechanism is deterministic, not L3).
"""

from __future__ import annotations

import re

import pytest

from blindfold.engine import LeakError, blindfold_payload, leak_gate, restore_response
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.store._mint import is_reserved_provisional_surrogate_form
from blindfold.surrogates import SurrogateMapping


class _ConfirmSet:
    """Confirms exactly the (single-token) candidate texts named in ``confirm`` --
    mirrors ``tests/test_range_declared_collision.py``'s own stub of the same
    name. The engine's own adjacency coalescing (``_coalesce_adjacent_spans``)
    merges whitespace-adjacent confirmed single-word tokens into one multi-word
    entity -- confirming ``{"Kestrel", "Dynamics"}`` mints one "Kestrel Dynamics"
    referent from the WRITTEN prose occurrence. The underscore-joined URL slug
    ``Kestrel_Dynamics`` never reaches this stub at all: ``_WORD_RE`` (``\\w+``)
    matches it as one run, and ``_is_capitalized_token`` rejects it outright
    (``.isalpha()`` is False for a run containing ``_``) -- so an L3 mint can
    never independently touch the slug occurrence, isolating this test to
    issue #440's own deterministic slug pass.
    """

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(
            is_entity=candidate.text in self._confirm, entity_type="organization"
        )


@pytest.mark.parametrize(
    "leaky_url",
    [
        pytest.param("https://example.org/wiki/Jane_Doe", id="underscore-original-case"),
        pytest.param("https://example.org/wiki/Jane-Doe", id="hyphen-original-case"),
        pytest.param("https://example.org/wiki/jane-doe", id="hyphen-lowercased"),
    ],
)
def test_leak_gate_fails_closed_on_a_mapped_real_present_only_in_one_slug_form(leaky_url):
    # A hand-built outbound payload the blinder never touched -- proves the
    # GATE's own checked set includes slug forms, not just the blinder's own
    # rewritten output (mirrors test_confirmed_component_blinding.py's identical
    # gate-only-assertion style for bare components).
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")

    leaky_outbound = {
        "messages": [{"role": "user", "content": f"See {leaky_url} for details."}]
    }

    with pytest.raises(LeakError):
        leak_gate(leaky_outbound, mapping, None)


def test_leak_gate_fails_closed_on_a_mapped_real_present_only_in_percent_encoded_slug_form():
    mapping = SurrogateMapping()
    mapping.seed("Jana Müller", "Petra Grün")

    leaky_outbound = {
        "messages": [
            {
                "role": "user",
                "content": "See https://example.org/wiki/Jana_M%C3%BCller for details.",
            }
        ]
    }

    with pytest.raises(LeakError):
        leak_gate(leaky_outbound, mapping, None)


def test_underscore_joined_slug_of_a_confirmed_entity_is_blindfolded_in_a_url():
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": "See https://example.org/wiki/Jane_Doe for the source page.",
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, None, None)
    text = blinded["messages"][0]["content"]

    assert "Jane_Doe" not in text
    assert "https://example.org/wiki/Alex_Brenner" in text

    # Must not raise: the gate's checked surface is exactly what the blinder just
    # rewrote (ADR-0051 symmetry, now extended to this slug form).
    leak_gate(blinded, mapping, None)


def test_restore_reverses_the_underscore_slug_to_the_original_slug_text_not_the_canonical_form():
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "See https://example.org/wiki/Jane_Doe please."}
        ],
    }
    blinded, session = blindfold_payload(payload, mapping, None, None)

    response = {
        "content": [
            {"type": "text", "text": blinded["messages"][0]["content"]}
        ]
    }
    restored = restore_response(response, session)

    # Restore reverses to the exact original SLUG text -- "Jane_Doe", never the
    # canonical space-joined "Jane Doe" -- because what session.record recorded
    # for this occurrence is the slug-shaped pair itself.
    assert restored["content"][0]["text"] == "See https://example.org/wiki/Jane_Doe please."


def test_hyphen_joined_lowercased_slug_is_blindfolded_and_restored():
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "See https://blog.example.org/jane-doe-bio here."}
        ],
    }
    blinded, session = blindfold_payload(payload, mapping, None, None)
    text = blinded["messages"][0]["content"]

    assert "jane-doe" not in text
    assert "https://blog.example.org/alex-brenner-bio here." in text
    leak_gate(blinded, mapping, None)

    response = {"content": [{"type": "text", "text": text}]}
    restored = restore_response(response, session)
    assert (
        restored["content"][0]["text"]
        == "See https://blog.example.org/jane-doe-bio here."
    )


def test_percent_encoded_non_ascii_name_slug_is_blindfolded_and_restored():
    mapping = SurrogateMapping()
    # Both real and surrogate carry a non-ASCII character so restore is checked
    # against a percent-encoded SURROGATE literal too, not just a percent-decode.
    mapping.seed("Jana Müller", "Petra Grün")

    real_encoded = "Jana_M%C3%BCller"
    surrogate_encoded = "Petra_Gr%C3%BCn"

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": f"See https://example.org/wiki/{real_encoded} please.",
            }
        ],
    }
    blinded, session = blindfold_payload(payload, mapping, None, None)
    text = blinded["messages"][0]["content"]

    assert real_encoded not in text
    assert f"https://example.org/wiki/{surrogate_encoded} please." in text
    leak_gate(blinded, mapping, None)

    response = {"content": [{"type": "text", "text": text}]}
    restored = restore_response(response, session)
    assert (
        restored["content"][0]["text"]
        == f"See https://example.org/wiki/{real_encoded} please."
    )


def test_a_same_hop_provisional_mint_is_covered_in_its_own_slug_form_too():
    # AC5 / the confirmation comment's "SAME exchange as mint" hits: the live
    # defect was mint-then-carry-back, measured both in the SAME exchange that
    # minted the referent and in LATER exchanges carrying the same URL back.
    # This is the SAME-HOP half -- the written mention and the slug both sit in
    # the one message that triggers the mint, so the item does not exist in
    # ``inbox`` yet when this hop's OWN per-hop deterministic pass runs (L3
    # mints later, within that same call). It is only caught afterwards, once
    # every hop has had its turn to mint: #386's cross-hop closing sweep
    # (``engine._close_cross_hop_mint_gap``) unconditionally re-runs the
    # ordinary deterministic pipeline -- including issue #440's own slug pass
    # -- over every hop's already-blinded text, this one included, now that
    # ``inbox`` holds the item.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmSet({"Kestrel", "Dynamics"}))

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Kestrel Dynamics just published "
                    "https://example.org/wiki/Kestrel_Dynamics as their profile."
                ),
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)
    text = blinded["messages"][0]["content"]

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Kestrel Dynamics"

    assert "Kestrel Dynamics" not in text
    assert "Kestrel_Dynamics" not in text
    surrogate_slug = item.provisional_surrogate.replace(" ", "_")
    assert f"https://example.org/wiki/{surrogate_slug} as their profile." in text

    leak_gate(blinded, mapping, inbox)


def test_a_cross_hop_same_exchange_provisional_mint_is_covered_in_slug_form_on_a_later_hop():
    # The cross-hop half of the same live shape: the mint happens on an
    # earlier message of THIS SAME exchange, and the slug carrying it back
    # sits on a later message -- covered by the ordinary per-hop deterministic
    # provisional-slug pass, since the second hop's call already sees the
    # first hop's mint in ``inbox``.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmSet({"Kestrel", "Dynamics"}))

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Kestrel Dynamics is our new vendor."},
            {
                "role": "assistant",
                "content": "Understood, I'll note that.",
            },
            {
                "role": "user",
                "content": "Their profile is at https://example.org/wiki/Kestrel_Dynamics",
            },
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)
    item = inbox.list()[0]
    later_text = blinded["messages"][2]["content"]

    assert "Kestrel_Dynamics" not in later_text
    surrogate_slug = item.provisional_surrogate.replace(" ", "_")
    assert (
        later_text
        == f"Their profile is at https://example.org/wiki/{surrogate_slug}"
    )

    leak_gate(blinded, mapping, inbox)


def test_world_acting_request_redirects_a_confirmed_entitys_slug_to_the_containment_token_too():
    # ADR-0060 §3 (issue #410): a world-acting request must never carry a
    # plausible-pool name outbound, in ANY form -- the slug pass's own
    # ``contained`` branch (mirroring _collect_confirmed_component_spans's)
    # must fire for a slug match exactly as it does for a bare/whole-value
    # match.
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = {
        "tools": [{"name": "web_search_20250101"}],
        "messages": [
            {
                "role": "user",
                "content": "See https://example.org/wiki/Elena_Voss for her profile.",
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, world_acting=True)
    text = blinded["messages"][0]["content"]

    assert "Elena_Voss" not in text
    assert "Bernhard_Vogt" not in text
    # The containment token replaced the WHOLE slug span as one opaque unit.
    match = re.search(r"https://example\.org/wiki/(\S+) for her profile\.", text)
    assert match is not None
    assert is_reserved_provisional_surrogate_form(match.group(1))

    leak_gate(blinded, mapping, None)
