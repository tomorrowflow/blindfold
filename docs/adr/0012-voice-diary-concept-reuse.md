# ADR-0012: voice-diary — concept reuse + data seed, not coupling

**Status:** Accepted
**Date:** 2026-06-17

## Context

The user's existing voice-diary project (FastAPI/Postgres/Ollama, "vibe-coded") already
contains a proven 4-pass detection design, useful schema patterns, and — crucially —
curated entity data. But it is a messy server we don't want to couple to or rewrite.

## Decision

Blindfold is a **clean reimplementation** that reuses voice-diary's detection algorithm
and schema patterns as **concepts, not code** (4-pass `entity_detector.py`, selective
LLM-validation pattern, canonical+variations / org_units / entity_relationships /
role_assignments shapes). Its curated **data** is imported directly as the cold-start
**seed** (encrypting real values via Transit on import, per ADR-0008). Optional mining
of historical transcripts is proposed through the review inbox (ADR-0010).

## Consequences

- We get proven patterns and a day-one seed without inheriting a messy codebase.
- The clean JSON API (ADR-0011) is the future convergence point with voice-diary.
- No build/runtime dependency on the voice-diary repo.

## Alternatives considered

- **Fork/extend voice-diary** — rejected: couples to a messy server and its tech debt.
- **Start with no seed** — rejected: day-one leakage of already-known entities.

_Migrated from DESIGN.md decision log rows 10 and 18._
