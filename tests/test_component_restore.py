"""ADR-0036: component restore — bounded, closed-world sub-token restore.

Whole-surrogate restore (ADR-0024) only restores a coalesced multi-word surrogate
(issue #162) when the provider echoes the whole string. When the provider
abbreviates a full-name/org surrogate ("Hallo Carla!" for injected "Carla
Distel"), the synthetic component must still restore to the real value —
the second pass over the per-exchange injected-surrogate set, bounded to a small,
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


def test_component_with_unequal_word_counts_is_not_registered_as_a_restore_key():
    # issue #304 (ADR-0036 amendment): when the surrogate and real value have
    # different word counts, positional alignment is meaningless -- there is no
    # correspondence between a component's position and any single real word, so
    # the pair contributes NO component keys at all. The prior behavior (falling
    # back to the *whole* real value) is exactly the defect #304 reports: it lets
    # an ordinary word like "Analytics" become a restore key for an unrelated real
    # value. The bare abbreviated component is left untouched (a synthetic-name
    # quality cost, never a leak) rather than risk a wrong whole-value donation.
    session = _session_with({"Carla Distel": "real-word-1 real-word-2 real-word-3"})

    assert _restore("Hallo Carla!", session) == "Hallo Carla!"


def test_org_component_restores_by_positional_alignment():
    # ADR-0036 acceptance criterion 3: scope is all multi-word surrogates, not
    # just persons — a bare org-name component ("Baumgart") also restores
    # positionally to the real org's first word ("Nordwind").
    session = _session_with({"Baumgart Handel": "Nordwind Logistik"})

    assert _restore("per Baumgart bestellt", session) == "per Nordwind bestellt"


def test_generic_legal_form_component_is_not_registered_as_a_restore_key():
    # ADR-0036 acceptance criterion 4: a generic legal-form word ("Corporation")
    # is not distinctive, so it is never registered as a component restore key —
    # a response using it generically elsewhere must be left untouched. Word
    # counts are equal (2 vs. 2) on purpose -- alignment alone would otherwise
    # register "Corporation" -> "Holdings", so this exercises the distinctiveness
    # filter specifically rather than incidentally passing via #304's
    # unaligned-pair exclusion.
    session = _session_with({"Baumgart Corporation": "Acme Holdings"})

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


def test_run_7_shape_restores_to_northwind_analytics_never_northwind_vault():
    # issue #304 acceptance criterion 1: replaying run 7's exact live shape.
    # Injected pairs "Nordkap Systeme GmbH" -> "Northwind Analytics" and
    # "Moosburg Analytics" -> "Vault" in the same exchange; the model's output
    # contains the full first surrogate. The client must see exactly
    # "Northwind Analytics" -- never "Northwind Vault".
    session = _session_with(
        {
            "Nordkap Systeme GmbH": "Northwind Analytics",
            "Moosburg Analytics": "Vault",
        }
    )

    text = "Prepared by Priya Nadkarni, Lead architect -- Nordkap Systeme GmbH"
    restored = _restore(text, session)

    assert restored == "Prepared by Priya Nadkarni, Lead architect -- Northwind Analytics"
    assert "Vault" not in restored


def test_analytics_is_not_a_restore_key_for_vault():
    # issue #304 acceptance criterion: the live run-7 shape. "Moosburg
    # Analytics" (2-word surrogate) -> "Vault" (1-word real) is length-mismatched,
    # so neither "Moosburg" nor "Analytics" is ever registered as a component
    # restore key -- an ordinary word ("Analytics") must never become a restore
    # key for an unrelated real value ("Vault").
    session = _session_with({"Moosburg Analytics": "Vault"})

    text = "Please review the Analytics summary before the Moosburg call."
    assert _restore(text, session) == text


def test_pass_2_does_not_match_inside_pass_1s_own_output():
    # issue #304 acceptance criterion: the second pass must never re-scan text the
    # first pass has already produced. Two *independent*, individually valid pairs:
    # pair A's full surrogate "Nordkap Systeme" restores to real "Northwind Analytics"
    # (the first pass). Pair B is a genuinely aligned pair contributing a legitimate
    # component key "Analytics" -> "Baz" (the second pass). "Analytics" never occurs
    # in the model's actual output -- it only exists in the text because the first
    # pass just inserted it as part of pair A's real value. The second pass must not
    # treat that inserted occurrence as a
    # match, or the correctly restored "Northwind Analytics" gets corrupted into
    # "Northwind Baz" -- exactly the "Northwind Vault" defect this issue reports.
    session = _session_with(
        {
            "Nordkap Systeme": "Northwind Analytics",
            "Foo Analytics": "Bar Baz",
        }
    )

    text = "Nordkap Systeme provided the report."
    assert _restore(text, session) == "Northwind Analytics provided the report."


def test_full_surrogate_pass_takes_precedence_over_the_component_pass():
    # ADR-0036: the first pass (full surrogates) runs first so a full match is never
    # clobbered by the second pass (components) — both occurring in the same response.
    session = _session_with({"Carla Distel": "Sarah Bergmann"})

    text = "Carla Distel called; Carla will follow up."
    assert _restore(text, session) == "Sarah Bergmann called; Sarah will follow up."


def test_bare_integer_component_is_never_registered_as_a_restore_key():
    # issue #286: a provisional surrogate's positional digit ("Provisional
    # Surrogate 8") carries no entity meaning. Word counts are equal (3 vs. 3,
    # aligned) on purpose -- since #304 an unaligned pair is already excluded
    # for a different reason (no positional correspondence at all), so this
    # keeps exercising the digit-token guard itself: unfiltered, "8" is
    # distinctive and unambiguous, so it would be admitted as a second-pass restore
    # key and rewrite any ordinary "8" in a response -- observed live as
    # `utf-8` becoming `utf-Kestrel Dynamics`.
    session = _session_with({"Provisional Surrogate 8": "Kestrel Dynamics Holdings"})

    text = 'encoding="utf-8"'
    assert _restore(text, session) == text


def test_bare_integer_component_does_not_corrupt_phone_shaped_text():
    # issue #286: "Provisional Surrogate 41" must not turn its positional digit
    # into a restore key that clobbers the leading digits of an ordinary phone
    # number -- observed live as "+41 79 555 0142" becoming "+Transit 79 555 0142".
    # Aligned (3 vs. 3) for the same reason as the sibling test above.
    session = _session_with({"Provisional Surrogate 41": "Transit Systems Holdings"})

    text = "+41 79 555 0142"
    assert _restore(text, session) == text


def test_component_restores_inside_tool_call_json_the_same_as_prose():
    # ADR-0036 acceptance criterion 8: behavior is identical across all three
    # restore paths — component restore shares _restore_text, so tool-call
    # JSON string args get the same second-pass treatment as prose.
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
