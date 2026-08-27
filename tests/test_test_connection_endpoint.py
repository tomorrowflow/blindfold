"""Test connection (issue #265) -- ``POST /v1/management/test-connection`` end to
end: the real-socket property (Q3), the failure taxonomy (Q4) against Blindfold's
own actual responses (not hand-built fixtures), and the store/entity-graph/review-
inbox absence guarantee (Q2).

Leak-audit clauses (this slice touches the request path -- one exchange really
does egress through the stub-upstream seam):
- A: asserted directly in ``test_the_canary_egresses_as_its_surrogate_never_the_real_value``
  -- the stub upstream's recorded request never contains the canary literal.
- B: asserted in the ``blindfolded_ok`` test -- the canary comes back restored in
  the loopback response Blindfold hands back.
- C: N/A -- this slice injects exactly one surrogate (the canary's); no second,
  coincidental surrogate-shaped value is ever present to misrestore.
- D: the verify pass (leak_gate/resolution_gate) is exercised indirectly by every
  passing exchange here (it always runs); ``leak_flagged`` covers its block path.
- E: covered by ``test_the_canary_surrogate_never_touches_persistent_store_entity_graph_or_review_inbox``.
- F: N/A this slice -- no L3 dependency is exercised (the canary is pure L1 PII,
  by design, so it never reaches L3); the existing fail-closed suites own that.
- G: N/A -- no mapping-cipher/store wiring is exercised (default in-memory store).
"""

import json

import httpx
import pytest

from blindfold.app import app, get_l3_detector, get_reidentify_store, get_review_inbox, get_upstream_client
from blindfold.l3 import L3Detector, L3Unavailable
from blindfold.review import ReviewInbox
from blindfold.reidentify import InMemoryReIdentificationStore
from blindfold.test_connection import (
    CANARY_EMAIL,
    CODE_BLINDFOLDED_OK,
    CODE_FAIL_CLOSED_BLOCK,
    CODE_PROXY_UNREACHABLE,
    CODE_UPSTREAM_AUTH_REJECTED,
    CODE_UPSTREAM_UNREACHABLE,
    CODE_WRONG_ENDPOINT,
)
from blindfold.upstream import UpstreamClient


def _echoing_stub_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    """Echoes the canary's surrogate back, exactly, so restore has something to
    resolve -- the same "echo verbatim" pattern used elsewhere in this suite
    (test_provisional_leak_gate_request_path.py's `_make_echoing_stub_upstream`).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        sent = json.loads(request.content)
        blinded_text = sent["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": blinded_text}],
                "model": "m",
                "stop_reason": "end_turn",
            },
        )

    return UpstreamClient(
        base_url="http://upstream.test",
        client=httpx.AsyncClient(
            base_url="http://upstream.test", transport=httpx.MockTransport(handler)
        ),
    )


@pytest.fixture
def client(wired_app) -> httpx.AsyncClient:
    # test-connection is `viewer`-gated (same operational-glance sensitivity as the
    # processing trace it reads) -- the anonymous caller identity (no
    # x-blindfold-identity header) needs that role on the default workspace.
    wired_app.rbac.grant("", "default", "viewer")
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://management.test")


@pytest.mark.anyio
async def test_proxy_unreachable_when_nothing_listens_at_the_base_url(wired_app, client):
    from conftest import _free_port

    dead_port = _free_port()  # nothing bound here

    response = await client.post(
        "/v1/management/test-connection",
        json={"base_url": f"http://127.0.0.1:{dead_port}", "model": "m"},
    )

    assert response.status_code == 200
    assert response.json()["code"] == CODE_PROXY_UNREACHABLE


@pytest.mark.anyio
async def test_wrong_endpoint_when_something_else_answers_at_the_base_url(
    wired_app, client
):
    reader_writer_port = await _start_plain_http_server()

    response = await client.post(
        "/v1/management/test-connection",
        json={
            "base_url": f"http://127.0.0.1:{reader_writer_port}",
            "model": "m",
        },
    )

    assert response.status_code == 200
    assert response.json()["code"] == CODE_WRONG_ENDPOINT


async def _start_plain_http_server() -> int:
    """A bare TCP server that answers HTTP with something Blindfold never would --
    stands in for "wrong port/app" (Q4) without pulling in a second web framework.
    """
    import asyncio

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(1024)
        body = b'{"hello": "world"}'
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )
        await writer.drain()
        writer.close()

    from conftest import _free_port

    port = _free_port()
    server = await asyncio.start_server(handle, "127.0.0.1", port)
    asyncio.get_event_loop().create_task(server.serve_forever())
    return port


@pytest.mark.anyio
async def test_the_exchange_goes_through_the_real_listening_socket(
    wired_app, client, live_proxy_server
):
    """Q3: proves the loopback call actually crosses a TCP socket, not an internal
    function call -- ``live_proxy_server`` binds ``blindfold.app.app`` to a real
    127.0.0.1 port; a call that reached it any other way could never observe this.
    """
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _echoing_stub_upstream(recorded)

    response = await client.post(
        "/v1/management/test-connection",
        json={"base_url": live_proxy_server, "model": "m"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == CODE_BLINDFOLDED_OK
    assert len(recorded) == 1


@pytest.mark.anyio
async def test_blindfolded_ok_when_the_canary_egresses_and_the_model_echoes_it_back(
    wired_app, client, live_proxy_server
):
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _echoing_stub_upstream(recorded)

    response = await client.post(
        "/v1/management/test-connection",
        json={"base_url": live_proxy_server, "model": "m"},
    )

    body = response.json()
    assert body["code"] == CODE_BLINDFOLDED_OK
    assert body["message"]
    assert "remedy" in body


@pytest.mark.anyio
async def test_the_canary_egresses_as_its_surrogate_never_the_real_value(
    wired_app, client, live_proxy_server
):
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _echoing_stub_upstream(recorded)

    await client.post(
        "/v1/management/test-connection",
        json={"base_url": live_proxy_server, "model": "m"},
    )

    assert len(recorded) == 1
    outbound_body = recorded[0].content.decode("utf-8")
    assert CANARY_EMAIL not in outbound_body
    surrogate = wired_app.mapping.surrogate_for(CANARY_EMAIL)
    assert surrogate is not None
    assert surrogate in outbound_body


@pytest.mark.anyio
async def test_restore_unproven_when_the_model_does_not_echo_the_canary(
    wired_app, client, live_proxy_server
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "I won't repeat that."}],
                "model": "m",
                "stop_reason": "end_turn",
            },
        )

    app.dependency_overrides[get_upstream_client] = lambda: UpstreamClient(
        base_url="http://upstream.test",
        client=httpx.AsyncClient(
            base_url="http://upstream.test", transport=httpx.MockTransport(handler)
        ),
    )

    response = await client.post(
        "/v1/management/test-connection",
        json={"base_url": live_proxy_server, "model": "m"},
    )

    assert response.json()["code"] == "blindfolded_ok_restore_unproven"


@pytest.mark.anyio
async def test_upstream_auth_rejected_when_the_provider_returns_401(
    wired_app, client, live_proxy_server
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    app.dependency_overrides[get_upstream_client] = lambda: UpstreamClient(
        base_url="http://upstream.test",
        client=httpx.AsyncClient(
            base_url="http://upstream.test", transport=httpx.MockTransport(handler)
        ),
    )

    response = await client.post(
        "/v1/management/test-connection",
        json={"base_url": live_proxy_server, "model": "m"},
    )

    assert response.json()["code"] == CODE_UPSTREAM_AUTH_REJECTED


@pytest.mark.anyio
async def test_upstream_unreachable_when_the_provider_returns_500(
    wired_app, client, live_proxy_server
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "internal error"}})

    app.dependency_overrides[get_upstream_client] = lambda: UpstreamClient(
        base_url="http://upstream.test",
        client=httpx.AsyncClient(
            base_url="http://upstream.test", transport=httpx.MockTransport(handler)
        ),
    )

    response = await client.post(
        "/v1/management/test-connection",
        json={"base_url": live_proxy_server, "model": "m"},
    )

    assert response.json()["code"] == CODE_UPSTREAM_UNREACHABLE


@pytest.mark.anyio
async def test_fail_closed_block_surfaces_the_scrubbed_reason_as_ref(
    wired_app, client, live_proxy_server
):
    """The canary payload is engineered to never reach L3 at all (an all-lowercase
    instruction plus a purely L1-detected PII value, Q2) -- so this forces the
    mint pass to fail-closed the same way ``get_l3_detector``'s own docstring says
    it's meant to be overridden for ("to force an outage"), proving test-connection
    surfaces *whatever* fail-closed block the exchange hits as ``fail_closed_block``
    with its scrubbed ``ref``, rather than swallowing or misclassifying it.
    """

    class _UnavailableL3Detector(L3Detector):
        provider_name = "stub"

        def __init__(self) -> None:  # no super().__init__ -- nothing else is used
            pass

        def detect(self, *args, **kwargs):
            raise L3Unavailable("stubbed outage")

    app.dependency_overrides[get_l3_detector] = lambda: _UnavailableL3Detector()

    response = await client.post(
        "/v1/management/test-connection",
        json={"base_url": live_proxy_server, "model": "m"},
    )

    body = response.json()
    assert body["code"] == CODE_FAIL_CLOSED_BLOCK
    assert body["ref"]


@pytest.mark.anyio
async def test_the_canary_surrogate_never_touches_persistent_store_entity_graph_or_review_inbox(
    wired_app, client, live_proxy_server
):
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _echoing_stub_upstream(recorded)
    inbox = ReviewInbox()
    reidentify_store = InMemoryReIdentificationStore()
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_reidentify_store] = lambda: reidentify_store

    response = await client.post(
        "/v1/management/test-connection",
        json={"base_url": live_proxy_server, "model": "m"},
    )

    assert response.json()["code"] == CODE_BLINDFOLDED_OK
    # Q2: "the canary pair is absent from the persistent store, the entity graph,
    # and the review inbox after the test, asserted by test."
    assert wired_app.entity_graph.list_entities(workspace="default") == []
    assert inbox.list() == []
    assert reidentify_store.all_entries() == []


@pytest.mark.anyio
async def test_a_non_loopback_base_url_is_rejected_before_any_call_is_made(wired_app, client):
    """The feature's own stated purpose (Q3) is to call the proxy's own listening
    socket -- never an arbitrary external URL. Without this guard, a caller of this
    management endpoint (SPA origin, or anything else that can reach it -- the
    Connect page's own trust-boundary banner already documents that this API is
    unauthenticated-by-design on localhost) could direct Blindfold's backend to
    make an arbitrary outbound POST carrying attacker-chosen headers: an SSRF-
    shaped primitive this endpoint must not offer, even under that trust model.
    """
    response = await client.post(
        "/v1/management/test-connection",
        json={"base_url": "http://example.invalid", "model": "m"},
    )

    assert response.status_code == 422
