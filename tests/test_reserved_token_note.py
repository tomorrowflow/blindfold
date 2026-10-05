"""ADR-0060 amendment 2026-10-05, decision 8 (issue #449): the reserved-token note.

When an outbound request's content carries any reserved-form token (containment,
ADR-0060 §3, or pool exhaustion, ADR-0052), Blindfold appends a fixed, value-free
sentence to the end of ``system`` -- telling the model that such identifiers are
privacy placeholders, not searchable/resolvable, and that a result concerning one
is protection, not a tool fault.

Reviewer-found hole (cycle 2 -> cycle 3): ``blindfold_payload``/
``blindfold_chat_completions_payload`` used to append the note themselves, as
their own last step -- BEFORE ``leak_gate`` ever ran on the result. Appending it
anywhere other than onto an existing text leaf (no ``system`` at all, or a
block-list ``system`` with no text block) adds a leaf the blind pass never
created, which can silently cancel out, in AGGREGATE leaf count alone, an
unrelated leaf stripped elsewhere in the same request -- ``leak_gate``'s
leaf-count backstop (issue #416) only fails closed on a count DISAGREEMENT, not
a coincidental agreement masking real drift. The fix: these two functions are no
longer called from inside ``blindfold_payload``/``blindfold_chat_completions_
payload`` at all -- ``app.py``'s shared exchange sequence calls them itself,
strictly AFTER ``leak_gate`` has already run (and raised or passed). Tests below
mirror that two-step production order: blind -> gate -> (only then) append.

Leak-audit clauses:
- A: the note itself carries no real value and no reserved-token literal (proven by
  construction: it is a fixed constant with no interpolation).
- D: the verify pass (leak_gate) stays clean -- proven on the note-FREE payload
  ``leak_gate`` actually ever sees in production, never on an already-noted one
  except where the note only ever extends an existing leaf (which changes no
  leaf's position or count, so staying clean afterward too is a true, stronger
  property worth also pinning there).
N/A this slice: B/C (restore acts on the provider *response*; the note lives only in
the outbound *request* and is never a surrogate, so nothing restores it), E (not a
surrogate), F (fail-closed policy untouched), G (mapping secrecy, unrelated).
"""

from __future__ import annotations

import pytest

from blindfold.engine import (
    RESERVED_TOKEN_NOTE,
    ExchangeSession,
    LeakError,
    append_reserved_token_note_chat_completions,
    append_reserved_token_note_messages,
    blindfold_chat_completions_payload,
    blindfold_payload,
    chat_completions_tool_container,
    leak_gate,
)
from blindfold.l3 import L3Adjudication, L3Detector
from blindfold.review import _PROVISIONAL_POOL, ReviewInbox
from blindfold.surrogates import SurrogateMapping

_POOL_SIZE = len(_PROVISIONAL_POOL)


class _ConfirmCapitalizedAsPerson:
    """Confirms any capitalized candidate token as a novel person -- stands in
    for a real L3 adjudicator (mirrors tests/test_world_acting_containment.py's
    own stub) so this test can prove the note's own prose never reaches L3 at
    all: it is appended only after every detection pass has already run.
    """

    def adjudicate(self, candidate):
        return L3Adjudication(is_entity=True, entity_type="person")


def _world_acting_payload(system, query_text: str) -> dict:
    payload = {
        "tools": [{"name": "web_search_20250101"}],
        "messages": [{"role": "user", "content": query_text}],
    }
    if system is not None:
        payload["system"] = system
    return payload


def test_note_appended_after_a_string_system_when_a_reserved_token_is_present():
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = _world_acting_payload(
        "You are a helpful assistant.", "Please search for Elena Voss"
    )

    blinded, session = blindfold_payload(payload, mapping, world_acting=True)
    assert blinded["system"] == "You are a helpful assistant."
    leak_gate(blinded, mapping, session=session)  # must not raise

    append_reserved_token_note_messages(blinded, session)

    assert blinded["system"] == (
        "You are a helpful assistant.\n\n" + RESERVED_TOKEN_NOTE
    )


def test_note_appended_onto_the_last_text_block_of_a_block_list_system():
    # Appended onto the EXISTING last text block's own string, not a new
    # block -- keeps the leaf count leak_gate's ADR-0051 #406 position-pairing
    # relies on unchanged (see _append_note_to_text_blocks's own docstring).
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = _world_acting_payload(
        [{"type": "text", "text": "You are a helpful assistant."}],
        "Please search for Elena Voss",
    )

    blinded, session = blindfold_payload(payload, mapping, world_acting=True)
    leak_gate(blinded, mapping, session=session)  # must not raise

    append_reserved_token_note_messages(blinded, session)

    assert blinded["system"] == [
        {
            "type": "text",
            "text": "You are a helpful assistant.\n\n" + RESERVED_TOKEN_NOTE,
        },
    ]
    # This shape never adds a leaf (the note extends the existing text block's
    # own string), so leak_gate staying clean even AFTER the note is appended
    # is also a true property -- unlike the "adds a new leaf" shapes below,
    # which production never asks leak_gate to tolerate.
    leak_gate(blinded, mapping, session=session)  # must not raise


def test_note_appended_as_a_new_block_when_the_system_block_list_has_no_text_block():
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = _world_acting_payload(
        [{"type": "image", "source": {"type": "url", "url": "https://example.com/x.png"}}],
        "Please search for Elena Voss",
    )

    blinded, session = blindfold_payload(payload, mapping, world_acting=True)
    assert blinded["system"] == [
        {"type": "image", "source": {"type": "url", "url": "https://example.com/x.png"}},
    ]
    leak_gate(blinded, mapping, session=session)  # must not raise

    # This shape DOES add a new leaf -- production never calls leak_gate again
    # after this point; see the regression test below for exactly why not.
    append_reserved_token_note_messages(blinded, session)

    assert blinded["system"] == [
        {"type": "image", "source": {"type": "url", "url": "https://example.com/x.png"}},
        {"type": "text", "text": RESERVED_TOKEN_NOTE},
    ]


def test_no_note_when_no_reserved_token_is_present():
    mapping = SurrogateMapping()
    payload = {
        "system": "You are a helpful assistant.",
        "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
        "messages": [{"role": "user", "content": "Please say hello."}],
    }

    blinded, session = blindfold_payload(payload, mapping, world_acting=False)
    assert blinded["system"] == "You are a helpful assistant."

    append_reserved_token_note_messages(blinded, session)

    assert blinded["system"] == "You are a helpful assistant."


def test_two_consecutive_requests_with_tokens_produce_identical_note_text_and_placement():
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = _world_acting_payload(
        "You are a helpful assistant.", "Please search for Elena Voss"
    )

    first, s1 = blindfold_payload(payload, mapping, world_acting=True)
    second, s2 = blindfold_payload(payload, mapping, world_acting=True)
    append_reserved_token_note_messages(first, s1)
    append_reserved_token_note_messages(second, s2)

    assert first["system"] == second["system"]


def test_the_note_passes_the_leak_gate():
    # Production order: blind, then gate, then (only once the gate has
    # already passed) append the note.
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = _world_acting_payload(
        "You are a helpful assistant.", "Please search for Elena Voss"
    )

    blinded, session = blindfold_payload(payload, mapping, world_acting=True)

    leak_gate(blinded, mapping, session=session)  # must not raise

    append_reserved_token_note_messages(blinded, session)
    assert RESERVED_TOKEN_NOTE in blinded["system"]


def test_the_note_passes_the_leak_gate_when_appended_onto_a_leaf_the_blinder_also_wrote_into():
    # Regression guard for _append_note_to_text_blocks's own rationale: the
    # SAME system leaf carries both an ordinary blindfolded entity (a ranged
    # splice the original blind pass recorded) and, appended afterward, the
    # note -- leak_gate's ADR-0051 #406 leaf-position pairing must still find
    # that leaf (no leaf-count drift from a brand-new block) and pass clean.
    # Unlike the "adds a new leaf" shapes, this one changes no leaf's position
    # or count, so leak_gate staying clean AFTER the note is appended too is a
    # true, worth-pinning property (even though production never re-gates).
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    mapping.seed("Rolf Brandt", "Theo Lindner")
    payload = _world_acting_payload(
        [{"type": "text", "text": "You work for Rolf Brandt."}],
        "Please search for Elena Voss",
    )

    blinded, session = blindfold_payload(payload, mapping, world_acting=True)
    leak_gate(blinded, mapping, session=session)  # must not raise

    append_reserved_token_note_messages(blinded, session)

    system_text = blinded["system"][0]["text"]
    assert "Rolf Brandt" not in system_text
    assert "Theo Lindner" in system_text
    assert RESERVED_TOKEN_NOTE in system_text

    leak_gate(blinded, mapping, session=session)  # must not raise


def test_the_note_is_never_itself_detected_or_minted():
    # An adjudicator that confirms EVERY capitalized token as a person would
    # mint "Some"/"They" etc. out of the note's own prose if the note were
    # present before detection ran. Appending it only after blind+gate have
    # both already run means no inbox item ever traces back to it.
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    payload = _world_acting_payload(
        "You are a helpful assistant.", "Please search for Elena Voss"
    )

    blinded, session = blindfold_payload(
        payload, mapping, detector, inbox, world_acting=True
    )
    leak_gate(blinded, mapping, inbox, session)  # must not raise

    append_reserved_token_note_messages(blinded, session)

    assert RESERVED_TOKEN_NOTE in blinded["system"]
    assert inbox.list() == []


def _chat_completions_world_acting_payload(system_content, query_text: str) -> dict:
    return {
        "tools": [{"type": "function", "function": {"name": "web_search"}}],
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": query_text},
        ],
    }


def test_chat_completions_string_system_gets_the_note_appended():
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = _chat_completions_world_acting_payload(
        "You are a helpful assistant.", "Please search for Elena Voss"
    )

    blinded, session = blindfold_chat_completions_payload(
        payload, mapping, world_acting=True
    )
    leak_gate(
        blinded, mapping, session=session, tool_container=chat_completions_tool_container
    )  # must not raise

    append_reserved_token_note_chat_completions(blinded, session)

    system_message = blinded["messages"][0]
    assert system_message["role"] == "system"
    assert system_message["content"] == (
        "You are a helpful assistant.\n\n" + RESERVED_TOKEN_NOTE
    )


def test_chat_completions_block_list_system_gets_the_note_onto_the_last_text_block():
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = _chat_completions_world_acting_payload(
        [{"type": "text", "text": "You are a helpful assistant."}],
        "Please search for Elena Voss",
    )

    blinded, session = blindfold_chat_completions_payload(
        payload, mapping, world_acting=True
    )
    leak_gate(
        blinded, mapping, session=session, tool_container=chat_completions_tool_container
    )  # must not raise

    append_reserved_token_note_chat_completions(blinded, session)

    system_message = blinded["messages"][0]
    assert system_message["content"] == [
        {
            "type": "text",
            "text": "You are a helpful assistant.\n\n" + RESERVED_TOKEN_NOTE,
        },
    ]
    leak_gate(
        blinded, mapping, session=session, tool_container=chat_completions_tool_container
    )  # must not raise


def test_chat_completions_no_note_when_no_reserved_token_present():
    mapping = SurrogateMapping()
    payload = {
        "tools": [
            {
                "type": "function",
                "function": {"name": "read_file", "parameters": {"type": "object"}},
            }
        ],
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Please say hello."},
        ],
    }

    blinded, session = blindfold_chat_completions_payload(
        payload, mapping, world_acting=False
    )
    assert blinded["messages"][0]["content"] == "You are a helpful assistant."

    append_reserved_token_note_chat_completions(blinded, session)

    assert blinded["messages"][0]["content"] == "You are a helpful assistant."


def test_messages_dialect_with_no_system_at_all_still_carries_the_note():
    # This shape DOES add a new leaf (no system existed for the blind pass to
    # visit) -- see the regression test below for exactly why leak_gate is
    # never asked to tolerate this payload AFTER the note is appended.
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = _world_acting_payload(None, "Please search for Elena Voss")
    assert "system" not in payload

    blinded, session = blindfold_payload(payload, mapping, world_acting=True)
    assert "system" not in blinded
    leak_gate(blinded, mapping, session=session)  # must not raise

    append_reserved_token_note_messages(blinded, session)

    assert blinded["system"] == RESERVED_TOKEN_NOTE


def test_chat_completions_with_no_system_message_inserts_one():
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = {
        "tools": [{"type": "function", "function": {"name": "web_search"}}],
        "messages": [{"role": "user", "content": "Please search for Elena Voss"}],
    }

    blinded, session = blindfold_chat_completions_payload(
        payload, mapping, world_acting=True
    )
    leak_gate(
        blinded, mapping, session=session, tool_container=chat_completions_tool_container
    )  # must not raise

    append_reserved_token_note_chat_completions(blinded, session)

    assert blinded["messages"][0] == {"role": "system", "content": RESERVED_TOKEN_NOTE}
    assert blinded["messages"][1]["role"] == "user"


async def _post_and_capture(payload: dict, mapping: SurrogateMapping) -> tuple[int, list]:
    """POST ``payload`` to ``/v1/messages`` through the real ASGI app, with a
    stub upstream, and return (status_code, recorded upstream requests) -- the
    end-to-end leak-audit seam (mirrors
    tests/test_world_acting_containment.py's own helper of the same name):
    what actually reached the "provider".
    """
    import httpx

    from blindfold.app import (
        app,
        get_l3_detector,
        get_mapping,
        get_review_inbox,
        get_upstream_client,
    )
    from blindfold.l3 import L3Detector
    from blindfold.upstream import UpstreamClient

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
async def test_end_to_end_the_stub_upstream_receives_the_note_appended_once():
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = {
        "model": "claude-3-5-sonnet",
        "system": "You are a helpful assistant.",
        "tools": [{"name": "web_search_20250101"}],
        "messages": [{"role": "user", "content": "Please search for Elena Voss"}],
    }

    status, recorded = await _post_and_capture(payload, mapping)

    assert status == 200
    assert len(recorded) == 1
    outbound = recorded[0].content.decode()
    assert outbound.count(RESERVED_TOKEN_NOTE) == 1
    assert "Elena Voss" not in outbound
    sent = recorded[0].content
    import json

    sent_payload = json.loads(sent)
    assert sent_payload["system"] == (
        "You are a helpful assistant.\n\n" + RESERVED_TOKEN_NOTE
    )


async def _post_and_capture_chat_completions(
    payload: dict, mapping: SurrogateMapping
) -> tuple[int, list]:
    """Chat Completions dialect counterpart of ``_post_and_capture`` -- proves
    ``app.py``'s ``/v1/chat/completions`` wiring threads
    ``append_reserved_token_note_chat_completions`` (not the Messages-dialect
    default) into the shared exchange sequence.
    """
    import httpx

    from blindfold.app import (
        app,
        get_l3_detector,
        get_mapping,
        get_openai_upstream_client,
        get_review_inbox,
    )
    from blindfold.l3 import L3Detector
    from blindfold.upstream import UpstreamClient

    scripted_response = {
        "id": "chatcmpl_1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "No results found."},
                "finish_reason": "stop",
            }
        ],
    }
    recorded: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    stub = UpstreamClient(base_url="http://upstream.test", client=client)

    app.dependency_overrides[get_openai_upstream_client] = lambda: stub
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
            resp = await proxy_client.post("/v1/chat/completions", json=payload)
    finally:
        app.dependency_overrides.clear()
    return resp.status_code, recorded


@pytest.mark.anyio
async def test_chat_completions_end_to_end_the_stub_upstream_receives_the_note_appended_once():
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = {
        "model": "m",
        "tools": [{"type": "function", "function": {"name": "web_search"}}],
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Please search for Elena Voss"},
        ],
    }

    status, recorded = await _post_and_capture_chat_completions(payload, mapping)

    assert status == 200
    assert len(recorded) == 1
    outbound = recorded[0].content.decode()
    assert outbound.count(RESERVED_TOKEN_NOTE) == 1
    assert "Elena Voss" not in outbound
    import json

    sent_payload = json.loads(recorded[0].content)
    assert sent_payload["messages"][0]["content"] == (
        "You are a helpful assistant.\n\n" + RESERVED_TOKEN_NOTE
    )


def test_a_pool_exhaustion_fallback_token_also_triggers_the_note_without_world_acting():
    # ADR-0060 amendment point 8: the note fires on EITHER reserved-form
    # source -- containment (covered above) or ADR-0052 pool exhaustion. This
    # mirrors tests/test_opaque_reserved_surrogate.py's own fixture (fill the
    # named pool, then mint one referent past it) with no world-acting tool
    # declared at all, to isolate the exhaustion path from containment.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    for i in range(_POOL_SIZE):
        inbox.upsert(
            f"Filler Person {i}", context=f"...Filler Person {i}...", entity_type="person"
        )
    referent_item = inbox.upsert(
        "Referent7", context="...summarise Referent7...", entity_type="person"
    )
    assert referent_item.provisional_surrogate == f"BFX{_POOL_SIZE:04d}"

    payload = {
        "system": "You are a helpful assistant.",
        "messages": [{"role": "user", "content": "Please summarise Referent7."}],
    }

    blinded, session = blindfold_payload(payload, mapping, None, inbox, world_acting=False)
    leak_gate(blinded, mapping, inbox, session)  # must not raise

    append_reserved_token_note_messages(blinded, session)

    assert RESERVED_TOKEN_NOTE in blinded["system"]


def test_note_fires_on_a_history_carried_reserved_token_the_main_conversation_wrote_back():
    # Reviewer finding, cycle 1 -> this cycle: `_any_reserved_token_injected` only
    # checked `session.injected` -- every surrogate THIS exchange actually spliced
    # in. A main-conversation request (not world-acting) never injects anything
    # itself; its own prior turn's reserved token is only present because the
    # CLIENT echoed it back in message history. `session.injected` is empty for
    # this request, so the note never fired even though a reserved-form token is
    # plainly present in the outbound payload -- the exact clause ADR-0060
    # amendment #8 names ("a request whose content carries any reserved-form
    # token", not "this exchange's own injections").
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    payload = {
        "system": "You are a helpful assistant.",
        "messages": [
            {"role": "assistant", "content": "I searched for BFW0000 and found nothing."},
            {"role": "user", "content": "Why?"},
        ],
    }

    blinded, session = blindfold_payload(
        payload, mapping, None, inbox, world_acting=False
    )

    assert session.injected == {}
    leak_gate(blinded, mapping, session=session)  # must not raise

    append_reserved_token_note_messages(blinded, session)

    assert RESERVED_TOKEN_NOTE in blinded["system"]
    assert inbox.list() == []


def test_note_fires_on_a_history_carried_reserved_token_inside_a_tool_result():
    # Same gap as the assistant-text case above, for a tool_result block's own
    # nested content -- the other shape the reviewer named explicitly.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    payload = {
        "system": "You are a helpful assistant.",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [
                            {"type": "text", "text": "No record found for BFW0000."}
                        ],
                    }
                ],
            },
        ],
    }

    blinded, session = blindfold_payload(
        payload, mapping, None, inbox, world_acting=False
    )

    assert session.injected == {}
    leak_gate(blinded, mapping, session=session)  # must not raise

    append_reserved_token_note_messages(blinded, session)

    assert RESERVED_TOKEN_NOTE in blinded["system"]
    assert inbox.list() == []


def test_chat_completions_note_fires_on_a_history_carried_reserved_token():
    # Chat Completions dialect counterpart -- reviewer asked for coverage in
    # both dialects.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    payload = {
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "assistant", "content": "I searched for BFW0000 and found nothing."},
            {"role": "user", "content": "Why?"},
        ],
    }

    blinded, session = blindfold_chat_completions_payload(
        payload, mapping, None, inbox, world_acting=False
    )

    assert session.injected == {}
    leak_gate(
        blinded, mapping, session=session, tool_container=chat_completions_tool_container
    )  # must not raise

    append_reserved_token_note_chat_completions(blinded, session)

    system_message = blinded["messages"][0]
    assert RESERVED_TOKEN_NOTE in system_message["content"]
    assert inbox.list() == []


def test_leak_gate_catches_a_genuine_miss_the_notes_own_new_leaf_would_have_masked():
    # Reviewer finding, cycle 2 -> this cycle: when the note was appended
    # INSIDE blindfold_payload (its own last step), a brand-new leaf the note
    # itself adds (this payload has no `system` at all) could silently cancel
    # out, in AGGREGATE leaf count only, an unrelated leaf
    # `_strip_schema_structural_tokens` removes elsewhere in the very same
    # request (an `enum`-nested schema description) -- leak_gate's leaf-count
    # backstop (issue #416) agreed by coincidence while the position pairing
    # in between had already drifted, excusing a genuine miss ("Ridge
    # Referent") against a stripped leaf's unrelated surrogate range ("Aurora
    # Systems"). Reproduced against pre-fix code (manually) with: no system;
    # a history-carried "BFW0000"; this exact tool/enum shape; "Ridge
    # Referent" seeded into `mapping` only after blindfold_payload ran (a
    # genuine miss, byte-for-byte as typed) -- leak_gate incorrectly passed.
    #
    # The fix is structural: blindfold_payload no longer appends the note at
    # all, so this payload's `system` stays absent and leak_gate's mirror
    # walk never has a phantom leaf to begin with, for this or any other
    # payload shape.
    mapping = SurrogateMapping.from_pairs([("Org A", "Aurora Systems")])
    payload = {
        "model": "m",
        "messages": [
            {"role": "assistant", "content": "I searched for BFW0000 and found nothing."},
            {"role": "user", "content": "Ridge Referent stays as typed."},
        ],
        "tools": [
            {
                "name": "t1",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "p": {"enum": [{"description": "Org A is on file."}]},
                    },
                },
            }
        ],
    }
    assert "system" not in payload

    blinded, session = blindfold_payload(payload, mapping, None, None, world_acting=False)

    # The note is no longer blindfold_payload's own business.
    assert "system" not in blinded
    assert (
        blinded["tools"][0]["input_schema"]["properties"]["p"]["enum"][0]["description"]
        == "Aurora Systems is on file."
    )

    # Seeded only after the blind pass ran -- a genuine miss, byte-for-byte.
    mapping.seed("Ridge Referent", "Some Other Surrogate")

    with pytest.raises(LeakError):
        leak_gate(blinded, mapping, None, session)
