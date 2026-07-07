"""Local-Ollama L3 adjudicator (ADR-0022): the real HTTP client behind the
``L3Adjudicator`` seam.

Stubbed at its network boundary (httpx.MockTransport) — the adjudicator-egress oracle
for the L3 call itself (CONTEXT.md: candidate spans handed here are un-blindfolded real
values, kept safe only by requiring the model to run on-device).

Leak-audit clause analysis: N/A this slice (this file exercises the L3-Ollama seam in
isolation, not the request path) — covered instead by the proxy-level mint-pass tests.
"""

from __future__ import annotations

import json

import httpx

from blindfold.l3 import CandidateSpan, L3Adjudication
from blindfold.ollama import OllamaAdjudicator, is_cloud_model


def test_ollama_adjudicator_sends_the_candidate_and_context_and_confirms_an_entity():
    # The adjudicator's only contract: hand the candidate span + its minimal context
    # to the local Ollama HTTP boundary, and parse a confirmed verdict back.
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200, json={"response": json.dumps({"is_entity": True})}
        )

    http = httpx.Client(
        base_url="http://localhost:11434", transport=httpx.MockTransport(handler)
    )
    adjudicator = OllamaAdjudicator(
        base_url="http://localhost:11434", model="llama3.1", http=http
    )
    candidate = CandidateSpan(
        text="Quentin", start=13, end=20, context="Please brief Quentin tomorrow."
    )

    decision = adjudicator.adjudicate(candidate)

    assert decision == L3Adjudication(is_entity=True)
    sent = json.loads(captured["request"].content.decode("utf-8"))
    assert sent["model"] == "llama3.1"
    assert candidate.text in sent["prompt"]
    assert candidate.context in sent["prompt"]


def test_ollama_adjudicator_rejects_a_non_entity_candidate():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"response": json.dumps({"is_entity": False})}
        )

    http = httpx.Client(
        base_url="http://localhost:11434", transport=httpx.MockTransport(handler)
    )
    adjudicator = OllamaAdjudicator(
        base_url="http://localhost:11434", model="llama3.1", http=http
    )
    candidate = CandidateSpan(
        text="Bash", start=0, end=4, context="Run the Bash script again."
    )

    decision = adjudicator.adjudicate(candidate)

    assert decision == L3Adjudication(is_entity=False)


def test_ollama_adjudicator_propagates_a_local_outage_so_l3_fails_closed():
    # ADR-0009 / leak-audit clause F: the adjudicator does not swallow a connection
    # failure into a false "not an entity" -- it lets the failure propagate so
    # L3Detector.detect() (l3.py) turns it into the typed L3Unavailable, which the
    # mint pass raises as the fail-closed 503 (never a silent fail-open).
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http = httpx.Client(
        base_url="http://localhost:11434", transport=httpx.MockTransport(handler)
    )
    adjudicator = OllamaAdjudicator(
        base_url="http://localhost:11434", model="llama3.1", http=http
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


def test_is_cloud_model_flags_the_colon_cloud_tag_suffix():
    # ADR-0022 local-only invariant: the `:cloud` tag is today's signal that an Ollama
    # model executes remotely, even when the daemon itself is reached over loopback.
    assert is_cloud_model("qwen3:cloud") is True
    assert is_cloud_model("gpt-oss:20b-cloud") is True


def test_is_cloud_model_allows_an_ordinary_local_tag():
    assert is_cloud_model("llama3.1") is False
    assert is_cloud_model("llama3.1:8b") is False
    assert is_cloud_model("qwen3:cloudy") is False
