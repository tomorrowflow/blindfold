"""Issue #292: Blindfold's own surrogates get re-detected as novel real entities.

Root cause (engine.py:701, ``_injected_surrogate_ranges``): the existing L3
"already-injected surrogate" guard (ADR-0022, issue #68) only skips a candidate
whose own occurrence position falls entirely inside a literal occurrence of the
*full* surrogate string in this hop's text. It says nothing about a **surrogate
component** (CONTEXT.md ~line 267, e.g. ``Carla`` in ``Carla Distel``) that shows
up on its own, at a *different* position, elsewhere in the same text -- e.g. a
worked example inside a doc/glossary hop the agent reads. L3 confirms that bare
component as a fresh novel person, the review inbox mints it a *second*
surrogate, and the resulting inbox item's ``real`` (the component, e.g.
``"Carla"``) is a substring of the live surrogate ``"Carla Distel"`` that
legitimately appears throughout the traffic -- so ``leak_gate``'s
``item.real in outbound_text`` check (issue #287) fires on every subsequent
request forever. Reproduced here directly at the ``blindfold_payload`` seam
(same one ``test_review_inbox_mint_hardening.py`` uses), not the full HTTP
app, since the defect and its fix are both in the mint pass.

Leak-audit clauses exercised:
- A: the stub upstream would receive only the live surrogate, never a second,
  spurious surrogate for a fragment of it.
- F (fail-closed unchanged): a genuinely novel real value that shares no span
  with surrogate-space still mints exactly as before -- the guard must not
  widen into a general "don't detect anything surrogate-shaped" policy
  (mirrors ``test_l3_surrogate_reblindfold_guard.py``'s companion "Vogt"
  case for the *other* direction's guard).
N/A this slice: B/C/D/E/G -- no restore/store-repair-path assertions in this
file (the repair-path acceptance criterion has its own test).
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.engine import blindfold_payload, leak_gate
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _ConfirmAnyNameShapedTokenAdjudicator:
    """Stub for a real local model: confirms EVERY candidate span as an entity.

    Mirrors ``test_l3_surrogate_reblindfold_guard.py``'s stub of the same name --
    a hand-scripted confirm-list can't reproduce this bug, since a real model
    has no such list: it says "yes, name-shaped" to a surrogate component
    exactly as readily as to a genuinely novel name.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=True)


def test_standalone_component_of_a_live_surrogate_is_never_minted_as_provisional_real():
    # "Referent Real" / "Carla Distel" stand in for #292's engagement-fixture
    # referent and its already-minted multi-word surrogate S.
    mapping = SurrogateMapping.from_pairs([("Referent Real", "Carla Distel")])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Referent Real asked me to loop in Carla about the schedule."
                ),
            }
        ],
    }

    blindfolded, session = blindfold_payload(payload, mapping, detector, inbox)

    # The bare component "Carla" (second occurrence, standalone -- not inside
    # the "Carla Distel" occurrence L2 just injected for "Referent Real") must
    # never become a provisional real entity.
    inbox_reals = {item.real for item in inbox.list()}
    assert "Carla" not in inbox_reals

    # The live surrogate itself must still egress untouched -- this is not a
    # second-surrogate-for-the-same-referent regression.
    text = blindfolded["messages"][0]["content"]
    assert "Carla Distel" in text
    assert "Referent Real" not in text

    # Companion assertion from the issue: once the component is never minted,
    # a later outbound payload that legitimately carries the live surrogate S
    # must not deadlock the pre-egress leak gate (issue #287's
    # ``item.real in outbound_text`` check over inbox items).
    leak_gate({"messages": [{"role": "user", "content": text}]}, mapping, inbox)


def test_genuinely_novel_real_value_sharing_no_span_with_surrogate_space_still_mints():
    # Guard over-broadness check (acceptance criterion 4): a real value that
    # shares NO span with any live surrogate must be detected and minted
    # exactly as before -- the fix must not become "never mint anything
    # surrogate-shaped", only "never mint a live surrogate's own component".
    mapping = SurrogateMapping.from_pairs([("Referent Real", "Carla Distel")])
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


def test_self_poisoning_loop_no_component_of_a_previously_emitted_surrogate_is_minted():
    # Acceptance criterion (the general form, not just the single-word repro
    # above): a payload containing a previously emitted surrogate must not
    # produce a provisional entity for the surrogate itself OR for any of its
    # components, wherever in the text they surface -- standalone, together,
    # or in either order.
    mapping = SurrogateMapping.from_pairs([("Referent Real", "Carla Distel")])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "I need Carla to review before Distel signs off, since "
                    "Carla Distel already scoped it."
                ),
            }
        ],
    }

    blindfold_payload(payload, mapping, detector, inbox)

    inbox_reals = {item.real for item in inbox.list()}
    assert inbox_reals.isdisjoint({"Carla", "Distel", "Carla Distel"})


def test_purge_surrogate_collisions_repairs_an_already_poisoned_persisted_inbox():
    # Acceptance criterion: a repair path for a store already poisoned before
    # this fix existed -- with mapping_cipher "none" the inbox is in-memory and
    # a restart clears it, but a persisted inbox carries the deadlock across
    # restarts with no way out except hand-rejecting every colliding item.
    mapping = SurrogateMapping.from_pairs([("Referent Real", "Carla Distel")])
    inbox = ReviewInbox()
    poisoned = inbox.upsert(
        "Carla",
        context="...abbreviates a full-name surrogate (Carla for Carla Distel)...",
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


def _make_echo_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        payload = json.loads(request.content.decode("utf-8"))
        echoed_text = payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": echoed_text}],
                "model": "claude-3-5-sonnet",
                "stop_reason": "end_turn",
            },
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_dismissed_collision_is_audited_with_a_scrubbed_reason_and_request_succeeds():
    # Acceptance criteria: the dismissal must be "visible in the dismissal log"
    # with a "distinguishable reason", and reason strings stay SEC-3 scrubbed
    # (reference the entity, never the plaintext) -- exercised end to end
    # through the app, the same seam ``test_l3_surrogate_reblindfold_guard.py``
    # uses, since the audit write lives in app.py's ``_mint_or_block`` (the one
    # place all mint call sites converge), not in the engine.
    mapping = SurrogateMapping.from_pairs([("Referent Real", "Carla Distel")])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmAnyNameShapedTokenAdjudicator())

    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_echo_upstream(recorded)
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Referent Real asked me to loop in Carla about "
                                "the schedule."
                            ),
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # Non-blocking: the request still succeeds; dismissal never fail-closes it.
    assert resp.status_code == 200

    collision_records = [
        r for r in audit_log.records if r.event == "dismissed-surrogate-collision"
    ]
    assert len(collision_records) == 1
    assert "Carla Distel" in collision_records[0].reason
    # Never the plaintext real value that triggered the dismissal.
    assert "Referent Real" not in collision_records[0].reason
