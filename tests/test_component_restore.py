"""ADR-0036: component restore — bounded, closed-world sub-token restore.

Whole-surrogate restore (ADR-0024) only restores a coalesced multi-word surrogate
(issue #162) when the provider echoes the whole string. When the provider
abbreviates a full-name/org surrogate ("Hallo Carla!" for injected "Carla
Distel"), the synthetic component must still restore to the real value —
Pass 2 over the per-exchange injected-surrogate set, bounded to a small,
self-minted, closed-world key set, never fuzzy matching.
"""

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.engine import ExchangeSession, restore_response, restore_tool_call_json
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


def _session_with(injected: dict[str, str]) -> ExchangeSession:
    session = ExchangeSession()
    for surrogate, real in injected.items():
        session.record(surrogate, real)
    return session


def _restore(text: str, session: ExchangeSession) -> str:
    provider_response = {"content": [{"type": "text", "text": text}]}
    restored = restore_response(provider_response, session)
    return restored["content"][0]["text"]


def test_bare_first_name_component_restores_by_positional_alignment():
    # ADR-0036 acceptance criterion 1: "Sarah Bergmann" injected as "Carla
    # Distel"; the provider abbreviates to bare "Carla" -> restores to "Sarah".
    session = _session_with({"Carla Distel": "Sarah Bergmann"})

    assert _restore("Hallo Carla!", session) == "Hallo Sarah!"


def test_component_with_unequal_word_counts_falls_back_to_the_full_real_value():
    # ADR-0036 acceptance criterion 2: when the surrogate and real value have
    # different word counts, positional alignment is meaningless, so a
    # restored component falls back to the full real value.
    session = _session_with({"Carla Distel": "Sarah Katharina Bergmann"})

    assert _restore("Hallo Carla!", session) == "Hallo Sarah Katharina Bergmann!"


def test_org_component_restores_by_positional_alignment():
    # ADR-0036 acceptance criterion 3: scope is all multi-word surrogates, not
    # just persons — a bare org-name component ("Baumgart") also restores
    # positionally to the real org's first word ("Nordwind").
    session = _session_with({"Baumgart Handel": "Nordwind Logistik"})

    assert _restore("per Baumgart bestellt", session) == "per Nordwind bestellt"


def test_generic_legal_form_component_is_not_registered_as_a_restore_key():
    # ADR-0036 acceptance criterion 4: a generic legal-form word ("Corporation")
    # is not distinctive, so it is never registered as a component restore key —
    # a response using it generically elsewhere must be left untouched. Unequal
    # word counts (3 real words vs. 2 surrogate words) mean an unfiltered
    # "Corporation" key would fall back to the full real value, so this would
    # visibly fail if the distinctiveness filter were missing.
    session = _session_with({"Baumgart Corporation": "Acme Global Holdings"})

    text = "Every Corporation must file its report."
    assert _restore(text, session) == text


def test_component_shared_by_two_surrogates_is_left_untouched():
    # ADR-0036 acceptance criterion 5: two injected surrogates share the
    # component "Carla" but resolve to different real people — ambiguous, so
    # neither registers it as a restore key. The bare token is left as-is.
    session = _session_with(
        {"Carla Distel": "Sarah Bergmann", "Carla Weber": "Petra Klein"}
    )

    text = "Carla called earlier."
    assert _restore(text, session) == text


def test_full_surrogate_pass_takes_precedence_over_the_component_pass():
    # ADR-0036: Pass 1 (full surrogates) runs first so a full match is never
    # clobbered by Pass 2 (components) — both occurring in the same response.
    session = _session_with({"Carla Distel": "Sarah Bergmann"})

    text = "Carla Distel called; Carla will follow up."
    assert _restore(text, session) == "Sarah Bergmann called; Sarah will follow up."


def test_component_restores_inside_tool_call_json_the_same_as_prose():
    # ADR-0036 acceptance criterion 8: behavior is identical across all three
    # restore paths — component restore shares _restore_text, so tool-call
    # JSON string args get the same Pass 2 treatment as prose.
    session = _session_with({"Carla Distel": "Sarah Bergmann"})

    restored = restore_tool_call_json({"recipient": "Carla"}, session)

    assert restored == {"recipient": "Sarah"}


class _StubAdjudicator:
    """Confirms exactly the whitelisted candidate texts; dismisses everything else."""

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=candidate.text in self._confirm)


def _make_abbreviating_upstream(
    recorded: list[httpx.Request], inbox: ReviewInbox
) -> UpstreamClient:
    """Stub upstream (leak-audit egress oracle) that replies with only the FIRST
    word of the coalesced surrogate it was sent — simulating the live abbreviation
    behavior this ADR fixes ("Hallo Carla!" for a full "Carla Distel" surrogate).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        # The mint step (which populates the inbox) runs before this upstream
        # call in the request pipeline, so the provisional surrogate is already
        # recorded by the time this handler executes.
        first_word = inbox.list()[0].provisional_surrogate.split()[0]
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": f"Hallo {first_word}!"}],
                "model": "claude-3-5-sonnet",
                "stop_reason": "end_turn",
            },
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_abbreviated_multi_word_surrogate_round_trips_through_the_request_path():
    # Full leak-audit shape: the coalesced multi-word entity ("Sarah Bergmann")
    # is minted as one surrogate; the (stubbed) provider abbreviates its reply to
    # just the surrogate's first word, and the client must still see the real
    # first name — never the raw surrogate fragment, never the real value egressing.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_StubAdjudicator(confirm={"Sarah", "Bergmann"}))

    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_abbreviating_upstream(
        recorded, inbox
    )
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
                        {"role": "user", "content": "Hi, ich bin Sarah Bergmann"}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200

    # Clause A: the stub upstream saw only the coalesced surrogate — zero real
    # tokens, not even a fragment of "Sarah Bergmann".
    egressed = recorded[0].content.decode("utf-8")
    assert "Sarah" not in egressed
    assert "Bergmann" not in egressed

    # Clause B/C: the client sees the real first name, closed-world restored —
    # not the raw surrogate fragment the provider actually returned.
    item = inbox.list()[0]
    surrogate_first_word = item.provisional_surrogate.split()[0]
    body = resp.json()
    restored_text = body["content"][0]["text"]
    assert restored_text == "Hallo Sarah!"
    assert surrogate_first_word not in restored_text
