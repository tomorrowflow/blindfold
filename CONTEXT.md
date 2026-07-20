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
- **Data directory** — Blindfold's install-global on-disk location for large local
  *assets* (detection models, caches), rooted at `BLINDFOLD_DATA_DIR` and defaulting
  to the OS app-data convention. Distinct from the **store** (entities, **mapping**,
  RBAC) and never per-**workspace**: it holds capability assets like the **GLiNER
  cascade** model, not entity data. _Avoid_: cache dir, app dir, home dir.
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
    implementation behind the adjudicator seam is L3 — a local LLM (Ollama /
    oMLX) alone, or a local NER confirmer (GLiNER) chained before the LLM to
    skip the expensive call for spans it can confirm directly (ADR-0033). Full-document
    ML detection is *not* L3 — that would be a new concept requiring its own term
    and ADR (ADR-0003 rejected it deliberately).
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
- **Role** — a workspace-scoped RBAC grant (ADR-0028). The canonical set is flat,
  no hierarchy: **`viewer`** (read audit events + entity listings), **`curator`**
  (structural edits in fake-space — merge, edge CRUD, rename, surrogate edit —
  **never unmask**), **`re-identifier`** (decrypt a surrogate to its real value;
  every attempt audited), **`admin`** (grant/revoke roles within the workspace).
  Key invariant: **curate ≠ re-identify** — a `curator` is fully productive on
  structure and surrogates without ever holding the right to unmask a real value
  (ADR-0017). The management app's top-bar chips surface only `curator` and
  `re-identifier` (the two day-to-day capability roles); that's a display
  subset, not a different role set.
- **Bootstrap** — the *automatic, headless* step that makes a fresh install
  non-empty and self-consistent without human interaction: it seeds the entity
  graph from the vendored seed and grants the bootstrap-admin identity so the
  store isn't RBAC-locked-out of itself. Machine-run at startup, no operator in
  the loop. Distinct from **Setup**. _Avoid_ using "bootstrap" for the human
  first-run flow.
- **Setup** — the *human-driven* first-run flow an operator walks through to make
  a fresh install *theirs*: claim the install as admin, create the first real
  **workspace**, and populate it with real **entities** (optionally loading the
  vendored seed as **sample data**). Triggered by an **empty store** (no
  **workspace** exists yet) and pointed to from the startup console line. The
  counterpart to **Bootstrap**: Setup is deliberate and interactive, Bootstrap is
  automatic and headless. _Avoid_: wizard, onboarding, initialization (as the
  canonical word).
- **Seed bundle** — a portable **entity-graph** artifact (persons, **terms**, org
  units, **variations**, **relationships** — real names) importable into a
  **workspace** any time it has no **entities** yet — a persistent capability of
  the entity list, not confined to first-run **Setup**. Carries the *dictionary
  of what to protect*,
  deliberately **not** the **mapping**: no **surrogates**, no encrypted real
  values, and **no RBAC grants** (a file must never self-grant a **Role**). On
  import the local install mints its **own** surrogates, so two installs importing
  the same bundle get **divergent** surrogates — a bundle seeds **detection**,
  never shared **re-identification** (that stays the job of a shared **store**). v1
  is plaintext JSON; an **encrypted** variant (file-level crypto, **not Transit**)
  is a deferred v2 option. The vendored **Sample data** is the shipped instance.
  _Avoid_: dump, export file, backup.
- **Sample data** — the vendored **Seed bundle** (ADR-0012) shipped with
  Blindfold, offered as an *opt-in* load inside **Setup**, never silently
  populating a real **workspace**. A demo, not a default. _Avoid_: demo seed,
  default data (as the canonical words).
- **Novel entity** — an entity encountered in traffic that is not yet in the
  **entity graph**: not a known entity, not one of its **variations**, not
  **allowlist**ed. L3's verdict on a **candidate span** decides whether a span
  denotes one; it enters the world with a **provisional surrogate** and is
  confirmed or rejected through the **review inbox**.
- **Dismissal** — L3's `is_entity: false` verdict on a **candidate span**: the
  opposite of confirmation. A dismissed candidate never enters the **review
  inbox** and mints no **provisional surrogate** — distinct from a human
  **reject**, which acts on a candidate L3 already confirmed. Dismissals are the
  bulk of L3 traffic in an agentic session (framework/tool vocabulary in the
  system prompt) and are the raw material the **seeded allowlist** is curated
  from (ADR-0032).
- **Dismissal log** — an opt-in, local-only diagnostic file
  (`BLINDFOLD_L3_DISMISSAL_LOG`, ADR-0032) capturing each distinct
  **dismissal**'s token text — never its surrounding context — deduped per
  process; off by default. Exists solely to give a curator real evidence to
  extend the **seeded allowlist** with, the same evidence-first method issues
  #71/#87 used. v1 curation is manual (a human reads the log and hand-edits
  `seeded_allowlist.txt`); a management-app roundtrip to promote entries
  directly is deferred (v2).
- **Review inbox** — the queue of **provisional**ly-blindfolded novel candidates
  awaiting human confirmation. A **durable real-value surface** (ADR-0037): it holds
  each candidate's real value and surrounding **context** as **Transit** ciphertext
  (+ a **blind index** on the real value for dedup), never plaintext — the same
  storage class as the **entity graph** and the re-identification **mapping**, and
  the opposite of the deliberately-ephemeral **Processing trace**. Persists only when
  a store and Transit are wired; otherwise in-memory and ephemeral, never plaintext
  on disk.
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
  tool vocabulary, stopwords, **positional case heuristic**). Always
  token-granularity: a region (system prompt, code fence) may inform heuristics
  but is never skipped wholesale. Suppression never affects L1/L2 protection —
  a suppressed token that is a known entity is still blindfolded.
- **Positional case heuristic** — a **Suppression** condition (ADR-0033) that
  eliminates English positional-capitalization noise from L3 candidacy before
  any model call. A capitalized token is suppressed when (b) it appears only
  at sentence/quotation/heading/list-marker start in the same **hop** text,
  never mid-sentence in capitalized form (positional evidence) — *and either*
  (a) it appears lowercase elsewhere in the same hop (vocabulary evidence) *or*
  (issue #161) at least one occurrence sits at a list/numbered-marker start
  specifically, not a bare heading or unmarked paragraph start (list-marker
  evidence, for a one-off bullet/skill-list command name that never recurs
  lowercase). The positional gate is always load-bearing: it guards the
  **Don/Mark/Stone failure mode** — a real first name appearing mid-sentence
  in capitalized form always fails (b) and is never suppressed, regardless of
  which of (a)/list-marker evidence would otherwise fire. English-benefiting,
  German-neutral: German capitalizes all nouns mid-sentence, so vocabulary
  evidence rarely fires for German vocabulary and German candidates pass
  through unchanged.
- **Closed-world restore** — restore only surrogates actually injected for this
  exchange, to avoid restoring a coincidentally-emitted lookalike. Closed-world
  constrains the *referent set*, not the string match: an injected surrogate
  carrying a bounded morphological suffix (e.g. German genitive "-s") is still
  in-world and restores with the suffix transferred to the real value; a string
  that merely *contains* an injected surrogate as a sub-token of an unrelated
  word is out-of-world and is never restored.
- **Surrogate component** — an individual word token of a multi-word **surrogate**
  (e.g. `Carla` in `Carla Distel`). **Restore** matches components as additional
  **closed-world** keys — distinctive and unambiguous ones only — so a provider that
  abbreviates a full-name surrogate (`Carla` for `Carla Distel`) still restores.
  Distinct from a coincidental sub-token, which is never restored (ADR-0024/0036).
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
  It holds **keys, not data**, and is **not a dataset-distribution channel**: the
  encrypted **mapping** lives in the **store**, and sharing data means connecting
  to a shared store + shared Transit (RBAC-gated), never exchanging a
  Transit-encrypted file.
- **Blind index** — a deterministic derived column enabling equality lookups over
  encrypted real-value columns without decrypting them.
- **Fail-closed** — when the full detection pipeline can't run, block by default;
  deterministic L1+L2 still protect known entities. A per-workspace opt-in allows
  degrading to deterministic-only.
- **Audit event** — a recorded **real-space crossing or refusal**: every **Re-identify**
  attempt (success, denied, failed — SEC-8), every real-name lookup (hit or miss —
  ADR-0018), every block (fail-closed, leak gate). Surrogate-space structural work
  (**Merge**, surrogate rename, **Relationship** edits, review-inbox triage) is
  *never* an audit event — recording that would be history/versioning, a distinct
  concept requiring its own term. _Avoid_: activity log, event log (for this concept).
- **Scrubbed reason** — a failure reason string that references an offending entity
  by its surrogate or a hashed id, never the plaintext. The pre-egress leak gate's
  one scrubbed reason routes identically to the 503 body, the audit record, and the
  log — a real value that fails to blindfold must not then leak through the error/
  observability surface meant to report it.
- **Processing trace** (ADR-0035) — a live, local, in-memory, count-bounded (~200)
  ring buffer of one scrubbed record per exchange (every hop, streaming and
  non-streaming, including a clean 0-detection pass-through), replacing `tail`-ing
  stdout. Never persisted to the store, evaporates on restart — distinct from
  **Audit event** (a real-space crossing/refusal, durable for the process lifetime)
  and from history/versioning. A record carries stage outcomes/counts/timings and
  surrogate/hashed references only, never a real value, raw hop content,
  candidate-span text, or a payload diff. Exposed viewer-gated and
  workspace-scoped, the same RBAC story as the audit log.

## Key invariants

- Every hop of every request is blindfolded before egress. Over-redaction is a
  quality bug (privacy-safe); an un-blindfolded real entity is a privacy bug. But
  over-redaction is **not free**: a mismatched provisional surrogate (e.g. a
  person-name minted for a tool token) corrupts the live outbound payload and
  degrades the provider's answer on every request until review clears it — so
  detection **precision** and **category-appropriate surrogates** both matter, and
  "erring toward blindfolding is safe" must not be read as "erring toward
  blindfolding is costless."
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
  absolute. The detection signal is provider-specific, not generic: Ollama's is the
  `:cloud` tag (a local daemon can still proxy to a remote model); oMLX's is a
  loopback-only base-url check (plain oMLX has no remote-routing feature of its own, so
  loopback is sufficient there) — a future provider must re-derive its own local-only
  story, not assume either check transfers.

## Controlled vocabulary

- **Relation** (edge label): `employer`, `subsidiary_of`. New relations are added
  deliberately, only when coherent-world/disambiguation logic consumes them.
- **Role** (RBAC grant, ADR-0028): `viewer`, `curator`, `re-identifier`, `admin`.
  This is the full set — no fifth role, and no separate "chip" vocabulary; the
  top-bar chips are a display subset of these four, not a different list.

## Non-goals

- Intercepting apps whose endpoint can't be redirected (claude.ai web, ChatGPT
  desktop/mobile). Scope is tools where the base URL is configurable.
- Irreversible anonymization. Blindfold is reversible pseudonymization by design.
- Being a general secrets manager. Secret/key custody is delegated to OpenBao.
