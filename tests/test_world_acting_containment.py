"""ADR-0060 §2-§3 (issue #410): a world-acting request carries reserved-namespace
surrogates for person and org, never a plausible-pool name.

Sibling #411 (merged, ADR-0060 §4/§5) already taught restore/resolution_gate to
never reverse a reserved-form surrogate -- this slice is the other half: at blind
time, identify a world-acting request structurally (ADR-0060 §2) and, in one,
draw every person/org surrogate from the reserved namespace instead of the
plausible pool (ADR-0060 §3) -- additive and non-durable: the entity's stable
surrogate is untouched, no new review-inbox row, no pool-cursor advance.

Leak-audit for this slice: clause A (pre-egress) is the direct subject -- a
contained request must carry zero plausible-pool person/org names outbound.
Clause E (reserved-namespace) is the mechanism, not a new decision (ADR-0052's
existing closed syntactic class). Clauses B/C (restore) are N/A here -- #411
already proved a reserved-form token is never restored; this slice never
restores anything. Fail-closed (F) is unaffected -- containment produces MORE
protection, not less, and never bypasses the leak gate.
"""

from __future__ import annotations

import pytest

from blindfold.engine import (
    ExchangeSession,
    _is_plausible_named_surrogate,
    blindfold_payload,
    is_world_acting_request_chat_completions,
    is_world_acting_request_messages,
)
from blindfold.l3 import L3Adjudication, L3Detector
from blindfold.review import _PROVISIONAL_ORG_POOL, _PROVISIONAL_POOL, ReviewInbox
from blindfold.store._mint import (
    _ORG_POOL,
    _PERSON_POOL,
    is_reserved_provisional_surrogate_form,
)
from blindfold.surrogates import SurrogateMapping


def test_session_contain_mints_a_reserved_form_token_stable_within_the_session():
    session = ExchangeSession()

    token = session.contain("Elena Voss")

    assert is_reserved_provisional_surrogate_form(token)
    assert session.contain("Elena Voss") == token  # same real -> same token


def test_session_contain_mints_distinct_tokens_for_distinct_reals():
    session = ExchangeSession()

    assert session.contain("Elena Voss") != session.contain("Rolf Brandt")


def test_plausible_named_surrogate_recognizes_person_and_org_pool_entries():
    assert _is_plausible_named_surrogate(_PERSON_POOL[0]) is True
    assert _is_plausible_named_surrogate(_ORG_POOL[0]) is True
    assert _is_plausible_named_surrogate(_PROVISIONAL_POOL[0]) is True
    assert _is_plausible_named_surrogate(_PROVISIONAL_ORG_POOL[0]) is True


def test_plausible_named_surrogate_rejects_reserved_and_pii_and_term_surrogates():
    assert _is_plausible_named_surrogate("BFP0007") is False
    assert _is_plausible_named_surrogate("pii-user-0001@blindfold.invalid") is False
    assert _is_plausible_named_surrogate("Not a real pool entry") is False


def test_a_request_declaring_only_tools_with_input_schema_is_not_world_acting():
    payload = {
        "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
        "messages": [],
    }
    assert is_world_acting_request_messages(payload) is False


def test_a_request_declaring_a_tool_without_input_schema_is_world_acting():
    payload = {
        "tools": [
            {"name": "read_file", "input_schema": {"type": "object"}},
            {"name": "web_search_20250101"},
        ],
        "messages": [],
    }
    assert is_world_acting_request_messages(payload) is True


def test_a_request_carrying_mcp_servers_is_world_acting():
    payload = {
        "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
        "mcp_servers": [{"name": "asana", "url": "https://mcp.example.com"}],
        "messages": [],
    }
    assert is_world_acting_request_messages(payload) is True


def test_a_request_with_no_declared_tools_is_not_world_acting():
    assert is_world_acting_request_messages({"messages": []}) is False


def test_chat_completions_mirrors_the_same_structural_test():
    with_schema = {
        "tools": [
            {"type": "function", "function": {"name": "read_file", "parameters": {}}}
        ]
    }
    without_schema = {
        "tools": [{"type": "function", "function": {"name": "web_search"}}]
    }
    assert is_world_acting_request_chat_completions(with_schema) is False
    assert is_world_acting_request_chat_completions(without_schema) is True


def _world_acting_search_payload(query_text: str) -> dict:
    # ADR-0060's own run-3 shape: a provider-executed tool declared (no
    # input_schema), the query as plain user prose in messages, not a tool
    # argument -- see test_run_3_shape below for the acceptance-criterion
    # variant with the tool ALSO present in this same request.
    return {
        "tools": [{"name": "web_search_20250101"}],
        "messages": [{"role": "user", "content": query_text}],
    }


def test_containment_substitutes_a_reserved_token_for_an_already_stable_person():
    # ADR-0060's motivating run-3 scenario: "Elena Voss" already has a stable,
    # plausible person surrogate from an earlier (non-world-acting) exchange.
    # A LATER world-acting request mentioning her in plain prose must carry
    # the reserved token, not the stable plausible surrogate -- no plausible-
    # pool name anywhere in the outbound payload.
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = _world_acting_search_payload("Please search for Elena Voss")

    blinded, session = blindfold_payload(payload, mapping, world_acting=True)

    text = blinded["messages"][0]["content"]
    assert "Elena Voss" not in text
    assert "Bernhard Vogt" not in text
    assert any(is_reserved_provisional_surrogate_form(token) for token in text.split())
    # Additive/non-durable: the entity's own stable surrogate is unchanged.
    assert mapping.surrogate_for("Elena Voss") == "Bernhard Vogt"


def test_containment_does_not_fire_on_a_non_world_acting_request():
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = {
        "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
        "messages": [{"role": "user", "content": "Please search for Elena Voss"}],
    }

    blinded, _session = blindfold_payload(payload, mapping, world_acting=False)

    text = blinded["messages"][0]["content"]
    assert text == "Please search for Bernhard Vogt"


class _ConfirmCapitalizedAsPerson:
    """Confirms any capitalized candidate token as a novel person -- stands in
    for a real L3 adjudicator so a brand-new referent (never before seen by
    ``mapping``/``inbox``) reaches the containment guard inside the L3
    mint loop of :func:`~blindfold.engine._blindfold_text`.
    """

    def adjudicate(self, candidate):
        return L3Adjudication(is_entity=True, entity_type="person")


def test_a_brand_new_person_confirmed_in_a_world_acting_request_is_contained():
    # AC: additive/non-durable -- a genuinely NEW referent, first ever seen in
    # a world-acting request, must be substituted with a reserved token, and
    # must never reach the review inbox: no new row, no pool-cursor advance.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    payload = _world_acting_search_payload("Please search for Corvin Adler")

    blinded, _session = blindfold_payload(
        payload, mapping, detector, inbox, world_acting=True
    )

    text = blinded["messages"][0]["content"]
    assert "Corvin Adler" not in text
    assert any(is_reserved_provisional_surrogate_form(token) for token in text.split())
    assert inbox.list() == []
    assert inbox._pool_positions == {}


def test_the_same_scenario_off_world_acting_mints_an_ordinary_provisional_row():
    # Negative control for the test above: identical detector/payload shape,
    # but a non-world-acting request -- confirms the brand-new referent DOES
    # mint an ordinary provisional row (the normal #410-untouched behavior).
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    payload = {
        "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
        "messages": [{"role": "user", "content": "Please search for Corvin Adler"}],
    }

    blindfold_payload(payload, mapping, detector, inbox, world_acting=False)

    assert len(inbox.list()) == 1
    assert inbox.list()[0].real == "Corvin Adler"


def test_non_named_kinds_are_unaffected_by_containment():
    # AC: dates/numbers (L1 deterministic PII, never a plausible named pool)
    # are unaffected by containment -- still blinded via the ordinary
    # reserved PII namespace either way.
    mapping = SurrogateMapping()
    payload = _world_acting_search_payload("Call +1-415-555-0134 about the meeting.")

    blinded, _session = blindfold_payload(payload, mapping, world_acting=True)

    text = blinded["messages"][0]["content"]
    assert "+1-415-555-0134" not in text
    assert "555-01" in text  # still the ordinary NANPA-fictional PII surrogate


def test_run_3_shape_prose_alongside_a_declared_provider_tool_is_contained():
    # AC: reproduces the run-3 shape -- the query is plain user prose
    # (messages[0], not a tool argument), alongside a declared provider-side
    # tool with no input_schema in the SAME request.
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = {
        "tools": [{"name": "web_search_20250101"}],
        "messages": [
            {"role": "user", "content": "Please look up Elena Voss for me."}
        ],
    }

    assert is_world_acting_request_messages(payload) is True

    blinded, _session = blindfold_payload(payload, mapping, world_acting=True)

    text = blinded["messages"][0]["content"]
    assert "Elena Voss" not in text
    assert "Bernhard Vogt" not in text


class _ConfirmKestrelOnlyWhenRegistering:
    """Mirrors ``test_mid_exchange_mint_reaches_earlier_hop.py``'s own stub: a
    context-sensitive adjudicator that only confirms "Kestrel" as an
    organization in a hop whose own text mentions "register" -- so hop 1's
    own L3 pass leaves it un-mintable, and only hop 2's pass mints it.
    """

    def adjudicate(self, candidate):
        if candidate.text != "Kestrel":
            return L3Adjudication(is_entity=False)
        if "register" in candidate.context:
            return L3Adjudication(is_entity=True, entity_type="organization")
        return L3Adjudication(is_entity=False)


def test_a_referent_contained_by_a_later_hop_is_also_contained_in_an_earlier_hop():
    # Cross-hop containment (mirrors #386/#387's own cross-hop mint-gap fix,
    # applied to the containment token instead of an ordinary provisional
    # one): hop 1 mentions "Kestrel" in a context L3 does not confirm; hop 2
    # confirms it. Both hops' OUTPUT must carry the reserved token, never
    # the literal real value -- including hop 1, already fully processed by
    # the time hop 2's mint happens.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmKestrelOnlyWhenRegistering())
    payload = {
        "tools": [{"name": "web_search_20250101"}],
        "messages": [
            {"role": "user", "content": "They said Kestrel emailed again about the invoice."},
            {"role": "user", "content": "Our vendor said please register Kestrel now."},
        ],
    }

    blinded, _session = blindfold_payload(
        payload, mapping, detector, inbox, world_acting=True
    )

    hop1_text = blinded["messages"][0]["content"]
    hop2_text = blinded["messages"][1]["content"]
    assert "Kestrel" not in hop1_text
    assert "Kestrel" not in hop2_text
    assert inbox.list() == []


async def _post_and_capture(payload: dict, mapping: SurrogateMapping) -> tuple[int, list]:
    """POST ``payload`` to ``/v1/messages`` through the real ASGI app, with a
    stub upstream, and return (status_code, recorded upstream requests) --
    the end-to-end leak-audit seam: what actually reached the "provider".
    """
    import httpx

    from blindfold.app import (
        app,
        get_l3_detector,
        get_mapping,
        get_review_inbox,
        get_upstream_client,
    )

    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "No results found."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    from blindfold.upstream import UpstreamClient

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    stub = UpstreamClient(base_url="http://upstream.test", client=client)

    app.dependency_overrides[get_upstream_client] = lambda: stub
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(
        _ConfirmCapitalizedAsPerson()
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as proxy_client:
            resp = await proxy_client.post("/v1/messages", json=payload)
    finally:
        app.dependency_overrides.clear()
    return resp.status_code, recorded


@pytest.mark.anyio
async def test_end_to_end_the_stub_upstream_never_receives_a_plausible_pool_name():
    # Leak-audit at the payload level over the outbound body (the issue's own
    # clause): a world-acting request through the REAL /v1/messages route
    # must reach the stub "provider" carrying only the reserved token, never
    # the plausible-pool surrogate.
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = {
        "model": "claude-3-5-sonnet",
        "tools": [{"name": "web_search_20250101"}],
        "messages": [
            {"role": "user", "content": "Please search for Elena Voss"}
        ],
    }

    status, recorded = await _post_and_capture(payload, mapping)

    assert status == 200
    assert len(recorded) == 1
    outbound_body = recorded[0].content.decode()
    assert "Elena Voss" not in outbound_body
    assert "Bernhard Vogt" not in outbound_body
    assert "BFW" in outbound_body


def test_full_round_trip_leak_gate_clean_and_resolution_gate_clean_on_echoed_token():
    # Leak-audit "verify pass is clean" clause: leak_gate must pass on the
    # contained outbound payload, and resolution_gate must not fail-close
    # when the (stubbed) provider echoes the opaque token back unresolved --
    # #411's own exemption (is_reserved_provisional_surrogate_form), now
    # exercised together with #410's containment in one full round trip.
    from blindfold.engine import leak_gate, resolution_gate, restore_response

    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = _world_acting_search_payload("Please search for Elena Voss")

    blinded, session = blindfold_payload(payload, mapping, world_acting=True)

    leak_gate(blinded, mapping)  # must not raise

    token = [tok for tok in session.injected if is_reserved_provisional_surrogate_form(tok)][0]
    provider_response = {
        "content": [{"type": "text", "text": f"No results found for {token}."}]
    }
    restored = restore_response(provider_response, session)

    assert restored["content"][0]["text"] == f"No results found for {token}."
    resolution_gate(restored, session)  # must not raise


def test_collect_containment_spans_honors_the_self_poisoning_exclude():
    # Issue #410 cycle-1 self-review: the cross-hop closing sweep
    # (_close_cross_hop_mint_gap) re-enters _blindfold_text's main branch
    # against ALREADY-BLINDED text, mirroring #394's own self-poisoning
    # guard -- a containment candidate occurring inside an ``exclude``d range
    # (an already-injected surrogate's own literal text) must never be
    # treated as a fresh containment match.
    from blindfold.engine import ExchangeSession, _collect_containment_spans

    mapping = SurrogateMapping()
    mapping.seed("Doe", "Bernhard Vogt")  # a plausible-pool surrogate
    session = ExchangeSession()
    text = "Alex Doe met with the team."
    start = text.index("Doe")
    end = start + len("Doe")

    excluded = _collect_containment_spans(
        text, mapping, None, session, None, exclude=[(start, end)]
    )
    unexcluded = _collect_containment_spans(text, mapping, None, session, None)

    assert excluded == []
    assert len(unexcluded) == 1
    assert (unexcluded[0].start, unexcluded[0].end) == (start, end)


def test_a_bare_word_component_of_a_contained_entity_is_also_contained():
    # Closes a residual found in cycle-1 self-review: a bare last-name
    # mention ("Doe", not the full "Jane Doe") would otherwise still surface
    # the aligned SURROGATE word ("Vogt", a fragment of the plausible person
    # name "Bernhard Vogt") via the #394 confirmed-component pass -- itself a
    # potentially locatable real surname, exactly ADR-0060's own concern.
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Bernhard Vogt")
    payload = _world_acting_search_payload("Please ask Doe about the invoice.")

    blinded, _session = blindfold_payload(payload, mapping, world_acting=True)

    text = blinded["messages"][0]["content"]
    assert "Doe" not in text
    assert "Vogt" not in text
    assert any(is_reserved_provisional_surrogate_form(token) for token in text.split())
