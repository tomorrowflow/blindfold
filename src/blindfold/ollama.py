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


def is_cloud_model(model: str) -> bool:
    """True if ``model`` names a remotely-executing Ollama model (the ``:cloud`` tag)."""
    _, _, tag = model.partition(":")
    return tag.lower().endswith("cloud")


# Issue #69: a cold Ollama model load measured 6.35s live (warm ~0.67s); httpx's
# implicit default (5s) is too tight and spuriously fail-closes the first request
# after startup/eviction. This is deliberately generous headroom above that measured
# cold-load figure, not a tuned SLO -- ADR-0022 sets no latency budget for this call.
DEFAULT_ADJUDICATOR_TIMEOUT_SECONDS = 30.0

_PROMPT_TEMPLATE = (
    "You are adjudicating whether a flagged span of text names a real-world entity "
    "that must be protected: a person's name, an organization/company name, or an "
    "internal codename or project name. Respond with strict JSON only, of the exact "
    'shape {{"is_entity": true}} or {{"is_entity": false}} — no other text.\n\n'
    "Context: {context}\n"
    "Flagged span: {text}\n"
)


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
