"""ADR-0010's #417 amendment -- the load-bearing premise, pinned by a test.

The amendment's whole argument rests on one claim: **confirm already is the
protecting clearance**. ``confirm_review_item`` seeds the mapping and grows the
entity graph, so the deterministic pass (L2) reaches the value everywhere
afterwards and a block on that referent clears with it still protected -- no
third verdict is needed.

This premise is load-bearing, so ADR-0010 says it must be pinned by a test
rather than merely asserted: "confirming a blocking row clears the block *and*
keeps the value blinded. If that test ever cannot be written, this amendment's
reasoning has failed and the third-verdict question reopens."

Leak-audit clauses: A is the one this test exists to prove for the confirm path
specifically -- the referent stays blinded (never left in plaintext) on the
first ordinary request after confirm. B/C/D/E/F/G are unexercised here (no
restore, no mint, no fail-closed path) -- covered by the adjacent suites.
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_entity_graph,
    get_mapping,
    get_mapping_cipher,
    get_reidentify_store,
    get_review_inbox,
)
from blindfold.engine import LeakError, blindfold_payload, leak_gate
from blindfold.entity_graph import EntityGraph
from blindfold.reidentify import InMemoryReIdentificationStore
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


@pytest.mark.anyio
async def test_confirming_a_blocking_review_row_clears_the_block_and_keeps_the_referent_blinded():
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    entity_graph = EntityGraph()
    reidentify_store = InMemoryReIdentificationStore()
    real = "Kestrel Dynamics"
    item = inbox.upsert(
        real, context=f"Please brief {real} tomorrow.", workspace="acme"
    )

    # The row is genuinely blocking: a detection-miss payload replaying the real
    # value un-blindfolded is caught by the pre-egress leak gate and names
    # exactly this row (issue #417's own structural item_id linkage).
    leaky_outbound = {
        "messages": [{"role": "user", "content": f"Follow up with {real}."}]
    }
    with pytest.raises(LeakError) as excinfo:
        leak_gate(leaky_outbound, mapping, inbox)
    assert excinfo.value.item_id == item.id

    # Confirm the row through the real endpoint -- seeds the mapping and grows
    # the entity graph exactly as ADR-0010's amendment claims.
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store
    app.dependency_overrides[get_mapping_cipher] = lambda: None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
        ) as client:
            confirm_resp = await client.post(
                f"/v1/management/review-inbox/{item.id}/confirm"
            )
    finally:
        app.dependency_overrides.clear()
    assert confirm_resp.status_code == 200

    # The next request: the deterministic pass now reaches the referent
    # everywhere -- it stays blinded (never left in plaintext)...
    next_payload = {
        "model": "m",
        "messages": [{"role": "user", "content": f"Contact {real} again."}],
    }
    blinded, _session = blindfold_payload(next_payload, mapping)
    assert real not in json.dumps(blinded)

    # ...and the block clears: the same leak gate that named this row before
    # confirm no longer raises for this referent.
    leak_gate(blinded, mapping, inbox)
