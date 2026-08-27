"""Test connection: prove the exchange was blindfolded, with a typed failure
taxonomy (issue #265).

The Connect page's "Test connection" action runs one real exchange through the
proxy's own listening socket and reports what actually happened to it. Design
decisions (trusted-maintainer comment, 2026-08-27, issue #265):

- **Q2 canary.** The real-side canary is a fixed value in the reserved,
  non-colliding shape: an RFC 2606 ``.invalid``-domain email address. L1's
  unconditional email regex (:data:`blindfold.detection._EMAIL_RE`, issue #327)
  detects it deterministically -- no L3 candidate step, no review inbox, no
  entity graph -- so it "enters as a confirmed pair for this exchange, not as a
  candidate" and :meth:`~blindfold.surrogates.SurrogateMapping.mint_pii` (the
  same reserved-namespace mint every real PII value goes through) is the only
  thing that ever registers it: in-memory only, never the persistent store,
  the entity graph, or the review inbox (see ``test_surrogate_reserved_namespace_shape.py``
  for the same reserved-namespace precedent this reuses rather than inventing a
  parallel mechanism).
- **Honesty split.** Egress (the canary left as its surrogate) is deterministic
  and is the pass/fail bar, asserted from the exchange's own ADR-0035 processing
  trace. Restore depends on the model echoing the surrogate back; a response
  missing the echo is ``blindfolded_ok_restore_unproven`` -- informational, never
  a hard fail, never a silent pass.
- **Q4 taxonomy.** Typed codes, each with a remedy line -- never a single
  "failed" string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from .processing_trace import ProcessingTraceBuffer
    from .surrogates import SurrogateMapping

CANARY_EMAIL = "blindfold-test-connection-canary@blindfold.invalid"

# Deliberately all-lowercase (no capitalized token anywhere but the canary's own
# address): the L3 capitalized-token candidate producer's positional-case
# heuristic (ADR-0033) only suppresses a sentence-initial capital when it also
# has vocabulary/list-marker evidence -- a fresh sentence like "This is a
# connection test." has neither, on a payload consisting of nothing else, so
# "This"/"Reply" would otherwise mint their own spurious L3 candidate and reach
# L3Unavailable (blocked-l3-unavailable) in exactly the environment this
# feature exists to smoke-test: one with no L3 adjudicator wired. The canary
# must be the *only* thing this exchange ever detects -- L1, deterministic, no
# candidate step at all (Q2's "confirmed pair, not a candidate").
CANARY_INSTRUCTION = (
    f"connection test: reply with exactly and only this text, nothing else, "
    f"no punctuation added: {CANARY_EMAIL}"
)

CODE_BLINDFOLDED_OK = "blindfolded_ok"
CODE_BLINDFOLDED_OK_RESTORE_UNPROVEN = "blindfolded_ok_restore_unproven"
CODE_PROXY_UNREACHABLE = "proxy_unreachable"
CODE_WRONG_ENDPOINT = "wrong_endpoint"
CODE_UPSTREAM_AUTH_REJECTED = "upstream_auth_rejected"
CODE_UPSTREAM_UNREACHABLE = "upstream_unreachable"
CODE_FAIL_CLOSED_BLOCK = "fail_closed_block"
CODE_LEAK_FLAGGED = "leak_flagged"

# ADR-0009: the two block sub_reasons that mean "a real/injected value was actually
# at risk at this exchange's egress or restore boundary" (leak_gate / resolution_gate)
# -- distinct from an availability block (l3_unavailable, detection_internal, a pool
# exhaustion) that never involved a value crossing anywhere. The one taxonomy entry
# that "must be visually alarming" (Q4) is reserved for exactly this pair.
_LEAK_SUB_REASONS = frozenset({"leak_detected", "unresolved_surrogate"})

_MESSAGES: dict[str, str] = {
    CODE_PROXY_UNREACHABLE: "Could not connect to the configured base URL.",
    CODE_WRONG_ENDPOINT: (
        "Something answered at this address, but it isn't recognizable as Blindfold."
    ),
    CODE_UPSTREAM_AUTH_REJECTED: "The provider rejected the credential (401).",
    CODE_UPSTREAM_UNREACHABLE: "Blindfold could not reach the upstream provider.",
    CODE_FAIL_CLOSED_BLOCK: "Blindfold blocked this exchange (fail-closed).",
    CODE_LEAK_FLAGGED: "Blindfold's own verify pass objected to this exchange.",
    CODE_BLINDFOLDED_OK: (
        "Blindfold is reachable at this URL and blindfolded this exchange."
    ),
    CODE_BLINDFOLDED_OK_RESTORE_UNPROVEN: (
        "Blindfold is reachable at this URL and blindfolded this exchange. The "
        "model did not echo the canary back, so restore could not be observed "
        "this time."
    ),
}

_REMEDIES: dict[str, str] = {
    CODE_PROXY_UNREACHABLE: (
        "Start the Blindfold proxy, or check that the base URL matches its "
        "actual bind (host/port)."
    ),
    CODE_WRONG_ENDPOINT: (
        "Double-check the host/port -- another process may be listening there."
    ),
    CODE_UPSTREAM_AUTH_REJECTED: (
        "Check the credential you configured for this test (API key/token)."
    ),
    CODE_UPSTREAM_UNREACHABLE: (
        "Blindfold's own upstream connection failed (timeout or server error). "
        "Check the provider's status and Blindfold's upstream configuration."
    ),
    CODE_FAIL_CLOSED_BLOCK: (
        "See the reference below and the management app's Home/Status page."
    ),
    CODE_LEAK_FLAGGED: (
        "Treat this as a privacy incident, not a connectivity issue -- do not "
        "retry traffic through this proxy until it's investigated."
    ),
    CODE_BLINDFOLDED_OK: (
        "Scope: proxy-side only. Verify separately inside your client (e.g. "
        "Claude Code's /status) that its base URL points at this same proxy."
    ),
    CODE_BLINDFOLDED_OK_RESTORE_UNPROVEN: (
        "Scope: proxy-side only. Verify separately inside your client that its "
        "base URL points at this same proxy."
    ),
}

_UPSTREAM_HTTP_STATUS_RE = re.compile(r"upstream returned HTTP (\d+)")
_AUTH_REJECTED_STATUSES = frozenset({"401", "403"})

# Q3: this feature's own stated purpose is to prove reachability of *this
# machine's own proxy* -- never to place an arbitrary outbound POST (carrying
# caller-supplied headers) on Blindfold's behalf. Restricting to loopback closes
# that SSRF-shaped hole without narrowing the feature: the Connect page always
# computes ``base_url`` from GET /v1/status's own host/port, which is itself
# loopback-bound by default (ADR-0021).
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback_base_url(base_url: str) -> bool:
    """True iff ``base_url``'s host is a loopback address.

    ``urlsplit(...).hostname`` already lowercases the host and strips an IPv6
    literal's brackets, so ``http://[::1]:1234`` normalizes to ``::1`` here.
    """
    try:
        hostname = urlsplit(base_url).hostname
    except ValueError:
        return False
    return hostname in _LOOPBACK_HOSTS


@dataclass(frozen=True)
class TestConnectionVerdict:
    """A typed test-connection outcome (Q4): a code, never a bare "failed" string."""

    code: str
    ref: str | None = None

    @property
    def message(self) -> str:
        return _MESSAGES[self.code]

    @property
    def remedy(self) -> str:
        return _REMEDIES[self.code]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "remedy": self.remedy,
            "ref": self.ref,
        }


def _verdict(code: str, ref: str | None = None) -> TestConnectionVerdict:
    return TestConnectionVerdict(code=code, ref=ref)


def build_canary_payload(model: str, max_tokens: int = 32) -> dict[str, Any]:
    """The Anthropic Messages payload the loopback exchange sends.

    ``max_tokens`` is capped small (Q1): the cost line the Connect page states
    before the click stays honest.
    """
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": CANARY_INSTRUCTION}],
    }


def classify_transport_error(exc: httpx.TransportError) -> TestConnectionVerdict:
    """A connect-level failure reaching ``base_url`` at all (Q4: ``proxy_unreachable``)."""
    return _verdict(CODE_PROXY_UNREACHABLE)


def classify_response(status_code: int, body: Any) -> TestConnectionVerdict | None:
    """Classify the loopback ``/v1/messages`` call's raw HTTP response.

    ``body`` is the parsed JSON, or ``None`` when the response body could not be
    parsed as JSON at all -- itself evidence this isn't Blindfold answering.

    Returns ``None`` for a response that looks like a genuine Blindfold success
    (200 + an Anthropic Messages-shaped body) -- the caller still owns the
    trace-based egress assertion and the restore-echo check (the honesty split),
    neither of which this pure function has the trace/mapping to perform.
    """
    if not isinstance(body, dict):
        return _verdict(CODE_WRONG_ENDPOINT)

    error = body.get("error")
    if isinstance(error, dict):
        error_type = error.get("type")
        sub_reason = error.get("sub_reason")
        if error_type == "blindfold_blocked":
            ref = error.get("reason")
            if sub_reason in _LEAK_SUB_REASONS:
                return _verdict(CODE_LEAK_FLAGGED, ref=ref)
            return _verdict(CODE_FAIL_CLOSED_BLOCK, ref=ref)
        if error_type == "blindfold_upstream_error":
            message = error.get("message", "")
            match = _UPSTREAM_HTTP_STATUS_RE.search(message)
            if match is not None and match.group(1) in _AUTH_REJECTED_STATUSES:
                return _verdict(CODE_UPSTREAM_AUTH_REJECTED)
            return _verdict(CODE_UPSTREAM_UNREACHABLE)
        return _verdict(CODE_WRONG_ENDPOINT)

    if status_code == 200 and isinstance(body.get("content"), list):
        return None

    return _verdict(CODE_WRONG_ENDPOINT)


def response_text(body: dict[str, Any]) -> str:
    """Concatenate every text block of an Anthropic Messages response body."""
    content = body.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


# The connect leg should fail fast (a stopped proxy must not hang the button); the
# read leg allows for a real provider round trip on a capped, small exchange.
_CONNECT_TIMEOUT_SECONDS = 5.0
_READ_TIMEOUT_SECONDS = 60.0


def _egressed_as_surrogate(
    trace: "ProcessingTraceBuffer", workspace: str, surrogate: str | None
) -> bool:
    """Q2/Q3(b): assert egress from the exchange's own ADR-0035 processing trace,
    not from trusting the loopback call's 200 status. The most recent ``messages``
    record for this workspace is this exchange's own record -- test-connection
    drives its own dedicated loopback call, so nothing else should be racing it in
    practice, but scanning newest-first and stopping at the first workspace/endpoint
    match (rather than assuming ``trace.recent()[-1]``) keeps this correct even if
    something else did.
    """
    if surrogate is None:
        return False
    for record in reversed(trace.recent()):
        if record.workspace != workspace or record.endpoint != "messages":
            continue
        return any(surrogate in hop.get("surrogates", ()) for hop in record.hops)
    return False


async def run_test_connection(
    *,
    base_url: str,
    model: str,
    headers: dict[str, str],
    workspace: str,
    mapping: "SurrogateMapping",
    trace: "ProcessingTraceBuffer",
    client: httpx.AsyncClient | None = None,
) -> TestConnectionVerdict:
    """Run one canary exchange through ``base_url`` (Q3: the proxy's listening
    socket, a loopback HTTP call -- never an internal function call) and return a
    typed verdict.

    ``client`` is the leak-audit network-boundary seam: production builds a real
    ``httpx.AsyncClient``; tests inject one built on ``httpx.MockTransport``.
    """
    payload = build_canary_payload(model)
    url = f"{base_url.rstrip('/')}/v1/messages"
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT_SECONDS, read=_READ_TIMEOUT_SECONDS,
                write=_READ_TIMEOUT_SECONDS, pool=_CONNECT_TIMEOUT_SECONDS,
            )
        )
    try:
        try:
            response = await client.post(url, json=payload, headers=headers)
        except httpx.TransportError as exc:
            return classify_transport_error(exc)
    finally:
        if owns_client:
            await client.aclose()

    try:
        body = response.json()
    except ValueError:
        body = None

    verdict = classify_response(response.status_code, body)
    if verdict is not None:
        return verdict

    surrogate = mapping.surrogate_for(CANARY_EMAIL)
    if not _egressed_as_surrogate(trace, workspace, surrogate):
        return _verdict(CODE_LEAK_FLAGGED)

    if CANARY_EMAIL in response_text(body):
        return _verdict(CODE_BLINDFOLDED_OK)
    return _verdict(CODE_BLINDFOLDED_OK_RESTORE_UNPROVEN)
