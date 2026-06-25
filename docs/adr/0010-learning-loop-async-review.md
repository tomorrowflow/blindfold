# ADR-0010: Learning loop — auto-blindfold provisional + async review inbox

**Status:** Accepted
**Date:** 2026-06-17

## Context

A transparent proxy can't block to ask the user about a novel candidate, and coding
agents time out. Yet novel entities must be protected immediately, and the system
should get more deterministic (less LLM-dependent) over time.

## Decision

On detecting a novel **candidate**, we will **auto-blindfold it immediately with a
provisional surrogate (non-blocking)** and land it in an **async review inbox**. The
user later **confirms** (grows the entity graph → detected deterministically thereafter)
or **rejects** (grows the **allowlist** → never blindfolded again). The loop is
**bidirectional**, making detection more deterministic over time. Over-redaction is a
quality bug, not a privacy bug, so erring toward blindfolding is safe.

## Consequences

- Protection never waits on the user; agents don't stall.
- Requires a provisional-surrogate mechanism and a JSON review-inbox API (the SPA in
  ADR-0011 consumes it).
- Optional historical-transcript mining proposes candidates through the same inbox.

## Alternatives considered

- **Block to ask the user** — rejected: breaks transparent proxying and times out agents.
- **Auto-confirm everything** — rejected: pollutes the entity graph with false positives.

_Migrated from DESIGN.md decision log row 9._
