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
  audit event — a denied (no role) or failed (unknown surrogate, **mapping cipher**
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
  to the OS app-data convention. Distinct from the **store**: it holds capability
  assets like the **GLiNER cascade** model, never entity data, **mapping**, or RBAC —
  those live in the **store**, which has its own on-disk home (the **Store
  directory**). Never per-**workspace**. _Avoid_: cache dir, app dir, home dir.
- **Store directory** — the on-disk home of the default local **store** (the embedded
  SQLite database file), rooted at `BLINDFOLD_STORE_DIR` and defaulting to the OS
  app-data convention (distinct from the **Data directory**). Exists only for the
  embedded-SQLite backend; the opt-in **shared** Postgres backend keeps its location in
  its `BLINDFOLD_DATABASE_URL` DSN and has no Store directory. Holds entity data,
  **mapping**, and RBAC — the crown-jewel surfaces — so it is the store's durable seat,
  never a cache. _Avoid_: db dir, data dir (that is the assets location).
- **Variation** — a surface form of an entity (full name, first name, initials,
  nickname, misspelling). Resolving variations to one entity is **coreference**. A
  confirmed entity's variation surface lives in the **entity graph** and L2 matches
  it directly; a **provisional** review-inbox entity carries a narrower, derived
  variation surface too (`review.entity_variations`, issue #296 — currently just
  #289's legal-form-suffix stripping for an organisation), consumed by the
  blinder, `leak_gate`, the mint-time coverage check, and — as of ADR-0051 stage 1
  (issue #299, tool descriptions) and stage 2 (issue #300, every message hop) — the
  same deterministic provisional-pair pass, so a referent's bare and legal-form-
  suffixed surface forms are never treated as invisible to each other before a human
  ever reviews the candidate, in any of those surfaces. The #303 amendment (issue
  #308) widens the tool-description surface to every free-text `description` nested
  inside `input_schema`/`parameters` (`properties.*.description`,
  `properties.*.items.description`, `$defs.*.description`) — same pass, same
  deterministic-only scope, no new surface class. Issue #306 extends the same pass
  once more, mirroring restore's own positional-alignment rule (ADR-0036, amended
  by #304) onto the blinding side: when a provisional referent's `real` and
  `provisional_surrogate` have equal word counts, a bare occurrence of one of
  `real`'s own words blinds to the aligned surrogate word at the same position —
  the inverse of `engine._component_restore_map`, guarded the same way (stopwords,
  alphabetic content, cross-row ambiguity) and read by both `leak_gate` and the
  blinder from one shared derivation (`engine._provisional_pair_map`), per
  ADR-0051.
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
  - **L1** — deterministic regex/PII detection (emails, phones, IBANs, IDs). Checksum
    and check-digit validated kinds (IBAN, credit card, the four validated German
    `DE_*` IDs) are mounted from presidio-analyzer's **pattern recognizers only** —
    never its NER recognizers (ADR-0003 "Update (issue #317)").
  - **L2** — the curated entity-graph dictionary, matched 4-pass (exact, normalized,
    fuzzy, first-name ambiguity), German-aware.
  - **L3** — **candidate-span adjudication**, run only on spans the deterministic
    passes can't resolve. L3 names the *role*, not a model choice: any on-device
    implementation behind the adjudicator seam is L3 — a local LLM (Ollama /
    oMLX) alone, or a local NER confirmer (GLiNER) chained before the LLM to
    skip the expensive call for spans it can confirm directly (ADR-0033). Full-document
    ML detection is *not* L3 — that would be a new concept requiring its own term
    and ADR (ADR-0003 rejected it deliberately).
- **Candidate span** — a flagged span handed to L3, plus minimal context. Two
  producers feed the same adjudication path: unknown **capitalized tokens** (plus
  fuzzy near-misses and ambiguous first names), and **phone-shaped** digit runs
  that no deterministic pass matched. L3 cost scales with the number of candidate
  spans, not payload size. A span already occupied by an injected **surrogate** is
  never a candidate span — L3 adjudicates unknown real-world referents, not our own
  fakes. The exclusion is position-scoped: the same string at a different,
  unoccupied position can still be a candidate. The two producers are not
  symmetric, and one setting follows from that: a mis-flagged capitalized token
  self-explains to whoever reviews it, a mis-flagged digit run does not — so the
  phone-shaped producer alone can be switched off per **workspace**, an audited
  choice to discover less, never a way to un-protect a registered referent or a
  value the deterministic passes already match.
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
- **Supervisor** — the platform-native GUI process that *launches, monitors, and
  stops* the **proxy** (spawned via `blindfold serve`) and is the desktop user's front
  door to Blindfold. It renders as the **menu bar app** on macOS and the **tray app**
  on Windows — two platform renderings of one concept, not distinct things. It is
  **not** in the request path and holds **no entity data** — a supervisor and shortcut
  surface, never a second place a real value can live. Distinct from the **proxy** (the
  loopback interceptor it runs as a child): "proxy is stopped/degraded" always refers to
  that child, never the supervisor. Everything privacy-critical stays in the proxy.
  _Avoid_: "the server" (blurs supervisor and proxy), "shell" (ambiguous with the
  terminal / `blindfold serve`), agent, daemon (as the canonical word).
- **Launch environment** — the set of `BLINDFOLD_*` values the **supervisor** owns and
  is the *sole author* of when it spawns the **proxy**. The supervisor injects them and
  **strips** any ambient `BLINDFOLD_*` it inherited, so the proxy runs the same way
  whether launched from a menu, a login item, or a terminal — and so a stale variable in
  a shell profile can never reach it. Non-Blindfold variables (`PATH`, `HOME`, locale)
  pass through untouched. Distinct from the **store**'s persisted settings: a launch
  environment names *how to reach* a dependency (L3 endpoint, Transit address), never
  entity data or **mapping**. A field left *automatic* is **omitted** rather than
  defaulted, so a store-persisted setting still decides. Authoritative: ADR-0044.
  _Avoid_: "the config", "env" (both ambiguous across the real environment, the
  supervisor's values, and store settings), profile.
- **Bootstrap** — the *automatic, headless* step that makes a fresh install
  non-empty and self-consistent without human interaction: it seeds the entity
  graph from the vendored seed and grants the bootstrap-admin identity so the
  store isn't RBAC-locked-out of itself. Machine-run at startup, no operator in
  the loop. Distinct from **Setup**. _Avoid_ using "bootstrap" for the human
  first-run flow, and for the human act of editing a **launch environment**.
- **Setup** — the *human-driven* first-run flow an operator walks through to make
  a fresh install *theirs*: claim the install as admin, create the first real
  **workspace**, and populate it with real **entities** (optionally loading the
  vendored seed as **sample data**). Triggered by an **empty store** (no
  **workspace** exists yet) and pointed to from the startup console line. The
  counterpart to **Bootstrap**: Setup is deliberate and interactive, Bootstrap is
  automatic and headless. Scoped to making the *install* theirs — **workspace**, admin,
  entities — and explicitly **not** to machine wiring: pointing Blindfold at a local L3
  daemon or a Transit address is a **launch environment** concern, and lives in the
  **supervisor** because Setup is served *by* the **proxy** and is unreachable when
  misconfiguration stops it starting. _Avoid_: wizard, onboarding, initialization (as the
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
  is plaintext JSON; an **encrypted** variant (file-level crypto, **not** the
  **mapping cipher**)
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
  each candidate's real value and surrounding **context** as **mapping cipher** ciphertext
  (+ a **blind index** on the real value for dedup), never plaintext — the same
  storage class as the **entity graph** and the re-identification **mapping**, and
  the opposite of the deliberately-ephemeral **Processing trace**. Persists only when
  a store and a **mapping cipher** are wired; otherwise in-memory and ephemeral, never
  plaintext on disk.
- **Provisional surrogate** — the fake auto-minted for a novel entity at request
  time, before review; protection happens immediately and non-blocking. Drawn from a
  plausible per-kind pool, and past pool exhaustion from the **reserved namespace**
  (an opaque token, never natural language — ADR-0052).
- **Learning loop** — review actions feed the system: **confirm** grows the entity
  graph; **reject** grows the **allowlist**. Bidirectional; makes detection more
  deterministic over time.
- **Allowlist** — tokens (or, since issue #294, **phrases** — a rejected
  multi-word/coalesced entity, e.g. "Apple Development") marked NOT sensitive,
  so they're never flagged as candidates again. Entries arrive two ways:
  **learned** (a reject verdict from the review inbox) and **seeded** (a
  curated list of common framework/code tokens shipped with Blindfold). Both
  carry identical semantics; a registered **Term** always wins over an
  allowlist entry — the allowlist suppresses novelty discovery, never
  protection. A phrase entry is matched against the hop text span-wise, case-
  and whitespace-normalized (issue #294) — it never implicitly suppresses one
  of its own component words occurring standalone elsewhere.
- **Declared tool vocabulary** — the tool names a request itself declares in its
  tool schemas, plus (issue #297) each name's components split on `_`/`__`/`.`/`-`
  (an MCP name like `mcp__claude_ai_Asana__authenticate` carries the vendor token
  `Asana`). Suppressed from L3 candidacy for that request — never on the L3
  detector singleton, never persisted into the **allowlist** (a request must not
  be able to permanently poison *that* learning loop by declaring a tool named
  after a person). Since issue #302, a *second*, distinct mechanism gives this
  same vocabulary a longer lifetime: a workspace-scoped, process-lifetime
  registry (`engine.DeclaredToolVocabulary`) accumulates every name (and
  component) a **workspace**'s requests have ever declared, consulted in
  `select_candidate_spans` alongside the current request's own set — so
  suppression outlives the single request that declared it (the #74 run-7
  unblocker: a value minted from a tools-less sub-agent hop, before the main
  agentic-loop request ever declared the same tool name). A tool name is
  protocol vocabulary, not user content, so remembering it — unlike persisting a
  *learned* allowlist reject — poisons nothing.
- **Suppression** — ruling a token out of L3 adjudication (allowlist, declared
  tool vocabulary, stopwords, **positional case heuristic**, **payload-region
  confinement**, **case-inconsistency suppression**). Token-granularity by
  default: a region (system prompt, code fence) may inform heuristics but is
  never skipped wholesale. The one span-granular exception (issue #294): a
  multi-word **allowlist** phrase suppresses exactly its own literal
  occurrence — still a bounded span the allowlist itself names, never a
  region. Suppression never affects L1/L2 protection — a suppressed token that
  is a known entity is still blindfolded.
- **Payload-region confinement** — a **Suppression** condition (ADR-0023,
  "Update (issue #301)") that treats a capitalized token's presence in
  `system[]` alone as evidence it is framework/product prose, not a protected
  referent: a token every one of whose occurrences across the whole payload
  falls inside `system[]` is suppressed from L3 candidacy, `system[]`'s own
  hop included; a token occurring even once in `messages[]` or
  `tools[].description` stays a full candidate everywhere. Computed once per
  request on the untouched payload (`extract_system_confined_tokens_messages`
  / `_chat_completions`, engine.py) — never persisted, never state on the L3
  detector, distinct in lifetime from `DeclaredToolVocabulary`'s deliberate
  workspace persistence (issue #302). Run 7 evidence: 25 of 43 review-inbox
  mints were system-confined and every one was a false positive; all 6
  genuine referents occurred at least once in `messages[]`.
- **Case-inconsistency suppression** — a **Suppression** condition (ADR-0023,
  "Update (issue #342)") that treats a capitalized token's own lowercase form
  occurring in the same **request payload** as evidence it is ordinary
  vocabulary, not a protected referent. Only **prose** occurrences count: a
  lowercase form inside an email address, a URL, or a dotted-or-hyphenated
  identifier or filename is evidence about encoding conventions, not about how
  humans write the word, and is excluded — the exclusion that separates the
  common noun `metrics` from the real org `Harrowgate Metrics`, whose only
  lowercase occurrence sat inside `harrowgate-metrics.example`. For a
  multi-word candidate the condition is **conjunctive**: every capitalized
  token needs its own evidence, because a real entity name reliably pairs a
  distinctive token with a generic one, so any-token matching preferentially
  eats real names. Computed once per request on the untouched payload, never
  persisted — the same lifetime as **payload-region confinement**,
  deliberately not `DeclaredToolVocabulary`'s workspace persistence. Distinct
  from the **positional case heuristic**, which tests the same vocabulary
  signal at **hop** scope *and* behind a positional gate: this condition has
  payload scope and no gate, so it **bypasses the Don/Mark/Stone guard** for
  tokens the positional heuristic keeps. That guard is therefore no longer a
  system-wide guarantee about real first names. Run 10 evidence: 14 of 21
  false-positive mints suppressed at a cost of 0 of 6 genuine referents — on a
  fixture whose planted names are deliberately novel non-dictionary words, so
  it cannot exercise the guard it removes. **Threshold, decided (issue
  #345): proportionate evidence** — a token's lowercase occurrences must
  outnumber its capitalized ones, so pervasive vocabulary separates from
  incidental use of an ordinary word as a name. Issue #344's dictionary-word
  fixture measured this against the alternative (bare presence — any single
  lowercase occurrence suffices), which loses a real referent built from an
  ordinary word right alongside every false positive; proportionate evidence
  keeps the referent while still suppressing every false positive. Bare
  presence was removed rather than shipped as a configuration option. The
  condition is **on by default**, wired at the app boundary alongside
  **payload-region confinement**'s own per-request set, for every real
  exchange.
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
  (e.g. `Erika` in `Erika Mustermann`). **Restore** matches components as additional
  **closed-world** keys — distinctive and unambiguous ones only — so a provider that
  abbreviates a full-name surrogate (`Erika` for `Erika Mustermann`) still restores.
  Distinct from a coincidental sub-token, which is never restored (ADR-0024/0036).
  (`Erika Mustermann` is a reserved placeholder name, never assigned by any live
  surrogate pool — issue #292 — so it is safe to use as a worked example here
  without colliding with an actual mint-time surrogate.)
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
- **Declared collision** (ADR-0051 amendment, issue #303/#307) — the invariant "every
  surface the leak gate checks is a surface the blinder rewrites" constrains both
  directions: a field the blinder is *structurally forbidden* to rewrite
  (`tools[].name`/`tools[].function.name` — rewriting breaks tool dispatch; the
  JSON-Schema structural tokens `type`/`required`/`enum` inside `input_schema`/
  `parameters` — rewriting breaks schema validation/argument binding) leaves the
  **pre-egress leak gate**'s checked surface entirely, since a field the blinder
  was never permitted to enter cannot contain a blinder *miss*. A known real value
  found confined to one of these fields does not raise `LeakError`; it is recorded
  as a distinguishable, scrubbed declared-collision (WARNING + audit record +
  processing-trace entry — surrogate/hashed-id/inbox-item-id reference only, never
  the plaintext) instead. Field-scoped, not value-scoped: the identical real value
  occurring anywhere else in the payload still blocks normally. Free-text schema
  prose (`input_schema.properties.*.description`) is not in this closed set — it is
  blindable, so the symmetry there is restored by widening the blinder instead
  (ADR-0023 §3), not by narrowing the gate.
- **Sliding-window restore** — streaming restore that holds back a tail buffer (≥
  the longest known surrogate) so surrogates split across stream chunks are matched
  before emitting; tool-call JSON is reassembled before restoring inside it.
- **Mapping cipher** — whatever encrypts and decrypts the **mapping**'s real values and
  derives their **blind index**. Two kinds (ADR-0045): the **Transit cipher** for the
  **shared** store, and the **Local key cipher** for a single-user local install. Exactly
  one is active per install, chosen by which secret is configured; both configured is a
  startup refusal. With **none** configured, real-value surfaces — the **entity graph**,
  the **review inbox** — are in-memory and ephemeral, never plaintext on disk, while
  surrogate-space and RBAC tables persist normally. Its absence never affects whether
  traffic is protected: **Protected** is a claim about egress, not about remembering.
  _Avoid_: key provider (collides with **L3** provider), crypto backend.
- **Transit** — the OpenBao (MPL-2.0) encryption-as-a-service engine that holds the
  encryption keys and performs encrypt/decrypt; the **Transit cipher** is the
  **mapping cipher** built on it, and the app never holds key material. It holds
  **keys, not data**, and is **not a dataset-distribution channel**: the
  encrypted **mapping** lives in the **store**, and sharing data means connecting
  to a shared store + shared Transit (RBAC-gated), never exchanging a
  Transit-encrypted file.
- **Store key** — the single root secret keying the **Local key cipher**, held by the
  **supervisor** in the platform secret store and injected through the **launch
  environment** (ADR-0044/0045). Named for the **Store directory** it protects. It is
  never exported, escrowed or displayed, and losing it makes that store undecryptable —
  a **startup refusal**, recovered by re-running **Setup**, not a recovery flow.
  _Avoid_: master key, passphrase, recovery key.
- **Blind index** — a deterministic derived column enabling equality lookups over
  encrypted real-value columns without decrypting them, derived by the active
  **mapping cipher**.
- **Fail-closed** — when the full detection pipeline can't run, block by default;
  deterministic L1+L2 still protect known entities. A per-workspace opt-in allows
  degrading to deterministic-only. Two distinct triggers, never conflated (ADR-0009
  amendment, issue #315): a genuine adjudicator-**availability** problem
  (`L3Unavailable`/`blocked-l3-unavailable`, remedy names the deterministic-only
  opt-in) versus an **internal detection defect** — the #179 span-containment
  backstop firing, or any other Blindfold-internal bug (`L3DetectionInternalError`/
  `blocked-detection-internal`, remedy says "report this defect", never suggests
  degrading protection, since that fixes nothing for a code bug).
- **Unprotected mode** — a temporary, **local**, operator-invoked override that
  **suspends all blindfolding**: the detection pipeline does not run, nothing is
  surrogate-replaced, and real **entities** egress to the provider as a pure relay.
  The **only** mode that deliberately crosses **egress** with real values — the exact
  inverse of the every-hop-blindfolded invariant — and thus categorically distinct from
  **fail-closed** and **deterministic-only**, both of which still protect. It is an
  **override on top of** the configured global protection posture, never a change to it:
  resuming returns to whatever posture was set. **Bounded** (next-request / timed /
  infinite) but never silent — while active the **supervisor**'s icon shows a distinct
  alarm state, enabling it is an **audit event**, and auto-revert raises a notification.
  Enforced **proxy-level** (flag + expiry timer live in the **proxy**, so the guarantee
  and the auto-revert survive a **supervisor** crash); scoped to this machine's proxy only,
  never carried across the shared **store**. The **capability is off by default** and
  must be explicitly enabled in Settings before it can be invoked — a fresh install
  cannot have protection disabled by a rogue local process one loopback `POST` away
  (fail-closed instinct applied to the control surface, ADR-0009/0019). Distinct from a
  benign *0-detection
  pass-through* (the pipeline ran and found nothing): there the pipeline runs, here it is
  skipped. _Avoid_: pass-through (use only as a parenthetical gloss), bypass, disable
  protection.
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
- **Supervisor log** (ADR-0046) — a durable, size-bounded (truncated, not rotated),
  allowlisted-by-construction file the menu bar/tray's **supervisor** appends its own
  lifecycle events to: spawn attempt (exe path + args, never an environment value), exit
  outcome (exit code or signal), the already-**scrubbed reason**, and stop/quit. Distinct
  from the **Dismissal log** (L3 curation evidence) and the **Processing trace** (in-memory,
  request-path exchanges): this is process-lifecycle-only and the one durable thing "Open
  Logs" points at. Never the child proxy's raw stdout/stderr — that stream is a separate,
  unaudited, out-of-scope concern (ADR-0046).
- **Diagnostic session** (ADR-0047) — the **proxy** run from a source checkout with the
  `blindfold_devtools` extra installed, so it can write **Exchange captures**. The one
  context in which real payload content is written down, and deliberately **not a mode**:
  there is no flag, no build variant and no artifact — the diagnostic code lives in a
  sibling top-level package no `blindfold.*` module imports, so it is unreachable from the
  frozen entry point and physically absent from every release binary (asserted by a gate
  with a positive control). Refuses to run against a **shared** store or **Transit**, on
  every entry point. _Avoid_: **dev mode**, **debug mode**, diagnostic build (there is no
  build) — a single boolean that "unlocks debugging" is the shape this term exists to
  replace, and `BLINDFOLD_DEV_MODE` was retired to `BLINDFOLD_ALLOW_ROOT_TRANSIT_TOKEN`
  precisely so no such flag could grow back under its old name.
- **Exchange capture** (ADR-0047) — the artifact a **Diagnostic session** writes: one
  JSONL file per exchange, hops nested, covering the whole **round trip** (real inbound,
  blindfolded outbound, provider response, restored response). Two sections of different
  provenance — `observed` (witnessed, including `ExchangeSession.injected`'s full
  surrogate↔real pair table) and `reconstructed` (offsets, catching pass, L3 verdicts,
  produced by replaying through `blindfold explain`) — whose **disagreement is itself the
  signal**. Holds real values in plaintext by definition, opt-in by a named directory,
  count-bounded. The deliberate opposite of the **Processing trace**: same field
  vocabulary, separate schema, and it exists only where the Processing trace's scrubbing
  refusal does not apply because no shipped code is involved. _Avoid_: payload diff, dump,
  trace (that is the Processing trace).

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
- **A surrogate issued without a corpus-disjointness check is opaque by construction.** The
  plausible named pools are checked against the corpus both ways — on issue, and on re-entry as
  a candidate real — so they stay plausible. The pool-exhaustion fallback is checked neither
  way, so it is drawn from a **reserved namespace**: a single opaque token, no natural-language
  word, no free-standing integer, and syntactically closed against ever being minted as a real.
  A natural-language reserved label collides with the corpus that contains it — Blindfold's own
  docs are corpus — and that collision is unfixable rather than merely unlikely, because
  de-colliding it never terminates (ADR-0052, issue #328).
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
- **Detection is reproducible.** The same **hop**, adjudicated under the same
  conditions — the same **entity graph**, **allowlist**, **review inbox**,
  **declared tool vocabulary** and per-**workspace** detection settings — produces
  the same detection outcome. What gets protected never depends on request history,
  process age, or cache state, and no optimisation may buy throughput by making a
  span's verdict a function of what this process happened to adjudicate earlier
  (ADR-0048). This is what makes a reported miss reproducible and a detection
  regression measurable: without it, a real regression and an unlucky sample look
  alike. Two limits, stated so neither is mistaken for a defect. It is a claim
  about **identical conditions, not about repeating a request** — a run that
  confirms a **novel entity** mints a **provisional surrogate** into the review
  inbox, changing the conditions, so the same hop sent twice is legitimately
  adjudicated differently. And the **last mile is not ours**: the adjudication
  requests Blindfold issues are a pure function of the inputs above, but the
  adjudicator returning the same answer to the same request is that process's
  property — Blindfold pins what the protocol exposes (greedy decoding, fixed
  seed) and cannot verify the rest.
- **No diagnostic code ships.** The ability to see real payload content exists only in a
  **Diagnostic session** run from source; every release artifact is asserted to contain no
  `blindfold_devtools` module — and the assertion carries a **positive control** (a canary
  build the same check must fail on), because a green absence check proves nothing unless
  it has been shown to go red. Corollary: the request path gains **no** branch, flag or
  observer seam for diagnostics. Over-redaction is a quality bug and an un-blindfolded
  entity is a privacy bug; a shipped `if dev:` is neither, it is the erosion that produces
  both later (ADR-0047).

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
- Being a general secrets manager. Key custody is delegated — to OpenBao for the
  **shared** store, and to the **supervisor**'s platform secret store for a single-user
  local install (the **Store key**, ADR-0045). Blindfold never invents its own key
  storage, and never offers key escrow, export or recovery.
