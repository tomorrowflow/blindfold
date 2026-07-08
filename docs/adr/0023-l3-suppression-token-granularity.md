# ADR-0023: L3 suppression for coding-agent traffic — token-granularity only

**Status:** Accepted
**Date:** 2026-07-08

## Context

The #57 live verify proved a real agentic Claude Code session through the proxy is
unusable: its system prompt floods L3 with dozens of candidate spans per request,
polluting the review inbox with framework tokens (`Claude`, `Anthropic`, `React`,
`Bash`, tool names) and corrupting tool-calls when those tokens get provisional
surrogates (issue #59). Two facts from that evidence shape the design:

1. **The `system` field is not framework boilerplate.** Claude Code embeds the
   user's `CLAUDE.md` and memory files — containing real protected entities —
   into `system`. Any "skip L3 over the system prompt" strategy would blind
   novelty discovery in exactly the region where this client concentrates
   personal data.
2. **The `tools` array is never scanned at all.** `blindfold_payload` rewrites
   only `system` and `messages[].content`; tool names and free-text
   `description` fields egress untouched today — so the flood does *not* come
   from tool schemas, but a registered **Term** in a tool description would
   cross provider egress un-blindfolded.

Candidate strategies (issue #59): region-aware skipping, code-fence detection, a
seeded allowlist, token-shape heuristics.

## Decision

### 1. Suppression is token-granularity only

**Suppression** (see CONTEXT.md) rules individual tokens out of L3 candidacy. A
region (system prompt, code fence) may *inform* heuristics but is never skipped
wholesale — adjudication scope stays "every hop". Suppression never affects
L1/L2: a registered Term or entity-graph surface always wins (it is checked
before the allowlist in `select_candidate_spans`), so suppression removes
*novelty discovery* for a token, never *protection*. A wrongly-suppressed token
has exactly the risk profile of a human "reject" in the review inbox.

### 2. Three v1 suppression layers

- **Seeded allowlist** — a curated data file shipped in the package, loaded into
  the existing process-global `Allowlist` at startup with semantics identical to
  a learned reject (ADR-0010). Content is evidence-first: the framework tokens
  the live verify actually minted, plus four categories (AI vendors/models,
  languages/frameworks, dev infrastructure, common agent tool names in prose),
  capped at ~150–200 tokens. Curation rule: a token qualifies only if it is a
  public framework/vendor/tool identifier and implausible as a protected
  referent *when unregistered*.
- **Declared tool vocabulary** — the tool names the request itself declares
  (`tools[].name`; `tools[].function.name` on the chat-completions path),
  suppressed for that request only. Extracted at the app boundary and threaded
  as a plain per-request parameter down to `select_candidate_spans` — never
  state on the detector singleton, and **never persisted** into the allowlist
  (a request must not poison learning by declaring a tool named after a
  person). This is the load-bearing fix for tool-call corruption: a token
  cannot be surrogated in text while remaining literal in the tool schema.
- **Expanded stopwords** — `_SENTENCE_STOPWORDS` grows from ~30 entries to a
  real closed-class function-word list (EN+DE: articles, pronouns,
  prepositions, conjunctions, auxiliaries). Function words are essentially
  never entity names; pure quality win.

All token suppression lives in `select_candidate_spans` — one function remains
the single place that decides what is a candidate span. No new pre-filter
stage. The #68 known-surrogate guard is orthogonal by construction (it is
post-adjudication and position-scoped; suppression is pre-adjudication and
token-scoped). Suppressed tokens never reach the adjudicator, so they never
occupy content-cache slots.

### 3. Tool schemas get deterministic-only scanning

`tools[].description` (free text) will be scanned by **L1+L2 only — L3 never
runs there**. This is the one region-scoped decision, made explicitly here
per the guardrail in (1): the deterministic passes are the backstop, and
running L3 over schema prose would reinstate the flood this ADR exists to
kill. Tool `name` and `input_schema` keys stay byte-identical (rewriting them
breaks tool-calling structurally). A Term hit in a description must mint the
*same surrogate* as in message text, or restore coherence breaks. Implemented
as a sibling issue, not inside #59.

### Verification

- `leak-audit`: real novel entities in the same traffic are still detected and
  protected; a registered Term equal to a seeded token is still blindfolded;
  declared tool names do not persist across requests; fail-closed unaffected.
- **Live re-measurement** is the acceptance gate and the escalation trigger:
  re-run the #57 live verify after v1 lands, count residual candidate spans
  and inbox items. The deferred heuristics ship only if still flooded.

## Consequences

- A full agentic session stops flooding the inbox and corrupting tool-calls
  without any zone losing novelty discovery over user-authored text.
- Every seeded token is a novelty-discovery blind spot, permanent until v2
  provenance — hence the small, evidence-first seed.
- Signature ripple: `blindfold_payload` / `blindfold_chat_completions_payload`
  and the `_blindfold_*` helpers gain a per-request `declared_tools` parameter
  (matching how `session` / `l3_detector` / `inbox` already thread).
- **Deferred (v2 / measured follow-ups):**
  - Allowlist **provenance** (`seeded` vs `learned`), user-removability of
    seeds, and seed-update merging that respects user deletions.
  - **Riskier heuristics**, gated on the live re-measurement:
    appears-lowercase-elsewhere; sentence-position + dictionary filtering
    (both can eat real names — `Stone`, `Mark`, `Frank`).
  - A per-request **context object** bundling `session`/`declared_tools`
    instead of parameter threading.

## Alternatives considered

- **Skip L3 over the `system` region** — rejected: the live verify proved that
  region carries the user's own protected entities (embedded `CLAUDE.md` +
  memory files); novelty discovery must not be blind exactly there.
- **Code-fence skipping** — rejected for v1: region-granularity with no
  deterministic backstop beyond L1/L2, and the flood evidence points at prose
  system text, not fences. May return as a *reprioritization* input, never a
  silent skip.
- **Full pipeline over tool schemas** — rejected: schema descriptions are dense
  capitalized prose; scanning them with L3 reinstates the flood.
- **Persisting declared tool names into the allowlist** — rejected: lets any
  request permanently poison learning by declaring a hostile tool name.
- **Provenance-aware allowlist in v1** — deferred: real cost (the plain token
  set becomes a provenance structure) with no v1 behavior difference.
- **Scraped mega seed list** — rejected: every entry is a blind spot; the
  learned allowlist and declared vocabulary mop up the tail.
