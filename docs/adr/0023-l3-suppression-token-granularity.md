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

## Update (issue #294): the allowlist gets one span-granular exception

The #74 live-verify run 5b proved Decision 1's "suppression is token-granularity
only" rule has a hole specifically for **learned rejects**: `reject_review_item`
(app.py) stores the rejected item's whole `real` value — which, since #162/#167,
can be a coalesced multi-word/multi-type span ("Apple Development",
"Store directory") — as one `Allowlist` entry, but `select_candidate_spans`
only ever consulted the allowlist by exact single-token equality
(`allowlist.contains(token)`). A phrase entry can never equal a single token,
so a multi-word reject was silently undone on the very next hop: both
components re-flag, L3 re-coalesces them (#162) into the same entity, and the
row re-mints with a fresh id — with no manual escape from the #293 leak-gate
deadlock a stuck multi-word false positive causes.

**Amended decision:** suppression stays token-granularity for every condition
in this ADR (seeded singles, stopwords, declared tool vocabulary, positional
case heuristic) *except* one: a **multi-word allowlist entry** (learned or, in
principle, seeded — the mechanism doesn't distinguish provenance) is now
matched against the hop text as its own literal span
(`l3._allowlisted_phrase_ranges`, case- and whitespace-normalized), and every
token whose position falls inside that span is excluded from candidacy — run
*before* L3 ever adjudicates, so the acceptance test asserts zero adjudicator
calls for the rejected span, not just zero re-mint. This is still not
region-granularity (Decision 1's actual guardrail): the suppressed span is
exactly the phrase the allowlist names, never a system-prompt-wide or
code-fence-wide skip, and it is reachable only through an existing entry, never
inferred from context.

**Component-suppression is explicitly NOT implicit.** Rejecting "Apple
Development" does not add "Apple" or "Development" to the allowlist on their
own — a standalone later occurrence of either word alone still mints normally
(pinned by `test_rejecting_a_multiword_phrase_does_not_implicitly_suppress_a_lone_component`,
tests/test_multi_word_allowlist_reject.py). `Bergmann` alone is not obviously
non-sensitive just because "Sarah Bergmann" was rejected in one context — the
same reasoning issue #292 already applied on the mint-time-collision side.

**Seeded-allowlist file contract is unaffected.** `seeded_allowlist.txt`'s
loader (`allowlist_seed.py`) already treats each whole line — whitespace
included — as one token/phrase; no parsing change was needed to make a phrase
representable there. Its header comment gains a one-line note that a line may
itself be a multi-word phrase, but the "one token per line" *file* contract
(one entry per line) is unchanged.

Protection-always-wins (a registered Term or entity-graph surface beats any
allowlist entry) is unaffected: `select_candidate_spans` still checks
`known_surfaces` before either allowlist condition, and L2's dictionary pass
(`detect_l2`) has already rewritten any registered entity's text to its
surrogate before L3 candidate selection ever sees it.

## Update (issue #302): declared-tool suppression gets a second, workspace-scoped lifetime

The #74 live-verify **run 7** measured the residual this ADR's own "per request only"
scoping (Decision 2, "Declared tool vocabulary") left open: inbox item 17
(`real='Agent'`) minted from ordinary prose in a request that carried no `tools`
array at all (a sub-agent/short-context call — the same shape #297's `Asana` mint
came from in run 6). By the time a later, main-agentic-loop request declared
`tools[].name == "Agent"`, this ADR's suppression had already expired — it never
had a chance to keep "Agent" out of L3 candidacy for the request that minted it,
because that request is exactly the one shape (no declared `tools`) the per-request
set cannot see. Run 7 died: 14 blocked exchanges, 9 of them on this one value,
unrecoverable without a human `reject` (#294) — `tools[].name` is a surface the
deterministic blinder is structurally forbidden to rewrite (rewriting a tool's own
name breaks dispatch), so once a value is provisional and also a declared tool
name, nothing in the request path can un-stick it going forward.

**Amended decision:** a *second*, distinct mechanism now gives the same declared
vocabulary a longer lifetime, alongside — not instead of — the per-request set
Decision 2 already describes. `engine.DeclaredToolVocabulary` is a workspace-scoped,
process-lifetime registry: every tool name (and #297 component) any request in a
**workspace** ever declares is recorded into it, and the *effective* suppressed set
`select_candidate_spans` consults for every subsequent request in that workspace —
tools-array-bearing or not — is the union of that request's own declared names and
everything the workspace has ever declared. Once a name has been declared at least
once, it stays suppressed from L3 novelty discovery in that workspace from then on,
closing the exact gap run 7 measured: a sub-agent hop with no `tools` array, sent
after the main request has already taught the workspace that name, no longer mints
it fresh.

This is deliberately **not** a reinterpretation of "never persisted" from Decision
2 — that clause is about the **allowlist** specifically (`_allowlist`, the
seeded/learned-reject store), and stays true unchanged: a declared tool name is
still never added there, so a request still cannot poison *that* learning loop by
declaring a tool named after a person. `DeclaredToolVocabulary` is a different
store, in-memory only (mirrors `policy.WorkspacePolicies` — no persistence across
a proxy restart), scoped by workspace rather than global, and reasoned about
separately: a tool name is protocol vocabulary derived from the traffic itself,
not curated or learned from a human verdict, so remembering it poisons nothing the
allowlist's non-persistence guard exists to prevent.

Scope discipline unchanged from this ADR's original decisions:

- Suppression still removes **L3 novelty discovery only**. A workspace-remembered
  declared-tool name that is also a registered **Term** or entity-graph surface is
  still blindfolded by L1/L2, which run first and win — re-pinned by test for the
  persisted set specifically, not just the per-request one.
- `leak_gate` is untouched. This closes future mints of a value that collides with
  a declared tool name; it does not retroactively un-mint an already-existing
  provisional entity like run 7's item 17 — that remains an operator-`reject`
  (#294) matter, tracked as a separate, human-scoped question (whether `leak_gate`
  should stop checking `tools[].name` at all is filed separately, not decided here).
- Workspace scoping is a real boundary: a name declared under one workspace does
  not suppress candidacy for a different workspace's traffic.

**Cost, stated plainly rather than left implicit.** Widening suppression's
lifetime from one request to the workspace widens the same accepted blind spot
this ADR's original Decision 2 already named — a token that happens to be both a
declared tool name and a genuine, never-registered real referent (an
implausible but not impossible collision, same class as any seeded token) is now
invisible to novelty discovery for the rest of the workspace's process lifetime,
not just for one request. This is not a new risk in kind, only in duration: the
same "suppression removes novelty discovery, never protection" guardrail still
holds (a registered Term or entity-graph surface is unaffected), and the
alternative — the run-7 permanent deadlock — is strictly worse for an unattended
proxy.

## Alternatives considered

- **Skip L3 over the `system` region** — rejected: the live verify proved that
  region carries the user's own protected entities (embedded `CLAUDE.md` +
  memory files); novelty discovery must not be blind exactly there.
  *(Still rejected as a region skip — but read "Update (issue #301)" below
  before relying on its premise: on the client #74 run 7 measured, `CLAUDE.md`
  and memory arrive in `messages`, not `system`. The update adds a
  token-granularity heuristic that uses the region as evidence; it does not
  reinstate this rejected option.)*
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

## Update (issue #301): a fourth suppression layer — payload-region confinement

### Context premise 1 is no longer true of the measured client

This ADR's Context opens with:

> **The `system` field is not framework boilerplate.** Claude Code embeds the user's `CLAUDE.md`
> and memory files — containing real protected entities — into `system`. Any "skip L3 over the
> system prompt" strategy would blind novelty discovery in exactly the region where this client
> concentrates personal data.

That was measured against the #57 live verify in July 2026. It does not hold on the client #74
run 7 measured (Claude Code `2.1.233`). Grepping run 7's 46 inbound captures for the injection
markers:

```
claudeMd            messages 46   system 0
Memory Index        messages 54   system 0
f.wolf@enersis.ch   messages 46   system 0
```

`CLAUDE.md` contents, `MEMORY.md` contents and the operator's own email address all arrive as
`<system-reminder>` blocks in the **user message stream**. `system[]` mentions the string
`MEMORY.md` only inside the memory-tool *instructions*. The run-7 audit's remark that
`pii-user-0000` came "from the harness system prompt" is loose in the same way — that address is in
`messages`, and L1 caught it there.

`system[]` is **not** unconditionally free of operator data, and this ADR will not claim it is: run
7's own security-monitor subagent ships a system block carrying ``**User identity**: `florianwolf` ``
in 8 requests. So the premise is stale, not inverted — which is exactly why the decision below is a
token-granularity heuristic and not the region skip this ADR's Alternatives still rejects.

### The evidence

Run 7's inbox (43 mints) with every real value matched word-boundary against every string leaf of
every inbound capture, bucketed by payload region:

| | n | verdict |
|---|---|---|
| occurs **only** in `system[]` | **25** | every one a false positive |
| occurs in `messages[]` | 17 | includes **all 6** genuine referents |
| occurs nowhere inbound (minted from the model's own output) | 1 | #304 artifact |

Suppressing these would have removed 58% of run 7's mints at a cost of zero true positives —
including item 17 (`Agent`), whose 13 blocks killed the run.

Two candidate explanations were tested against this data and **falsified**, recorded so they are
not re-proposed:

- **Extend the ADR-0033 positional case heuristic** to heading-/bullet-/list-initial position.
  `_is_positional_case_noise` suppresses a token only when it is *never* capitalized mid-sentence
  in the hop, and these terms all are (`"…are covered by Production Reads and Remote Shell Writes
  instead."`, `"…is Sensitive-Source Provenance's to judge…"`, `"…that is Git Destructive's
  business.)"`). The positional gate is already disqualified by design; widening which *other*
  positions it recognises changes nothing for this class.
- **Seed the observed words.** Open-class: the next harness revision ships a different taxonomy.

An adjudicator-side fix was also weighed and is not sufficient on its own: `Docker Swarm`,
`Azure Blob`, `Slurm`, `Nomad` and `Let's Encrypt` *are* real product names, and no prompt can tell
a product name from a company name without knowing whose data is being protected. ADR-0032 already
said this in passing — "a permanent novelty-discovery blind spot until v2 provenance lands."

### Amended decision

A fourth suppression condition joins the three in Decision 2, at the same granularity:

> A candidate token **every one of whose occurrences in the payload falls inside `system[]`** is
> suppressed from L3 novelty discovery. A token that occurs even once in `messages[]` or in
> `tools[].description` stays a full candidate everywhere, `system[]` included.

This is **not** the region skip rejected in Alternatives, and Decision 1's guardrail is intact: the
region is evidence *about a token*, never a switch on a subtree. Nothing is skipped — every hop is
still adjudicated, and a token that appears on both sides of the boundary is adjudicated in
`system[]` too. Checked row by row against run 7, it suppresses all 25 system-confined rows and
leaves `Org`, `Store`, `Vault`, `Cytoscape`, `Agent`, `Edit` and `Artifact` as candidates, because
they all occur in `messages` as well — those need the "Update (issue #302)" layer and the seed, not
this one.

Mechanically it is the same shape as the declared-tool layer and inherits its discipline: a
per-request `frozenset` computed at the app boundary before any hop is blinded, threaded down to
`select_candidate_spans` as a plain parameter, never state on the detector. `blindfold_payload`
already blinds `system` before `messages`, so the scan must run on the untouched payload. Candidate
selection therefore stays a pure function of its inputs (#261's invariant) — it depends on this
request's payload, never on history or process state, which is what separates this from
`DeclaredToolVocabulary`'s deliberately remembered set.

Scope discipline unchanged: suppression removes **L3 novelty discovery only**. L1 and L2 run over
`system[]` exactly as before, so PII-shaped values and any registered Term or entity-graph surface
are still blindfolded there, and protection still wins (`known_surfaces` is checked first).

### Consequences

- **The residual, stated plainly:** a novel real referent placed **only** in the system prompt,
  which is neither PII-shaped nor a registered Term, is no longer discovered. Registering it as a
  Term restores full protection. Per Decision 1, a wrongly-suppressed token has the risk profile of
  a human "reject" in the review inbox — but this layer's suppressed set is larger than the
  declared-tool set's, so this is the widest such residual the ADR carries and it is named as that
  rather than folded into the others.
- **This residual is client-shaped and will need re-measuring**, because premise 1 above already
  changed once. A harness revision that moves `CLAUDE.md` back into `system[]` silently widens the
  blind spot. The re-measurement belongs to #74's live-verify loop, and the marker grep above is
  the check.
- **It composes with, and does not subsume, the other layers.** #302 covers protocol vocabulary
  declared in the traffic; this covers prose confined to the vendor's own instructions; the seed
  and the learned allowlist cover the tail.
- **ADR-0051's governing-risk condition is what this answers.** See that ADR's "Amendment
  (issue #301)": the trade it accepted was conditional on this precision rate coming down, and
  14% is the measurement that made it due.
