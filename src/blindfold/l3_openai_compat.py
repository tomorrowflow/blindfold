"""oMLX L3 adjudicator (ADR-0031 §2-3, issue #122) — the real HTTP client behind the
``L3Adjudicator`` seam (network-boundary seam defined in ``l3.py``), alongside the
Ollama client (``ollama.py``).

oMLX is **not** a drop-in replacement for the Ollama client: it speaks the OpenAI
chat-completions wire format (``POST /v1/chat/completions``, ``GET /v1/models``), not
Ollama's native one (``POST /api/generate``, ``GET /api/tags``) — hence a genuinely new
client module rather than a config repoint.

The **adjudicator egress** (CONTEXT.md) carries un-blindfolded candidate spans — real
values, by definition — so this call must stay on-device. Unlike Ollama, oMLX has no
``:cloud``-tag-equivalent signal to detect at the client level; the local-only
invariant is instead enforced by ``serve.py``'s loopback-only startup guard
(ADR-0031 §3), which this module does not duplicate.
"""

from __future__ import annotations

import json

import httpx

from .l3 import CandidateSpan, L3Adjudication
from .ollama import _PROMPT_TEMPLATE
from .status import DependencyHealth

# Issue #92: /v1/status's l3 dependency probe -- a lightweight local-daemon liveness
# check, distinct from adjudicate(). GET /v1/models sends no candidate-span content (no
# adjudicator egress), so it's safe to run on every cache-miss poll. The failure detail
# is a fixed, scrubbed string (never the httpx exception's own text), matching
# ping_ollama's contract (ollama.py).
DEFAULT_PING_TIMEOUT_SECONDS = 5.0


def _bearer_auth_headers(api_key: str) -> dict[str, str]:
    # Single-sources the oMLX auth contract for both the probe and the adjudicator
    # (ADR-0031 follow-up, issue #130). Empty/unset means "no key" -- unchanged
    # behavior for oMLX installs run with skip_api_key_verification: true.
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def ping_omlx(
    base_url: str,
    api_key: str = "",
    http: httpx.Client | None = None,
    timeout: float = DEFAULT_PING_TIMEOUT_SECONDS,
) -> DependencyHealth:
    """Lightweight oMLX liveness probe (issue #92) -- GET ``{base_url}/v1/models``.

    Authenticates the same way ``OpenAICompatibleAdjudicator`` does (ADR-0031
    follow-up, issue #130) -- an auth-enabled oMLX instance 401s this probe too
    without a key, which would otherwise false-negative /v1/status's l3 dependency
    probe.
    """
    url = f"{base_url.rstrip('/')}/v1/models"
    headers = _bearer_auth_headers(api_key)
    client = http or httpx.Client(timeout=timeout)
    try:
        response = client.get(url, headers=headers)
        response.raise_for_status()
    except httpx.HTTPError:
        return DependencyHealth(healthy=False, detail="omlx unreachable")
    return DependencyHealth(healthy=True)


# Same headroom rationale as OllamaAdjudicator (issue #69) -- no latency SLO for L3
# (ADR-0022), just deliberately generous room above httpx's implicit 5s default so a
# cold model load doesn't spuriously fail-close.
DEFAULT_ADJUDICATOR_TIMEOUT_SECONDS = 30.0


class OpenAICompatibleAdjudicator:
    """Real local-oMLX client behind the :class:`~blindfold.l3.L3Adjudicator` seam.

    Uses the same adjudication prompt template as ``OllamaAdjudicator``
    (``ollama._PROMPT_TEMPLATE``) so both providers judge candidates identically;
    only the wire format differs. Synchronous (uses ``httpx.Client``); the mint pass
    runs it off the event loop via ``run_in_threadpool`` (issue #69), same pattern as
    ``OllamaAdjudicator``. Inject ``http=httpx.Client(transport=httpx.MockTransport(...))``
    in tests.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        http: httpx.Client | None = None,
        timeout: float = DEFAULT_ADJUDICATOR_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._http = http or httpx.Client(base_url=self._base_url, timeout=timeout)

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        prompt = _PROMPT_TEMPLATE.format(context=candidate.context, text=candidate.text)
        response = self._http.post(
            f"{self._base_url}/v1/chat/completions",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            headers=_bearer_auth_headers(self._api_key),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        verdict = json.loads(content)
        return L3Adjudication(is_entity=bool(verdict["is_entity"]))
