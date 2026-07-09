# CONTEXT — Blindfold

Blindfold is a self-hosted proxy that sits in the request path of the LLM tools a
user controls. It **blindfolds** outbound prompts (replacing real **entities** with
**surrogates**) and **restores** real values in the response, so the user works
with clear names while the provider only ever sees plausible fakes. Drivers:
GDPR/compliance + IP protection. Full architecture and decision log: `docs/DESIGN.md`.

**Deployment model (language note):** the **proxy/interceptor is always local,
single-owner** — no tenancy, no auth on the proxy itself, transparent *native*
interception (never provider translation/substitution). The only thing shared across
machines is the **surrogate DB** (entity graph + mapping + re-identify store). So
"**shared**" / "**multi-user**" always means *several people sharing one mapping store*,
never *one gateway serving many tenants*; every access-control concern lives on the
**management API over the shared store**, not the proxy. Authoritative: ADR-0020.

This file is the project's **ubiquitous language**. Use these terms (not synonyms)
in issues, tests, code, and docs. If a needed concept isn't here, that's a signal
to add it via `/grill-with-docs`, not to invent a synonym.

## Glossary

- **Blindfold** — (verb) replace real entities with surrogates in an outbound
  payload before it leaves the machine. (noun) the system as a whole. Avoid
  "anonymize"/"mask"/"redact" as the primary verb — they imply destruction; we
  pseudonymize reversibly.
- **Restore** — the reverse of blindfold: replace surrogates with their real
  entities in a provider response. Automatic and inline in the request path,
  closed-world. Avoid "de-anonymize"/"unmask". Distinct from **Re-identify**.
- **Re-identify** — the on-demand, RBAC-gated, audited management action of
  resolving a surrogate back to its real entity (e.g. via the management API /
  audit viewer). Not the same as **Restore**: Restore is automatic inline
  reversal scoped to one exchange; Re-identify is a deliberate human/admin lookup
  of the **mapping**. Authorized **iff the referent is tagged to a workspace the
  caller holds the `re-identifier` role on** — a multi-workspace referent is
  re-identifiable from any of its workspaces. Every re-identify **attempt** is an
  audit event — a denied (no role) or failed (unknown surrogate, Transit
  unavailable, decrypt error) attempt is audited too, not just a success (SEC-8):
  an attacker probing for surrogates always leaves a trail.
- **Entity** — a real-world referent that must be protected: a person, organization,
  contact-PII value (email, phone, IBAN, ID), or IP term/codename. An organization worth
  protecting is realized as a **Term**; an internal **Org unit** is graph structure and is
  an Entity only when its name is itself sensitive (then it is also a Term).
- **Term** — a non-person sensitive referent — a real company name, internal codename, or
  secret project/initiative/system name — that must be blindfolded. The non-person
  counterpart to a person Entity; membership in the term set is the single lever that decides
  whether a token is blindfolded. _Avoid_: keyword, tag.
- **Org unit** — a node in the organization's structure (department, division, board),
  carrying hierarchy and role assignments. Structure, **not** a sensitivity signal: an Org
  unit is never blindfolded by virtue of being one. A unit whose name is itself sensitive is
  *also* registered as a **Term**. _Avoid_: department (as the canonical word), team.
- **Surrogate** — the fake stand-in assigned to an entity. Plausible and
  locale-aware for names/orgs; **reserved-namespace** (non-routable, non-colliding)
  for contactable PII. Stable once minted.
- **Mapping** (a.k.a. **re-identification mapping**) — the real↔surrogate record.
  The crown-jewel secret; real-value side is stored encrypted.
- **Entity graph** — the curated store of entities, variations, relationships, and
  surrogates. The authoritative dictionary the deterministic passes match against.
- **Variation** — a surface form of an entity (full name, first name, initials,
  nickname, misspelling). Resolving variations to one entity is **coreference**.
- **Merge** — the curator action that collapses two separate canonical **entities**
  discovered to be the same referent into one. The surviving entity absorbs the
  other's **variations**, **relationships**, and role assignments; the absorbed
  entity's **surrogate** is **retired** (kept restorable, never deleted). The
  inter-entity counterpart to **coreference**, which resolves variations *within* a
  single entity. _Avoid_: link, dedupe, combine, fold-in.
- **Retired surrogate** — a surrogate no longer minted into new exchanges but kept
  permanently restorable and re-identifiable, so historical exchanges never break.
  Retirement is a one-way state produced by **Merge** (the absorbed entity's
  surrogate retires); it is never deletion. _Avoid_: deleted, orphaned, stale.
- **Relationship** — an edge in the entity graph. Drives the **coherent surrogate
  world** and disambiguation. The `relation` label is a **controlled vocabulary**, not
  free text — drift (`employer` vs `works_at` vs `employed-by`) silently breaks the
  logic that keys off it. Current set: **`employer`** (person → org the person works at;
  the edge the coherent world reads to align fake email domains) and **`subsidiary_of`**
  (org → parent org). `alias-of` is **not** a drawable relation — collapsing two entities
  that are the same referent is **Merge**, not an edge. New relations are added
  deliberately, only when coherent-world/disambiguation logic consumes them.
- **Coherent surrogate world** — surrogates whose relationships stay internally
  consistent: a person's fake email domain equals their employer's fake domain;
  locales match; dates are **date-shifted** by a stable per-entity offset.
- **Detection layers**:
  - **L1** — deterministic regex/PII detection (emails, phones, IBANs, IDs).
  - **L2** — the curated entity-graph dictionary, matched 4-pass (exact, normalized,
    fuzzy, first-name ambiguity), German-aware.
  - **L3** — **candidate-span adjudication**, run only on spans the deterministic
    passes can't resolve. L3 names the *role*, not a model choice: any on-device
    implementation behind the adjudicator seam (LLM via Ollama today; a small
    local classifier or a cascade tomorrow) is L3. Full-document ML detection is
    *not* L3 — that would be a new concept requiring its own term and ADR
    (ADR-0003 rejected it deliberately).
- **Candidate span** — a flagged span (unknown capitalized token, fuzzy near-miss,
  ambiguous first name) handed to L3, plus minimal context. L3 cost scales with the
  number of candidate spans, not payload size. A span already occupied by an
  injected **surrogate** is never a candidate span — L3 adjudicates unknown real-world
  referents, not our own fakes. The exclusion is position-scoped: the same string
  at a different, unoccupied position can still be a candidate.
- **Hop** — a single message within a request (system prompt, a user turn, or a
  **tool-result** message). Blindfold rewrites every hop, not just the first prompt.
- **Workspace** — the scoping unit for team access (RBAC), disambiguation context,
  and audit. One canonical entity per real referent, organized by workspace tags.
  Scope binds on the **mapping** itself, not merely on audit visibility: a
  **Re-identify** resolves only if the referent is tagged to a workspace the caller
  is authorized on. The surrogate stays globally stable (one referent → one
  surrogate everywhere); what is workspace-scoped is the *right to unmask it*.
- **Novel entity** — an entity encountered in traffic that is not yet in the
  **entity graph**: not a known entity, not one of its **variations**, not
  **allowlist**ed. L3's verdict on a **candidate span** decides whether a span
  denotes one; it enters the world with a **provisional surrogate** and is
  confirmed or rejected through the **review inbox**.
- **Review inbox** — the queue of **provisional**ly-blindfolded novel candidates
  awaiting human confirmation.
- **Provisional surrogate** — the fake auto-minted for a novel entity at request
  time, before review; protection happens immediately and non-blocking.
- **Learning loop** — review actions feed the system: **confirm** grows the entity
  graph; **reject** grows the **allowlist**. Bidirectional; makes detection more
  deterministic over time.
- **Allowlist** — tokens marked NOT sensitive (e.g. a code identifier
  mis-flagged as a name), so they're never flagged as candidates again. Entries
  arrive two ways: **learned** (a reject verdict from the review inbox) and
  **seeded** (a curated list of common framework/code tokens shipped with
  Blindfold). Both carry identical semantics; a registered **Term** always wins
  over an allowlist entry — the allowlist suppresses novelty discovery, never
  protection.
- **Declared tool vocabulary** — the tool names a request itself declares in its
  tool schemas. Suppressed from L3 candidacy for that request only —
  session-scoped, never persisted into the **allowlist** (a request must not be
  able to permanently poison learning by declaring a tool named after a person).
- **Suppression** — ruling a token out of L3 adjudication (allowlist, declared
  tool vocabulary, stopwords). Always token-granularity: a region (system
  prompt, code fence) may inform heuristics but is never skipped wholesale.
  Suppression never affects L1/L2 protection — a suppressed token that is a
  known entity is still blindfolded.
- **Closed-world restore** — restore only surrogates actually injected for this
  exchange, to avoid restoring a coincidentally-emitted lookalike. Closed-world
  constrains the *referent set*, not the string match: an injected surrogate
  carrying a bounded morphological suffix (e.g. German genitive "-s") is still
  in-world and restores with the suffix transferred to the real value; a string
  that merely *contains* an injected surrogate as a sub-token of an unrelated
  word is out-of-world and is never restored.
- **Egress** — a boundary where data leaves the local machine. Two distinct kinds:
  (1) **Provider egress** — a *blindfolded* payload leaving for the upstream provider
  (`upstream.send_*` / the streaming request); the **pre-egress leak gate** sits here
  and enforces "no real entity crosses egress" as a prevention gate, not post-hoc
  detection. (2) **Adjudicator egress** — the **L3** call, which carries *un-blindfolded*
  **candidate spans** (real values, by definition). No leak gate can guard this boundary
  because the values there are *supposed* to still be real; it is kept safe only by
  requiring L3 to run **on-device** (a local Ollama model). See the local-only invariant.
- **Verify pass** — the two-gate safety net around **egress**: the **pre-egress leak
  gate** blocks *before* a known real value would cross egress; the **post-restore
  resolution gate** asserts, after restore, that no injected surrogate was left
  unresolved (and no coincidental lookalike was restored). Together they replace an
  earlier single post-hoc check that ran only after the blinded payload had already
  reached the provider.
- **Sliding-window restore** — streaming restore that holds back a tail buffer (≥
  the longest known surrogate) so surrogates split across stream chunks are matched
  before emitting; tool-call JSON is reassembled before restoring inside it.
- **Transit** — the OpenBao (MPL-2.0) encryption-as-a-service engine that holds the
  encryption keys and performs encrypt/decrypt; the app never holds key material.
- **Blind index** — a deterministic derived column enabling equality lookups over
  encrypted real-value columns without decrypting them.
- **Fail-closed** — when the full detection pipeline can't run, block by default;
  deterministic L1+L2 still protect known entities. A per-workspace opt-in allows
  degrading to deterministic-only.
- **Scrubbed reason** — a failure reason string that references an offending entity
  by its surrogate or a hashed id, never the plaintext. The pre-egress leak gate's
  one scrubbed reason routes identically to the 503 body, the audit record, and the
  log — a real value that fails to blindfold must not then leak through the error/
  observability surface meant to report it.

## Key invariants

- Every hop of every request is blindfolded before egress. Over-redaction is a
  quality bug; an un-blindfolded real entity is a privacy bug.
- Surrogates are stable: a given entity maps to the same surrogate everywhere.
- Sensitivity (is it blindfolded?) and structure (is it an Org unit?) are independent axes.
  Being an Org unit never makes a referent sensitive, and being sensitive never makes it
  structural; a name that is both is recorded as both an Org unit and a Term.
- The real-value side of the mapping is never stored in plaintext — nor surfaced in
  plaintext on an error/observability surface. A leak_gate violation's 503 body,
  audit record, and log line all carry the same **scrubbed reason**.
- Restore is closed-world. The pre-egress leak gate blocks a known real value from
  crossing egress; the post-restore resolution gate catches any surrogate left
  unresolved afterward.
- **L3 runs on-device only.** The candidate spans handed to L3 are real, un-blindfolded
  values, so the adjudicator endpoint is a privacy boundary (**adjudicator egress**). A
  model that executes remotely (a `:cloud`/remote-execution Ollama model) is **refused at
  startup** — the operator is informed and the process does not run L3 against it. There
  is **no override** (unlike the SEC-2 root-token dev-mode escape hatch): sending real
  candidate spans off-device categorically defeats the product, so this invariant is
  absolute.

## Non-goals

- Intercepting apps whose endpoint can't be redirected (claude.ai web, ChatGPT
  desktop/mobile). Scope is tools where the base URL is configurable.
- Irreversible anonymization. Blindfold is reversible pseudonymization by design.
- Being a general secrets manager. Secret/key custody is delegated to OpenBao.
