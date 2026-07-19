"""Local-Ollama L3 adjudicator (ADR-0022) — the real HTTP client behind the
``L3Adjudicator`` seam (network-boundary seam defined in ``l3.py``).

The **adjudicator egress** (CONTEXT.md) carries un-blindfolded candidate spans — real
values, by definition — so this call must stay on-device. ``is_cloud_model`` is the
local-only invariant's detection primitive: a ``:cloud``-suffixed Ollama tag names a
model that executes remotely even when the daemon itself is reached over loopback
(ADR-0022 "Alternatives considered": a loopback base URL is necessary but not
sufficient). There is no override — the caller (``serve.refuse_if_cloud_model``)
refuses to start rather than risk a real candidate span leaving the machine.
"""

from __future__ import annotations

import json

import httpx

from .l3 import CandidateSpan, L3Adjudication
from .status import DependencyHealth


def is_cloud_model(model: str) -> bool:
    """True if ``model`` names a remotely-executing Ollama model (the ``:cloud`` tag)."""
    _, _, tag = model.partition(":")
    return tag.lower().endswith("cloud")


# Issue #92: /v1/status's l3 dependency probe -- a lightweight local-daemon liveness
# check, distinct from adjudicate(). GET /api/tags sends no candidate-span content (no
# adjudicator egress), so it's safe to run on every cache-miss poll. The failure detail
# is a fixed, scrubbed string (never the httpx exception's own text, which could echo
# request internals) -- matching the issue's own contract example verbatim.
DEFAULT_PING_TIMEOUT_SECONDS = 5.0


def ping_ollama(
    base_url: str, http: httpx.Client | None = None, timeout: float = DEFAULT_PING_TIMEOUT_SECONDS
) -> DependencyHealth:
    """Lightweight Ollama liveness probe (issue #92) -- GET ``{base_url}/api/tags``."""
    url = f"{base_url.rstrip('/')}/api/tags"
    client = http or httpx.Client(timeout=timeout)
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        return DependencyHealth(healthy=False, detail="ollama unreachable")
    return DependencyHealth(healthy=True)


# Issue #69: a cold Ollama model load measured 6.35s live (warm ~0.67s); httpx's
# implicit default (5s) is too tight and spuriously fail-closes the first request
# after startup/eviction. This is deliberately generous headroom above that measured
# cold-load figure, not a tuned SLO -- ADR-0022 sets no latency budget for this call.
DEFAULT_ADJUDICATOR_TIMEOUT_SECONDS = 30.0

_PROMPT_TEMPLATE = (
    "You are adjudicating whether a flagged span of text names a real-world entity "
    "that must be protected: a SPECIFIC, private or sensitive real person, "
    "organization/company, or secret project/initiative — not merely a capitalized "
    "word. Reject the span (is_entity: false) if it is any of these:\n"
    "- a common dictionary word that is capitalized only because of its position in "
    "a sentence or heading (e.g. Single, Tools, Lead);\n"
    "- a well-known PUBLIC software, framework, operating system, library, or tool "
    "name (e.g. Vue, Playwright, Darwin, Postgres) — even when it is also a generic "
    "word, treat it as public software, not a protected referent;\n"
    "- a common English or German verb, action word, or generic instruction/tool/"
    "command label — even when capitalized mid-sentence for emphasis or used as a "
    "label (e.g. Write, Refactor, Build, Find) — unless it independently names a "
    "specific, private/sensitive real person, organization, or secret project/"
    "initiative.\n"
    "Only answer is_entity: true for a specific, private/sensitive real person, "
    "organization, or secret project/initiative that fails all rejection rules "
    "above. Respond with strict JSON only, of the exact shape "
    '{{"is_entity": true}} or {{"is_entity": false}} — no other text.\n\n'
    "Context: {context}\n"
    "Flagged span: {text}\n"
)

# Issue #142: the batch counterpart of _PROMPT_TEMPLATE — same adjudication rules,
# applied independently to each numbered candidate in a single call. The response
# is wrapped in a "verdicts" object (not a bare array) so the same shape works
# under oMLX's OpenAI-compatible `response_format: json_object`, which requires a
# top-level JSON object (l3_openai_compat.py imports this template unchanged).
#
# Issue #148 (#142 regression): live testing against a real weak local model
# showed received<expected verdicts on almost every batch call, typically
# exactly 1 regardless of N — the model wasn't told *how many* verdicts to
# produce, only "one per candidate," and a weak model reading a numbered list
# apparently often collapses that into a single overall judgment. Naming the
# count explicitly, twice (both up front and again at the response-shape
# instruction), is the low-risk half of the fix; the parser itself already
# round-trips a well-formed N-item array losslessly, so this is a prompt/format
# fix, not a parser fix.
_BATCH_PROMPT_TEMPLATE = (
    "You are adjudicating {count} flagged spans, listed below as a numbered "
    "list of candidates. Each span may or may not name a real-world entity that "
    "must be protected: a SPECIFIC, private or sensitive real person, "
    "organization/company, or secret project/initiative — not merely a "
    "capitalized word. Reject a span (is_entity: false) if it is any of "
    "these:\n"
    "- a common dictionary word that is capitalized only because of its position "
    "in a sentence or heading (e.g. Single, Tools, Lead);\n"
    "- a well-known PUBLIC software, framework, operating system, library, or tool "
    "name (e.g. Vue, Playwright, Darwin, Postgres) — even when it is also a "
    "generic word, treat it as public software, not a protected referent;\n"
    "- a common English or German verb, action word, or generic instruction/tool/"
    "command label — even when capitalized mid-sentence for emphasis or used as a "
    "label (e.g. Write, Refactor, Build, Find) — unless it independently names a "
    "specific, private/sensitive real person, organization, or secret project/"
    "initiative.\n"
    "Only answer is_entity: true for a specific, private/sensitive real person, "
    "organization, or secret project/initiative that fails all rejection rules "
    "above. Adjudicate each numbered candidate independently, using only that "
    "candidate's own context — do not let one candidate's verdict influence "
    "another's. Respond with strict JSON only, of the exact shape "
    '{{"verdicts": [{{"is_entity": true}}, {{"is_entity": false}}, ...]}} — '
    "your \"verdicts\" array MUST contain exactly {count} verdicts, one per "
    "candidate below, in the same order as listed, no other text.\n\n"
    "{candidates}"
)

_BATCH_CANDIDATE_TEMPLATE = "{index}. Context: {context}\n   Flagged span: {text}\n"


def _build_batch_prompt(candidates: list[CandidateSpan]) -> str:
    listing = "\n".join(
        _BATCH_CANDIDATE_TEMPLATE.format(index=index, context=c.context, text=c.text)
        for index, c in enumerate(candidates, start=1)
    )
    return _BATCH_PROMPT_TEMPLATE.format(count=len(candidates), candidates=listing)


def _parse_batch_verdicts(content: str) -> list[L3Adjudication]:
    """Best-effort, position-preserving parse of a batch response (issue #142).

    A short or malformed response is not an error here — it degrades to however
    many well-formed verdicts could be parsed off the front of the array (stopping
    at the first malformed entry, so a later "recovered" entry can't be
    mis-attributed to an earlier candidate's position). L3Detector then retries
    whatever is missing one candidate at a time (issue #148) and pads only the
    genuine residual as ``is_entity: true`` (fail-closed).
    """
    try:
        raw_verdicts = json.loads(content)["verdicts"]
        if not isinstance(raw_verdicts, list):
            return []
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
    verdicts: list[L3Adjudication] = []
    for item in raw_verdicts:
        try:
            verdicts.append(L3Adjudication(is_entity=bool(item["is_entity"])))
        except (KeyError, TypeError):
            break
    return verdicts


class OllamaAdjudicator:
    """Real local-Ollama client behind the :class:`~blindfold.l3.L3Adjudicator` seam.

    Synchronous (uses ``httpx.Client``); the mint pass runs it off the event loop via
    ``run_in_threadpool`` (issue #69) so a slow/cold L3 call can't starve other in-flight
    requests, and ADR-0022 sets no latency SLO for this slice. Inject
    ``http=httpx.Client(transport=httpx.MockTransport(...))`` in tests — the same
    seam-stub pattern as :class:`~blindfold.transit.TransitClient`.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        http: httpx.Client | None = None,
        timeout: float = DEFAULT_ADJUDICATOR_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._http = http or httpx.Client(base_url=self._base_url, timeout=timeout)

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        prompt = _PROMPT_TEMPLATE.format(context=candidate.context, text=candidate.text)
        response = self._http.post(
            f"{self._base_url}/api/generate",
            json={
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
        )
        response.raise_for_status()
        verdict = json.loads(response.json()["response"])
        return L3Adjudication(is_entity=bool(verdict["is_entity"]))

    def adjudicate_batch(
        self, candidates: list[CandidateSpan]
    ) -> list[L3Adjudication]:
        """Adjudicate N candidates in one HTTP call (issue #142) — amortises the
        round-trip overhead (connection setup, headers, JSON framing) across the
        whole batch instead of paying it once per candidate. A network-layer
        failure propagates (letting L3Detector fail-close the whole batch, same as
        a single-candidate outage); a malformed/short response body degrades to
        however many verdicts :func:`_parse_batch_verdicts` could parse, leaving
        L3Detector to fail-close whatever's missing.
        """
        prompt = _build_batch_prompt(candidates)
        response = self._http.post(
            f"{self._base_url}/api/generate",
            json={
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
        )
        response.raise_for_status()
        return _parse_batch_verdicts(response.json()["response"])
