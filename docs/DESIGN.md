# Blindfold — Design

**Status:** Design agreed (greenfield). Implementation not started.
**Last updated:** 2026-06-17

## Purpose

A self-hosted proxy that sits in the request path of every LLM tool the user
controls, replacing real entities (people, orgs, contact PII, IP/codenames) with
**coherent fake surrogates** before the prompt leaves the machine, and restoring
the real values in the response. The user works with clear names; the LLM provider
only ever sees plausible fakes. Context/reasoning stays intact.

**Drivers:** GDPR/compliance + intellectual-property protection. High stakes ⇒
fail-closed leaning. The real↔fake mapping is the crown-jewel re-identification
key and is treated as the primary secret.

> Note: this is **pseudonymization**, not anonymization — the data leaving the
> machine is only safe while (a) surrogates carry no identifying signal and
> (b) the mapping store never leaks.

## Landscape (why this shape)

- **codeburn is NOT a template** — it's a passive token/cost dashboard that reads
  session logs off disk; it does not proxy traffic or anonymize anything.
- Closest references: **DontFeedTheAI** (Ollama+regex reversible proxy w/ vault),
  **pii-redactor** (SqliteVault, consistent tokens), **LLM Guard** (Vault
  Anonymize/Deanonymize), **Presidio** (detection engine everyone builds on).
- **LiteLLM** is the gateway substrate, but its built-in PII de-anonymization is
  buggy/immature ⇒ **we build our own restore layer**.
- **Genuine gap / differentiator:** no mainstream OSS tool does **relational
  entity-linking** (same company ⇒ shared placeholder, coreference, org graph).
  That + a durable, queryable, team-shareable mapping store is the niche.

## Architecture

### Components
1. **Proxy** (FastAPI + LiteLLM). Serves both **Anthropic `/v1/messages`**
   (Claude Code via `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`) and
   **OpenAI `/v1/chat/completions`** (scripts/IDEs). Blindfolds **every hop**
   (system prompt, user turns, **tool-result messages**), not just the first prompt.
2. **Detection** (inline, fail-closed):
   - **L1** deterministic — regex/Presidio for emails, phones, IBANs, IDs.
   - **L2** curated dictionary — 4-pass, German-aware (exact → normalized via
     unidecode → fuzzy Levenshtein ≤2 → first-name ambiguity), stopwords + dedup.
     *(Algorithm design reused as a concept from voice-diary `entity_detector.py`.)*
   - **L3** local LLM (Ollama) — **candidate-span adjudication only**: runs on
     flagged spans (unknown capitalized tokens, fuzzy near-misses, ambiguous
     names) + small context, **not** the whole payload ⇒ latency decoupled from
     file size. Tractable on large code.
   - **Content cache** so unchanged code chunks aren't re-scanned across agent turns.
3. **Surrogate engine** — locale-aware **plausible** names/orgs (Faker), but
   **reserved-namespace** for contactable PII (`.example`/`.invalid` domains,
   reserved phone ranges, test-IBAN ranges) so no routable/colliding PII is
   created. **Coherent**: a person's fake email domain = employer's fake domain.
   **Date-shift** by a stable per-entity offset (preserves intervals). Surrogates
   are **stable once minted**. **Closed-world restore + post-restore verify pass.**
4. **Entity graph** (Postgres) — **global registry + workspace tags**
   (one canonical entity per real person/org; workspaces = unit of team access +
   disambiguation + audit). Tables: persons + variations (coreference), org_units
   (self-ref hierarchy), entity_relationships (generic graph), role_assignments,
   surrogates. **Real-value columns stored as OpenBao Transit ciphertext + a
   deterministic blind-index column** for equality lookups without decrypting.
5. **Security / key custody** — **OpenBao** (MPL-2.0 fork of Vault) **Transit
   engine** = encryption-as-a-service. Keys live in OpenBao, never in the app.
   Per-identity **RBAC** (proxy service vs human get different decrypt rights),
   central **audit** of every de-anonymization, key rotation/rewrap. This is what
   makes the store **secure AND company-shareable** without hand-rolling crypto.
   (Disk encryption from company policy is orthogonal — it gives no RBAC/audit.)
6. **Management app** — **React/Vue SPA + FastAPI JSON API** (the API boundary is
   also the future convergence point with voice-diary). Features: review inbox,
   merge, relationship/org-graph editor (Cytoscape/react-flow), surrogate editor,
   audit viewer, workspace/RBAC admin.
7. **Learning loop** (auto-blindfold + async review) — a transparent proxy can't
   block to ask, and coding agents time out, so: novel candidate → **auto-blindfold
   immediately with a provisional fake (non-blocking)** → lands in **async review
   inbox** → user **confirms (grows dictionary)** or **rejects (grows allowlist)**.
   Bidirectional learning makes the system more deterministic / less LLM-dependent
   over time. Over-redaction is a quality bug, not a privacy bug.
8. **Seeding / cold-start** — one-time **ETL import of voice-diary's curated
   persons/terms/org_units/variations** (encrypt real values via Transit on import);
   optional mining of historical transcripts → review. Avoids day-one leakage of
   already-known entities.

### Linking model (the differentiator)
Consistent per-entity mapping already preserves relationships in the blindfolded
text for free. Explicit relationship modeling additionally provides:
- **Coherent surrogate world** (matching fake domains/locales).
- **Alias / coreference** ("Stefan", "Mr. Wegner", "SW" → one entity → one surrogate).
- **Disambiguation** (two different "Anna"s at different orgs → different surrogates).
- **Org-membership graph** (management/review UX: group, bulk-edit, see shared context).

### Request flow
```
app → proxy (Anthropic/OpenAI format)
    → blindfold each message: L1+L2 full scan, L3 on candidate spans
    → real→surrogate via entity graph (mint+store+auto-blindfold if novel)
    → provider
    → stream response back
    → sliding-window buffered restore surrogate→real  (+ reassemble tool-call JSON,
       escaping-safe; hold back a tail ≥ longest surrogate)
    → verify pass (no real value leaked; no unresolved surrogate)
    → app
```

### Failure policy
LLM layer down/timeout ⇒ **block by default** (deterministic L1+L2 still protect
*known* entities; only novel-discovery is lost). **Explicit, logged, per-workspace
opt-in** to degrade to deterministic-only and keep working.

## Integration reality check
- No global/transparent interception exists. Each app must point at the proxy via
  its base-URL setting (`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`). ~2-line change.
- **Closed consumer apps** (claude.ai web, ChatGPT desktop/mobile) **cannot** be
  redirected — out of scope. Scope = "apps where you control the endpoint": CLIs,
  IDE extensions, scripts.

## Decision log

> The canonical decision records now live as ADRs in [`docs/adr/`](./adr/). This table
> is a quick index; see each ADR for context, consequences, and alternatives.

| # | Decision | Choice |
|---|----------|--------|
| 1 | Threat model | Compliance (GDPR) + IP protection; high stakes |
| 2 | Entity scope | Person names, orgs, contact PII, IP terms/codenames (all) |
| 3 | Form factor | LiteLLM proxy; **own** restore layer |
| 4 | Traffic scope | Everything incl. coding agents; every hop |
| 5 | Surrogate style | Realistic fakes + closed-world + verify pass |
| 6 | Detection timing | Inline always (deterministic full scan + L3) |
| 7 | L3 strategy | Candidate-span adjudication (not full-doc NER) |
| 8 | Linking model | All four: coherent world, coreference, disambiguation, org graph |
| 9 | Learning loop | Auto-blindfold + async review inbox; bidirectional |
| 10 | Diary coupling | Concept reuse / clean reimplementation |
| 11 | Store scope | Global registry + workspace tags |
| 12 | Restore mechanics | Sliding-window buffered streaming + tool-call reassembly |
| 13 | Failure policy | Fail-closed default; per-workspace degrade opt-in |
| 14 | Surrogate generation | Plausible names + reserved-namespace PII; coherent; date-shift; stable |
| 15 | Store security | OpenBao Transit + Postgres ciphertext columns + blind index |
| 16 | Management app | Full SPA (React/Vue) + FastAPI JSON API |
| 17 | Backend stack (derived) | Python/FastAPI + Postgres + Ollama + OpenBao |
| 18 | Seeding | Import voice-diary data + optional history mining |

## Top risks
1. **Restore correctness on code/tool-calls** — the make-or-break problem; needs a
   serious round-trip test suite.
2. **Sub-token over-restoration** (restoring a coincidental "Martin" → "Stefan") —
   closed-world + careful sub-token maps + verify pass.
3. **L3 latency on coding agents** — bounded by content cache + candidate-span, but
   must be perf-tested.
4. **LiteLLM supply chain** — pin a clean version (1.82.7/1.82.8 shipped malware).

## Suggested slices
- **Slice 0 (tracer bullet):** proxy passthrough on both endpoints + Postgres schema
  + diary seed import + deterministic L1/L2 blindfold + non-streaming restore on
  prose; CLI review. Proves the round trip.
- **Slice 1:** sliding-window streaming restore + tool-call reassembly + coherent
  surrogate engine + OpenBao Transit.
- **Slice 2:** L3 candidate-span adjudication + async review inbox + learning loop.
- **Slice 3:** the SPA (review, merge, graph, audit, workspace RBAC).
- **Slice 4:** harden — verify pass, fail-closed policy, perf/cache, multi-user
  RBAC, voice-diary convergence.

## Reusable from voice-diary (concept, not code)
- 4-pass detection algorithm design (`entity_detector.py`), German stopwords.
- Selective-LLM validation pattern (`llm_validator.py`, SSE-streamed to UI).
- Schema patterns: canonical + variations (coreference), org_units hierarchy,
  entity_relationships graph, role_assignments.
- Review/admin UI patterns (the diary lacked merge / relationship-edit / org-graph
  UIs — those are net-new here).
- **Data** is directly importable as the cold-start seed.
