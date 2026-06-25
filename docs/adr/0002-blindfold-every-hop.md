# ADR-0002: Blindfold every hop of every request

**Status:** Accepted
**Date:** 2026-06-17

## Context

A request to an LLM is not a single prompt. It carries a system prompt, multiple user
turns, and — especially with coding agents — **tool-result** messages containing file
contents read from the user's machine. Real entities can appear in any of these.

## Decision

We will **blindfold every hop** of every request before egress — system prompt, user
turns, and tool-result messages alike — not just the first prompt. Restore runs over
the full response stream.

## Consequences

- Code and data fed back to the model via tool results are protected, not just the
  opening prompt.
- The blindfold/restore pipeline must handle every message shape both providers emit,
  including streamed tool-call JSON (see ADR-0006).
- Establishes the key invariant: an un-blindfolded real entity reaching the provider is
  a privacy bug; over-redaction is merely a quality bug.

## Alternatives considered

- **Blindfold only the first/user prompt** — rejected: leaks via tool results and
  system prompts, defeating the purpose for coding agents.

_Migrated from DESIGN.md decision log row 4._
