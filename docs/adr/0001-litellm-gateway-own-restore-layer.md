# ADR-0001: LiteLLM gateway with our own restore layer

**Status:** Accepted
**Date:** 2026-06-17

## Context

Blindfold must sit in the request path of LLM tools the user controls and speak both
the Anthropic Messages API (`/v1/messages`, for Claude Code via `ANTHROPIC_BASE_URL`)
and the OpenAI Chat Completions API. LiteLLM is the obvious gateway substrate, and it
ships a built-in PII anonymize/de-anonymize feature — but that de-anonymization is
immature and buggy, and **restore correctness is our top engineering risk**. A leaked
real entity or a botched restore is a privacy bug, not a cosmetic one.

## Decision

We will build on **FastAPI + LiteLLM** as the proxy/gateway, but own our **restore**
layer entirely rather than relying on LiteLLM's de-anonymization. LiteLLM is **pinned
to a known-clean version** (the 1.82.7/1.82.8 line shipped malware) as a supply-chain
control.

## Consequences

- Full control over the make-or-break round-trip; restore can be tested at the HTTP
  proxy seam against a stub upstream from Slice 0.
- We carry the maintenance of the restore layer ourselves.
- LiteLLM upgrades require a deliberate, audited version bump, not an automatic one.

## Alternatives considered

- **Use LiteLLM's built-in de-anonymization** — rejected: immature, and it would put
  our highest risk in a dependency we can't fully verify.
- **Hand-rolled proxy without LiteLLM** — rejected: loses provider-format breadth for
  no gain on the part that matters (restore).

_Migrated from DESIGN.md decision log rows 3 and risk 4 (LiteLLM supply chain)._
