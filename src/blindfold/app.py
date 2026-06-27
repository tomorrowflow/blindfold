"""FastAPI proxy exposing the Anthropic-compatible ``/v1/messages`` endpoint.

Request path (tracer-bullet slice):
  blindfold every hop  ->  forward to upstream  ->  restore the response  ->  verify pass
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request

from .config import get_settings
from .engine import blindfold_payload, restore_response, verify_pass
from .store import vendored_seed_repository
from .surrogates import SurrogateMapping
from .upstream import UpstreamClient

app = FastAPI(title="Blindfold")

# Process-wide surrogate mapping built from the entity-graph repository seam (the seeded
# real->surrogate pairs, including variations), NOT a hardcoded dict. Keeping it a
# singleton makes surrogates stable across exchanges within the process (leak-audit
# clause E-stable). The default seam is the in-process vendored seed; tests substitute it
# via dependency_overrides[get_mapping]. Postgres-backed persistence lands via the same
# repository seam (ETL into the graph); this slice keeps the request path hermetic.
_mapping = SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())

# Client auth/version headers forwarded upstream. content-type is intentionally omitted
# so it is not duplicated with the JSON body the upstream client serializes.
_FORWARDED_HEADERS = ("x-api-key", "authorization", "anthropic-version", "anthropic-beta")


def get_mapping() -> SurrogateMapping:
    return _mapping


def get_upstream_client() -> UpstreamClient:
    return UpstreamClient.from_settings(get_settings())


@app.post("/v1/messages")
async def messages(
    request: Request,
    upstream: UpstreamClient = Depends(get_upstream_client),
    mapping: SurrogateMapping = Depends(get_mapping),
) -> dict:
    payload = await request.json()

    blinded, session = blindfold_payload(payload, mapping)
    forwarded = {
        key: value
        for key, value in request.headers.items()
        if key.lower() in _FORWARDED_HEADERS
    }
    raw_response = await upstream.send_messages(blinded, forwarded)
    restored = restore_response(raw_response, session)
    verify_pass(blinded, restored, session, mapping)
    return restored
