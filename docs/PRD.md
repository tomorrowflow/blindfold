# PRD — Blindfold: reversible LLM-anonymization proxy

> Mirrors GitHub issue [#1](https://github.com/tomorrowflow/blindfold/issues/1) (`enhancement`, `ready-for-agent`).
> Architecture + decision log: [docs/DESIGN.md](./DESIGN.md). Glossary: [CONTEXT.md](../CONTEXT.md).

## Problem Statement

I send prompts to hosted LLM providers (and through coding agents like Claude Code)
that routinely contain real **entities** — colleagues' and clients' names, company
names, emails/phones/IBANs, and confidential IP terms/codenames. Under GDPR and our
IP-protection obligations I cannot hand that data to a US LLM provider, but I still
want to work naturally with clear names and keep the model's reasoning intact. Today
there is no transparent way to ensure the provider only ever sees plausible fakes
while I keep working with the real values — and existing tools either don't restore
reliably, don't link related entities, or aren't shareable with my company.

## Solution

**Blindfold**: a self-hosted proxy in the request path of the LLM tools I control.
It **blindfolds** every outbound **hop** (system prompt, user turns, tool-result
messages), replacing real entities with stable **surrogates**, and **restores** the
real values in the response — including streamed responses and tool-call JSON. The
provider only ever sees a **coherent surrogate world** (plausible, relationship-
consistent fakes; reserved-namespace for contactable PII). A curated **entity graph**
backs consistent replacement, **coreference**, **disambiguation**, and an
org-relationship model. Novel entities are auto-blindfolded with **provisional
surrogates** and surfaced in a **review inbox** where I confirm (growing the graph)
or reject (growing the **allowlist**). The crown-jewel **mapping** is stored
encrypted via OpenBao **Transit** so it is secure and shareable with my company under
RBAC and audit. A management SPA lets me review, merge, edit relationships, edit
surrogates, and read the audit log.

I work with clear names; the provider sees only fakes; context stays intact.

## User Stories

1. As a privacy-conscious user, I want real person names in my prompts replaced with plausible fake names before they reach the provider, so that no colleague or client is identifiable to the provider.
2. As a user, I want organization names replaced with stable fake orgs, so that my employer and clients are not disclosed.
3. As a user, I want emails, phone numbers, IBANs, and ID numbers detected and replaced with reserved-namespace fakes, so that no contactable or financial PII leaves my machine.
4. As a user, I want confidential IP terms and codenames replaced, so that unreleased projects and internal hostnames are never disclosed.
5. As a user, I want the same real entity to always map to the same surrogate, so that the model reasons consistently across a conversation.
6. As a user, I want the provider's response restored to real values automatically, so that I read and act on clear names without manual substitution.
7. As a Claude Code user, I want to point the agent at Blindfold by setting `ANTHROPIC_BASE_URL`, so that my coding sessions are blindfolded with a ~2-line change.
8. As a script/IDE user, I want an OpenAI-compatible endpoint, so that any OpenAI-SDK client can route through Blindfold.
9. As a coding-agent user, I want file contents and tool-result messages blindfolded too, so that code read from my machine and fed back to the model is also protected.
10. As a coding-agent user, I want surrogates to be valid-looking identifiers, so that blindfolded code stays syntactically valid and the model's edits don't corrupt.
11. As a user, I want streamed responses restored on the fly via a sliding-window buffer, so that I keep the streaming experience while still getting real values back.
12. As a coding-agent user, I want tool-call JSON reassembled before restore, so that surrogates inside structured arguments round-trip correctly and escaping is preserved.
13. As a user, I want a post-restore verify pass, so that I'm warned if any real value leaked or any surrogate was left unresolved.
14. As a compliance-minded user, I want the pipeline to fail-closed when detection can't fully run, so that nothing novel is sent unscanned by default.
15. As a user, I want an explicit, logged, per-workspace opt-in to degrade to deterministic-only detection, so that I can keep working during an Ollama outage with known-entity protection.
16. As a user, I want deterministic regex/PII detection (L1) on the full payload, so that high-precision PII is always caught cheaply.
17. As a user, I want a curated dictionary (L2) matched with exact/normalized/fuzzy/first-name passes, so that known entities and their misspellings/variations are caught, German included.
18. As a user, I want a local LLM (L3) to adjudicate only candidate spans, so that novel entities are caught inline without re-scanning entire files.
19. As a coding-agent user, I want unchanged file chunks cached and not re-scanned across turns, so that latency stays bounded on large code.
20. As a user, I want novel entities auto-blindfolded immediately with a provisional surrogate, so that protection never waits on my input and agents don't stall.
21. As a user, I want novel candidates to land in a review inbox, so that I can confirm or correct them later without blocking traffic.
22. As a user, I want to confirm a candidate as a real entity, so that it joins the entity graph and is detected deterministically thereafter.
23. As a user, I want to reject a candidate, so that the token is added to the allowlist and never blindfolded again.
24. As a user, I want to merge two entities that are the same person, so that aliases resolve to one surrogate.
25. As a user, I want to register variations/aliases for an entity, so that first names, initials, and misspellings resolve via coreference.
26. As a user, I want two different people who share a first name kept apart, so that disambiguation assigns them different surrogates.
27. As a user, I want to model org membership and relationships, so that related entities share a coherent fake world.
28. As a user, I want a person's fake email domain to match their employer's fake domain, so that the surrogate world is internally consistent and not obviously synthetic.
29. As a user, I want dates shifted by a stable per-entity offset, so that intervals between events are preserved without leaking real dates.
30. As a user, I want contactable PII surrogates drawn from reserved namespaces, so that I never generate a real third party's routable address or number.
31. As a user, I want surrogates to be stable once minted, so that historical exchanges keep restoring correctly.
32. As a user, I want to edit a surrogate, so that I can fix an awkward or colliding fake while preserving restorability of past exchanges.
33. As a team lead, I want the mapping store shareable across my company under RBAC, so that teammates get consistent anonymization without exposing real values to those without rights.
34. As a security owner, I want the encryption keys held by OpenBao Transit and never by the app, so that we don't hand-roll crypto and the app process never holds key material.
35. As a security owner, I want every de-anonymization (decrypt) centrally audited, so that we can prove who re-identified what.
36. As a security owner, I want key rotation/rewrap, so that we can rotate keys without exposing plaintext.
37. As a user, I want the structured mapping to remain queryable (by surrogate, type, workspace, blind index) while real values stay encrypted, so that the system is both secure and usable.
38. As a workspace owner, I want to scope entities and access to workspaces, so that one team's mappings aren't visible to another and audit is scoped.
39. As a user, I want to import my existing voice-diary entity data as the cold-start seed, so that my known people and orgs are protected from request #1.
40. As a user, I want optional mining of historical transcripts proposed for review, so that I can grow the graph from past material without manual entry.
41. As a user, I want a management SPA with an org/relationship graph view, so that I can see and edit how entities relate.
42. As a user, I want a reactive review inbox in the SPA, so that triaging novel candidates is fast.
43. As a user, I want an audit viewer in the SPA, so that I can inspect de-anonymization events.
44. As a maintainer, I want Blindfold to pin a known-clean LiteLLM version, so that the proxy isn't exposed to the malicious LiteLLM releases.
45. As a user, I want clear feedback when a request is blocked by fail-closed, so that I understand why and can choose to opt into degraded mode.
46. As a user, I want Blindfold to be a clean reimplementation reusing voice-diary's detection and schema *concepts*, so that I get proven patterns without coupling to that messy server.
47. As a user, I want the management API to be a clean JSON boundary, so that voice-diary can later converge on the same backend.

## Implementation Decisions

- **Proxy module.** FastAPI + LiteLLM. Exposes an Anthropic-compatible endpoint (Messages format, for Claude Code via `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`) and an OpenAI-compatible endpoint. Blindfold owns its **restore** layer rather than relying on LiteLLM's de-anonymization (which is immature). LiteLLM is pinned to a known-clean version.
- **Per-hop blindfolding.** Every message in a request — system, user, and tool-result hops — is blindfolded before egress; restore runs on the response stream.
- **Detection pipeline.** Inline, layered: **L1** deterministic regex/PII over the full payload; **L2** curated entity-graph dictionary matched 4-pass (exact → normalized → fuzzy → first-name ambiguity), German-aware with stopwords + dedup; **L3** local-LLM (Ollama) **candidate-span adjudication** invoked only on flagged spans plus minimal context. A content cache prevents re-scanning unchanged chunks across turns. Detection design is reused as a *concept* from voice-diary's `entity_detector.py`/`llm_validator.py`, not as code.
- **Failure policy.** Fail-closed by default when the pipeline can't fully run; deterministic L1+L2 still protect known entities. A per-workspace, logged opt-in degrades to deterministic-only.
- **Surrogate engine.** Locale-aware plausible names/orgs; reserved-namespace surrogates for contactable PII (reserved domains/phone ranges, test-IBAN ranges); relationship-driven **coherent surrogate world** (member email domain = org's fake domain); stable per-entity **date-shift** offsets; surrogates stable once minted. **Closed-world restore** + **verify pass**.
- **Restore mechanics.** **Sliding-window** buffered streaming: emit the safe prefix, hold back a tail ≥ the longest known surrogate, restore on full match. Tool-call JSON arguments are fully reassembled before restoring inside string values, preserving escaping.
- **Entity graph (schema).** Postgres. Global registry with **workspace** tags (one canonical entity per real referent). Core shapes: entities (persons) with **variations** (coreference); organization units with self-referential hierarchy; a generic relationships edge set; role assignments; and a surrogates table. **Real-value columns are stored as Transit ciphertext alongside a deterministic blind-index column** for equality lookups.
- **Key custody / sharing.** Self-hosted **OpenBao** (MPL-2.0) **Transit** engine performs encrypt/decrypt; keys never live in the app. Per-identity RBAC (the proxy service vs a human get different decrypt rights), central audit of decrypts, key rotation/rewrap. This is what makes the mapping secure *and* company-shareable.
- **Learning loop.** Novel candidate → auto-blindfold with a **provisional surrogate** (non-blocking) → **review inbox**. Confirm grows the entity graph; reject grows the **allowlist**. Bidirectional.
- **Management app.** React/Vue SPA over a FastAPI JSON API: review inbox, merge, relationship/org-graph editor, surrogate editor, audit viewer, workspace/RBAC admin. The JSON API is the future convergence point with voice-diary.
- **Seeding.** One-time ETL importing voice-diary's persons/terms/org-units/variations (encrypting real values via Transit on import); optional historical-transcript mining proposed through the review inbox.
- **Backend stack (derived).** Python/FastAPI, Postgres, Ollama, OpenBao.

## Testing Decisions

Good tests assert **external behavior at a seam**, never internal call shapes or
implementation details. External services (the upstream LLM provider, Ollama,
OpenBao Transit) are stubbed at their network boundaries; tests never assert how
many times an internal function was called. This is a greenfield repo, so these are
new seams proposed at the highest level possible (no prior art in-repo yet; the
nearest conceptual prior art is voice-diary's detector tests, reused as a pattern,
not as code).

Seams to be tested:

- **HTTP proxy seam (primary).** Drive a real request through the proxy against a
  **stub upstream provider**; assert (a) the upstream received only surrogates — no
  real entity value — and (b) the client received fully restored real values. This
  is the make-or-break round-trip and must cover prose, streamed responses, and
  tool-call JSON. The most important seam.
- **Blindfold-engine seam (in-process).** `blindfold(messages, store) →
  (blinded, session)` and `restore(response|stream, session) → restored`. Carries
  the bulk of combinatorial behavior: consistent mapping, closed-world restore,
  sliding-window splitting, verify-pass failures.
- **Detection seam.** `detect(text, dictionary) → spans` with L1+L2 real and L3
  (Ollama) stubbed: assert what is flagged, German normalization/fuzzy edge cases,
  candidate-span selection, and allowlist suppression.
- **Surrogate-engine seam.** `mint(entity, relationships) → surrogate`: assert
  coherence (shared fake domains), reserved-namespace membership, date-shift
  stability, and idempotence (stable once minted).
- **Management API seam.** FastAPI test client over the JSON API: review
  confirm/reject (and their effect on graph/allowlist), merge, relationship edits,
  surrogate edits, and audit reads — asserted via API responses and resulting store
  state, not internals.
- **Fail-closed behavior.** With L3 forced unavailable, assert the proxy blocks by
  default and that the per-workspace degrade opt-in produces an audited,
  deterministic-only pass.

## Out of Scope

- Intercepting apps whose endpoint can't be redirected (claude.ai web, ChatGPT
  desktop/mobile apps). Scope is tools with a configurable base URL.
- Network-level TLS interception / MITM of arbitrary apps.
- Irreversible anonymization — Blindfold is reversible pseudonymization by design.
- Building our own key-management/crypto — delegated to OpenBao Transit.
- Coupling to or rewriting the voice-diary server — concept and data reuse only.
- A guarantee of 100% novel-entity recall — candidate-span L3 plus the learning loop
  reduce but cannot eliminate the first-contact gap for a novel entity that looks
  like a plain word.

## Further Notes

- The user's working preference is to define the full functionality first and carve
  implementation **slices** afterward. This PRD is the full design; suggested slices
  (to become separate issues via `/to-issues`): **Slice 0** tracer-bullet round trip
  (both endpoints, schema, diary seed, deterministic blindfold, non-streaming restore
  on prose, CLI review); **Slice 1** sliding-window streaming restore + tool-call
  reassembly + coherent surrogate engine + OpenBao Transit; **Slice 2** L3
  candidate-span adjudication + review inbox + learning loop; **Slice 3** the SPA;
  **Slice 4** hardening (verify pass, fail-closed, perf/cache, multi-user RBAC,
  voice-diary convergence).
- Full architecture and the 18-row decision log live in `docs/DESIGN.md`; domain
  vocabulary in `CONTEXT.md`.
- The top engineering risk is restore correctness on code/tool-calls; the HTTP proxy
  seam exists to pin that down from Slice 0.
- Landscape note: codeburn was evaluated and rejected (it is a passive token/cost
  dashboard, not an anonymization proxy). Relational entity-linking is the
  differentiator versus existing OSS (Presidio, LLM Guard, pii-redactor,
  DontFeedTheAI).
