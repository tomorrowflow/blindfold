"""Issue #455 (ADR-0003, 2026-10-05 amendment, decision 1): an email address is a
structural unit, never spliced into. L2 (entity-graph substitution) runs strictly
before L1, so a known entity occurring INSIDE an email address's local part or
domain used to be replaced first, with its plausible (often multi-word, capitalized)
surrogate -- the address was no longer email-shaped, L1's anchored email detector
skipped it, and it was never minted as a reserved-namespace email surrogate. The
un-substituted remainder (local-part initials, domain fragments, the TLD) reached
the provider, and the leak gate had no minted real to check it against.

Decision: any span L1's email detector matches in the ORIGINAL text is claimed
whole, before L2 and the provisional-pair pass, and minted as one reserved-namespace
email surrogate -- restored whole. No later pass splices into a claimed email span.

Leak-audit clauses:
- A: proven directly below -- no part of the original address (local part, domain
  label, TLD) reaches the stub provider, whether the known entity sits in the
  domain or the local part.
- B/C: restore is closed-world -- the claimed email round-trips to the exact
  original address via ``session``, the ordinary ``session.injected`` lookup.
- D: the verify pass (``leak_gate``) is proven clean on the blinded payload.
- F: ``leak_gate`` fails closed on a hand-built outbound payload carrying the
  original address in the clear.
N/A this slice: E (the email surrogate is already reserved-namespace by
construction, ``mapping.mint_pii``'s own invariant, unchanged by this issue), G
(deterministic mechanism, no L3/fail-closed-degrade surface).
"""

from __future__ import annotations

import re

import pytest

from blindfold.engine import LeakError, blindfold_payload, leak_gate, restore_response
from blindfold.surrogates import SurrogateMapping


def test_known_org_in_email_domain_is_claimed_whole_not_spliced_into():
    # Northwind Analytics' org-pool surrogate is always multi-word (store/_mint.py's
    # _ORG_POOL), so splicing it into the domain in place would leave a space in
    # the address -- the exact structural break the ADR amendment describes.
    mapping = SurrogateMapping()
    mapping.seed("Northwind", "Team Atlas")

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": "Reach out to info@northwind.example for the invoice.",
            }
        ],
    }

    blinded, session = blindfold_payload(payload, mapping, None, None)
    text = blinded["messages"][0]["content"]

    assert "northwind" not in text.lower()
    assert "Team Atlas" not in text
    assert "info@" not in text
    match = re.search(r"pii-user-\d+@blindfold\.invalid", text)
    assert match is not None, text

    # Clean egress: no fragment of the original address reaches the provider.
    leak_gate(blinded, mapping, None)

    # Closed-world restore back to the exact original address.
    response = {
        "role": "assistant",
        "content": [{"type": "text", "text": f"Sent to {match.group(0)}."}],
    }
    restored = restore_response(response, session)
    assert restored["content"][0]["text"] == "Sent to info@northwind.example."


def test_known_persons_surname_in_email_local_part_is_claimed_whole_not_spliced_into():
    # "Doe" is a declared variation of "Jane Doe" -> "Alex Brenner" (a two-word
    # surrogate, same structural-break shape as the org case above, but in the
    # LOCAL part this time, and in the f.last@ style the issue names explicitly).
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")
    mapping.seed("Doe", "Alex Brenner")

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": "Reach out to j.doe@example.com for the invoice.",
            }
        ],
    }

    blinded, session = blindfold_payload(payload, mapping, None, None)
    text = blinded["messages"][0]["content"]

    assert "doe" not in text.lower()
    assert "Alex Brenner" not in text
    assert "j." not in text
    match = re.search(r"pii-user-\d+@blindfold\.invalid", text)
    assert match is not None, text

    leak_gate(blinded, mapping, None)

    response = {
        "role": "assistant",
        "content": [{"type": "text", "text": f"Sent to {match.group(0)}."}],
    }
    restored = restore_response(response, session)
    assert restored["content"][0]["text"] == "Sent to j.doe@example.com."


def test_email_with_no_known_entity_is_unchanged_from_today():
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": "Reach out to carol@example.org for the invoice.",
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, None, None)
    text = blinded["messages"][0]["content"]

    match = re.search(r"pii-user-\d+@blindfold\.invalid", text)
    assert match is not None, text
    assert "carol@example.org" not in text


def test_same_claimed_email_repeated_in_one_exchange_gets_one_surrogate():
    # ADR-0003's own one-span-per-occurrence contract
    # (test_l1_email_detection_yields_one_span_per_occurrence_not_per_value) must
    # still hold once the claim happens earlier, before L2.
    mapping = SurrogateMapping()
    mapping.seed("Northwind", "Team Atlas")

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Reach out to info@northwind.example, or again "
                    "info@northwind.example if that fails."
                ),
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, None, None)
    text = blinded["messages"][0]["content"]

    surrogates = set(re.findall(r"pii-user-\d+@blindfold\.invalid", text))
    assert len(surrogates) == 1
    assert text.count(next(iter(surrogates))) == 2


def test_known_entity_in_prose_next_to_an_email_is_still_substituted_normally():
    # Only the claimed email span is protected -- a genuine prose mention right
    # next to it is still ordinary L2 substitution.
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": "Jane Doe said reach out to doe@example.com for the invoice.",
            }
        ],
    }

    blinded, session = blindfold_payload(payload, mapping, None, None)
    text = blinded["messages"][0]["content"]

    assert "Jane Doe" not in text
    assert "Alex Brenner said" in text

    match = re.search(r"pii-user-\d+@blindfold\.invalid", text)
    assert match is not None, text

    leak_gate(blinded, mapping, None)

    response = {
        "role": "assistant",
        "content": [{"type": "text", "text": f"Told Alex Brenner, sent to {match.group(0)}."}],
    }
    restored = restore_response(response, session)
    assert restored["content"][0]["text"] == "Told Jane Doe, sent to doe@example.com."


def test_leak_gate_fails_closed_on_the_original_address_in_the_clear():
    # Clause F, gate-only: a hand-built outbound payload carrying the original
    # address the blinder never touched -- proves the gate's own checked set,
    # not just the blinder's rewritten output.
    mapping = SurrogateMapping()
    mapping.seed("Northwind", "Team Atlas")

    leaky_outbound = {
        "messages": [
            {"role": "user", "content": "Reach out to info@Northwind.example."}
        ]
    }

    with pytest.raises(LeakError):
        leak_gate(leaky_outbound, mapping, None)


@pytest.mark.parametrize(
    "address",
    ["INFO@NORTHWIND.EXAMPLE", "info@Northwind.example", "Info@NorthWind.Example"],
)
def test_known_org_in_email_domain_is_claimed_whole_regardless_of_case(address):
    mapping = SurrogateMapping()
    mapping.seed("Northwind", "Team Atlas")

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": f"Reach out to {address} for the invoice."}],
    }

    blinded, _session = blindfold_payload(payload, mapping, None, None)
    text = blinded["messages"][0]["content"]

    assert "northwind" not in text.lower()
    assert "Team Atlas" not in text

    assert re.search(r"pii-user-\d+@blindfold\.invalid", text) is not None, text
