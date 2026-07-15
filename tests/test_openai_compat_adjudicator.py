"""oMLX / OpenAI-compatible L3 adjudicator (ADR-0031 §2-3, issue #122): the real HTTP
client behind the ``L3Adjudicator`` seam, alongside ``OllamaAdjudicator`` (ollama.py).

oMLX is not a drop-in replacement for the Ollama client -- it speaks the OpenAI
chat-completions wire format (``POST /v1/chat/completions``, ``GET /v1/models``), not
Ollama's native one (``POST /api/generate``, ``GET /api/tags``).

Stubbed at its network boundary (httpx.MockTransport) — the adjudicator-egress oracle
for the L3 call itself (CONTEXT.md: candidate spans handed here are un-blindfolded real
values, kept safe only by requiring the model to run on-device -- enforced separately
by serve.py's ADR-0031 §3 loopback-only startup guard, not by this module).

Leak-audit clause analysis: N/A this slice (this file exercises the L3-oMLX seam in
isolation, not the request path) — mirrors test_ollama_adjudicator.py's own N/A stance.
"""

from __future__ import annotations

import json

import httpx

from blindfold.l3 import CandidateSpan, L3Adjudication
from blindfold.l3_openai_compat import OpenAICompatibleAdjudicator


def test_openai_compatible_adjudicator_sends_the_candidate_and_context_and_confirms_an_entity():
    # The adjudicator's only contract: hand the candidate span + its minimal context
    # to the oMLX OpenAI-compatible HTTP boundary, and parse a confirmed verdict back.
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"is_entity": True})}}
                ]
            },
        )

    http = httpx.Client(
        base_url="http://localhost:8080", transport=httpx.MockTransport(handler)
    )
    adjudicator = OpenAICompatibleAdjudicator(
        base_url="http://localhost:8080", model="qwen2.5-7b-mlx", http=http
    )
    candidate = CandidateSpan(
        text="Quentin", start=13, end=20, context="Please brief Quentin tomorrow."
    )

    decision = adjudicator.adjudicate(candidate)

    assert decision == L3Adjudication(is_entity=True)
    sent = json.loads(captured["request"].content.decode("utf-8"))
    assert sent["model"] == "qwen2.5-7b-mlx"
    prompt = sent["messages"][0]["content"]
    assert candidate.text in prompt
    assert candidate.context in prompt


def test_openai_compatible_adjudicator_sends_a_bearer_token_when_an_api_key_is_configured():
    # ADR-0031 follow-up (issue #130): a stock oMLX install requires an API key by
    # default (auth.skip_api_key_verification: false) and 401s without one.
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"is_entity": True})}}
                ]
            },
        )

    http = httpx.Client(
        base_url="http://localhost:8080", transport=httpx.MockTransport(handler)
    )
    adjudicator = OpenAICompatibleAdjudicator(
        base_url="http://localhost:8080",
        model="qwen2.5-7b-mlx",
        api_key="sk-omlx-secret",
        http=http,
    )
    candidate = CandidateSpan(
        text="Quentin", start=13, end=20, context="Please brief Quentin tomorrow."
    )

    adjudicator.adjudicate(candidate)

    assert captured["request"].headers["authorization"] == "Bearer sk-omlx-secret"


def test_openai_compatible_adjudicator_sends_no_authorization_header_when_api_key_is_unset():
    # Empty/unset means "no key" -- unchanged behavior for oMLX installs run with
    # skip_api_key_verification: true (ADR-0031 follow-up, issue #130).
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"is_entity": True})}}
                ]
            },
        )

    http = httpx.Client(
        base_url="http://localhost:8080", transport=httpx.MockTransport(handler)
    )
    adjudicator = OpenAICompatibleAdjudicator(
        base_url="http://localhost:8080", model="qwen2.5-7b-mlx", http=http
    )
    candidate = CandidateSpan(
        text="Quentin", start=13, end=20, context="Please brief Quentin tomorrow."
    )

    adjudicator.adjudicate(candidate)

    assert "authorization" not in captured["request"].headers


def test_openai_compatible_adjudicator_rejects_a_non_entity_candidate():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"is_entity": False})}}
                ]
            },
        )

    http = httpx.Client(
        base_url="http://localhost:8080", transport=httpx.MockTransport(handler)
    )
    adjudicator = OpenAICompatibleAdjudicator(
        base_url="http://localhost:8080", model="qwen2.5-7b-mlx", http=http
    )
    candidate = CandidateSpan(
        text="Bash", start=0, end=4, context="Run the Bash script again."
    )

    decision = adjudicator.adjudicate(candidate)

    assert decision == L3Adjudication(is_entity=False)


def test_openai_compatible_adjudicator_propagates_a_local_outage_so_l3_fails_closed():
    # ADR-0009 / leak-audit clause F: the adjudicator does not swallow a connection
    # failure into a false "not an entity" -- it lets the failure propagate so
    # L3Detector.detect() (l3.py) turns it into the typed L3Unavailable, which the
    # mint pass raises as the fail-closed 503 (never a silent fail-open). Mirrors
    # test_ollama_adjudicator_propagates_a_local_outage_so_l3_fails_closed.
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http = httpx.Client(
        base_url="http://localhost:8080", transport=httpx.MockTransport(handler)
    )
    adjudicator = OpenAICompatibleAdjudicator(
        base_url="http://localhost:8080", model="qwen2.5-7b-mlx", http=http
    )
    candidate = CandidateSpan(
        text="Quentin", start=0, end=7, context="Please brief Quentin tomorrow."
    )

    try:
        adjudicator.adjudicate(candidate)
        raised = False
    except httpx.ConnectError:
        raised = True
    assert raised


def test_openai_compatible_adjudicator_sets_an_explicit_timeout_not_httpxs_implicit_default():
    # Same rationale as OllamaAdjudicator (issue #69): when no `http` is injected (the
    # production path), the client's timeout must be explicit and deliberately longer
    # than httpx's 5s implicit default so a cold model load doesn't spuriously
    # fail-close.
    adjudicator = OpenAICompatibleAdjudicator(
        base_url="http://localhost:8080", model="qwen2.5-7b-mlx"
    )

    timeout = adjudicator._http.timeout
    assert timeout.connect > 5.0
    assert timeout.read > 5.0


def test_ping_omlx_reports_healthy_when_the_daemon_answers():
    from blindfold.status import DependencyHealth

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": []})

    from blindfold.l3_openai_compat import ping_omlx

    http = httpx.Client(transport=httpx.MockTransport(handler))
    health = ping_omlx("http://localhost:8080", http=http)
    assert health == DependencyHealth(healthy=True)


def test_ping_omlx_sends_a_bearer_token_when_an_api_key_is_configured():
    # ADR-0031 follow-up (issue #130): the liveness probe must also authenticate, so
    # /v1/status's l3 dependency probe doesn't false-negative against an auth-enabled
    # oMLX instance.
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"data": []})

    from blindfold.l3_openai_compat import ping_omlx

    http = httpx.Client(transport=httpx.MockTransport(handler))
    health = ping_omlx("http://localhost:8080", api_key="sk-omlx-secret", http=http)

    assert health.healthy
    assert captured["request"].headers["authorization"] == "Bearer sk-omlx-secret"


def test_ping_omlx_reports_unhealthy_scrubbed_detail_when_unreachable():
    from blindfold.status import DependencyHealth

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    from blindfold.l3_openai_compat import ping_omlx

    http = httpx.Client(transport=httpx.MockTransport(handler))
    health = ping_omlx("http://localhost:8080", http=http)
    assert health == DependencyHealth(healthy=False, detail="omlx unreachable")
