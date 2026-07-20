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
from blindfold.ollama import (
    _PROMPT_TEMPLATE,
    _build_batch_prompt,
    OllamaAdjudicator,
    is_cloud_model,
)


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


def test_adjudicator_prompt_requires_a_specific_sensitive_referent_and_rejects_common_words_and_public_software():
    # Issue #88 (semantic half of the precision fix, sibling to #87's allowlist half):
    # the prior prompt's "an internal codename or project name" clause invited the
    # model to flag any capitalized techy/prose word (Single, Tools, Darwin, Transit,
    # Mythos -- live 2026-07-10 evidence). The prompt must now explicitly instruct
    # rejection of (a) common dictionary words capitalized only by sentence/heading
    # position and (b) well-known public software/framework/OS/library/tool names,
    # requiring instead a specific, private/sensitive real person, organization, or
    # secret project/initiative -- while keeping the strict-JSON contract and the
    # candidate text/context interpolation unchanged (still covered by the sibling
    # "sends the candidate and context" test above).
    prompt = _PROMPT_TEMPLATE.format(context="ctx", text="span")
    lowered = prompt.lower()

    assert "common" in lowered and "word" in lowered
    assert "public" in lowered
    assert "software" in lowered or "framework" in lowered or "tool" in lowered
    assert "specific" in lowered
    assert "sensitive" in lowered
    assert '{"is_entity": true}' in prompt
    assert '{"is_entity": false}' in prompt


def test_adjudicator_single_prompt_rejects_common_verbs_and_action_labels_mid_sentence():
    # Issue #164: live false-positive evidence (Write/Refactor confirmed as entities
    # by inner oMLX gemma-2b in a Claude Code exchange). Write/Refactor/Build/Find
    # are common English verbs used as tool/command labels, capitalized mid-sentence
    # for emphasis, not because they name a private person, organization, or secret
    # project/initiative. The third rejection category must appear in the single-
    # candidate prompt (mirrors the #88 test pattern: assert on real prompt content,
    # not a fake/inline string).
    prompt = _PROMPT_TEMPLATE.format(context="Use the Write tool", text="Write")
    lowered = prompt.lower()

    # Third rejection rule: a common verb, action word, or instruction/tool/command
    # label -- even capitalized mid-sentence -- must be explicitly rejected.
    assert "verb" in lowered or "action" in lowered
    assert "label" in lowered or "instruction" in lowered or "command" in lowered
    # Representative example words from the issue's live evidence must appear.
    assert "write" in lowered
    assert "refactor" in lowered or "build" in lowered or "find" in lowered


def test_adjudicator_batch_prompt_rejects_common_verbs_and_action_labels_mid_sentence():
    # Issue #164: the batch counterpart of the single-candidate test above. Both
    # _PROMPT_TEMPLATE and _BATCH_PROMPT_TEMPLATE must carry the third rejection
    # category (the two templates are maintained together in ollama.py, and
    # OpenAICompatibleAdjudicator imports _build_batch_prompt unchanged from ollama.py).
    from blindfold.ollama import _build_batch_prompt

    candidates = [
        CandidateSpan(text="Write", start=8, end=13, context="Use the Write tool"),
        CandidateSpan(text="Refactor", start=4, end=12, context="Run Refactor now"),
    ]
    prompt = _build_batch_prompt(candidates)
    lowered = prompt.lower()

    assert "verb" in lowered or "action" in lowered
    assert "label" in lowered or "instruction" in lowered or "command" in lowered
    assert "write" in lowered
    assert "refactor" in lowered or "build" in lowered or "find" in lowered


def test_adjudicator_single_prompt_rejects_common_capitalized_nouns_used_generically():
    # Issue #165: live false-positive evidence — Code, Index, Design, Transit,
    # Artifacts, Tool confirmed as entities by inner oMLX when they appear in
    # blindfold's own system prompt (CLAUDE.md / skill list text). These are common
    # English nouns used generically/technically, not proper nouns naming a specific
    # private person, org, or secret project. A fourth rejection category must appear
    # in the single-candidate prompt (mirrors the #88/#164 test pattern: assert on
    # real prompt content via _PROMPT_TEMPLATE.format(...), not a fake/inline string).
    prompt = _PROMPT_TEMPLATE.format(context="Use the Code tool to Index artifacts", text="Code")
    lowered = prompt.lower()

    # Fourth rejection rule: a common English or German noun used generically/
    # technically — even when capitalized — must be explicitly rejected.
    assert "noun" in lowered
    # Representative live-evidence words from the issue must appear as examples.
    assert "code" in lowered
    assert "index" in lowered or "design" in lowered or "transit" in lowered or "artifacts" in lowered or "tool" in lowered


def test_adjudicator_batch_prompt_rejects_common_capitalized_nouns_used_generically():
    # Issue #165: batch counterpart of the single-candidate test above. Both
    # _PROMPT_TEMPLATE and _BATCH_PROMPT_TEMPLATE must carry the fourth rejection
    # category (the two templates are maintained together in ollama.py, and
    # OpenAICompatibleAdjudicator imports _build_batch_prompt unchanged from ollama.py).
    from blindfold.ollama import _build_batch_prompt

    candidates = [
        CandidateSpan(text="Code", start=8, end=12, context="Use the Code tool"),
        CandidateSpan(text="Index", start=4, end=9, context="Build an Index now"),
    ]
    prompt = _build_batch_prompt(candidates)
    lowered = prompt.lower()

    # Fourth rejection rule: a common noun used generically/technically.
    assert "noun" in lowered
    # Representative live-evidence words from the issue must appear as examples.
    assert "code" in lowered
    assert "index" in lowered or "design" in lowered or "transit" in lowered or "artifacts" in lowered or "tool" in lowered


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


def test_ollama_adjudicator_sets_an_explicit_timeout_not_httpxs_implicit_default():
    # Issue #69 (carved out of the #58 L3-performance umbrella): a cold Ollama model
    # load measured 6.35s live, but the production httpx.Client was built with no
    # explicit timeout, so it inherited httpx's implicit 5s default -- the first
    # request after startup/eviction raised a timeout, spuriously fail-closing
    # (l3_unavailable 503) even though nothing was actually wrong. When no ``http``
    # is injected (the production path), the client's timeout must be explicit and
    # deliberately longer than httpx's 5s implicit default.
    adjudicator = OllamaAdjudicator(base_url="http://localhost:11434", model="llama3.1")

    timeout = adjudicator._http.timeout
    assert timeout.connect > 5.0
    assert timeout.read > 5.0


def test_is_cloud_model_flags_the_colon_cloud_tag_suffix():
    # ADR-0022 local-only invariant: the `:cloud` tag is today's signal that an Ollama
    # model executes remotely, even when the daemon itself is reached over loopback.
    assert is_cloud_model("qwen3:cloud") is True
    assert is_cloud_model("gpt-oss:20b-cloud") is True


def test_is_cloud_model_allows_an_ordinary_local_tag():
    assert is_cloud_model("llama3.1") is False
    assert is_cloud_model("llama3.1:8b") is False
    assert is_cloud_model("qwen3:cloudy") is False


def test_ping_ollama_reports_healthy_when_the_daemon_answers():
    from blindfold.status import DependencyHealth

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": []})

    from blindfold.ollama import ping_ollama

    http = httpx.Client(transport=httpx.MockTransport(handler))
    health = ping_ollama("http://localhost:11434", http=http)
    assert health == DependencyHealth(healthy=True)


def test_ping_ollama_reports_unhealthy_scrubbed_detail_when_unreachable():
    from blindfold.status import DependencyHealth

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    from blindfold.ollama import ping_ollama

    http = httpx.Client(transport=httpx.MockTransport(handler))
    health = ping_ollama("http://localhost:11434", http=http)
    assert health == DependencyHealth(healthy=False, detail="ollama unreachable")


def test_build_batch_prompt_states_the_exact_expected_verdict_count():
    # Issue #148 (#142 regression): live testing against a real local model
    # (oMLX gemma-4-e2b-it-4bit) showed received<expected verdicts almost every
    # batch call -- the root cause is the prompt/format, not the parser (the
    # parser already round-trips a well-formed N-item array losslessly, see
    # test_ollama_adjudicator_batch_sends_one_call_for_n_candidates). The prior
    # prompt only said "exactly one verdict per candidate" without ever stating
    # the concrete N, leaving a weak model to guess how many items to emit.
    # Naming N explicitly is the low-risk half of the fix (the other half is
    # L3Detector's per-candidate retry-recovery for whatever still comes up
    # short, see test_l3_detection.py).
    candidates = [
        CandidateSpan(text="Quentin", start=0, end=7, context="ctx-1"),
        CandidateSpan(text="Priya", start=0, end=5, context="ctx-2"),
        CandidateSpan(text="Yasmin", start=0, end=6, context="ctx-3"),
    ]

    prompt = _build_batch_prompt(candidates)

    assert "exactly 3 verdicts" in prompt


def test_ollama_adjudicator_batch_sends_one_call_for_n_candidates():
    # Issue #142: one HTTP round-trip carries every candidate in the batch, with
    # each candidate's text and context present in the single prompt sent, and the
    # response's verdict array maps back positionally.
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "response": json.dumps(
                    {"verdicts": [{"is_entity": True}, {"is_entity": False}]}
                )
            },
        )

    http = httpx.Client(
        base_url="http://localhost:11434", transport=httpx.MockTransport(handler)
    )
    adjudicator = OllamaAdjudicator(
        base_url="http://localhost:11434", model="llama3.1", http=http
    )
    candidates = [
        CandidateSpan(text="Quentin", start=0, end=7, context="Please brief Quentin."),
        CandidateSpan(text="Bash", start=0, end=4, context="Run the Bash script."),
    ]

    decisions = adjudicator.adjudicate_batch(candidates)

    assert decisions == [
        L3Adjudication(is_entity=True),
        L3Adjudication(is_entity=False),
    ]
    sent = json.loads(captured["request"].content.decode("utf-8"))
    assert sent["model"] == "llama3.1"
    assert "Quentin" in sent["prompt"]
    assert "Bash" in sent["prompt"]
    assert "Please brief Quentin." in sent["prompt"]
    assert "Run the Bash script." in sent["prompt"]


def test_ollama_adjudicator_batch_tolerates_a_short_verdict_array():
    # Issue #142 fail-closed contract: the adjudicator itself doesn't raise on a
    # short/malformed response -- it returns however many verdicts it could parse,
    # positionally, and leaves L3Detector to fail-close the rest (is_entity: true).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"response": json.dumps({"verdicts": [{"is_entity": False}]})}
        )

    http = httpx.Client(
        base_url="http://localhost:11434", transport=httpx.MockTransport(handler)
    )
    adjudicator = OllamaAdjudicator(
        base_url="http://localhost:11434", model="llama3.1", http=http
    )
    candidates = [
        CandidateSpan(text="Quentin", start=0, end=7, context="ctx-1"),
        CandidateSpan(text="Priya", start=0, end=5, context="ctx-2"),
    ]

    decisions = adjudicator.adjudicate_batch(candidates)

    assert decisions == [L3Adjudication(is_entity=False)]


def test_ollama_adjudicator_batch_tolerates_malformed_json():
    # A completely unparseable response body degrades to zero verdicts, not an
    # exception -- L3Detector's fail-closed padding handles the rest.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": "not json at all"})

    http = httpx.Client(
        base_url="http://localhost:11434", transport=httpx.MockTransport(handler)
    )
    adjudicator = OllamaAdjudicator(
        base_url="http://localhost:11434", model="llama3.1", http=http
    )
    candidates = [CandidateSpan(text="Quentin", start=0, end=7, context="ctx-1")]

    decisions = adjudicator.adjudicate_batch(candidates)

    assert decisions == []


def test_ollama_adjudicator_batch_propagates_a_local_outage_so_l3_fails_closed():
    # ADR-0009 / leak-audit clause F: a network-layer failure of the batch call
    # itself is not swallowed -- it propagates so L3Detector turns it into the
    # typed L3Unavailable, same as the single-candidate path.
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http = httpx.Client(
        base_url="http://localhost:11434", transport=httpx.MockTransport(handler)
    )
    adjudicator = OllamaAdjudicator(
        base_url="http://localhost:11434", model="llama3.1", http=http
    )
    candidates = [CandidateSpan(text="Quentin", start=0, end=7, context="ctx-1")]

    try:
        adjudicator.adjudicate_batch(candidates)
        raised = False
    except httpx.ConnectError:
        raised = True
    assert raised
