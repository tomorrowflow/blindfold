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

## Update (issue #342): a fifth suppression condition — case-inconsistency at payload scope

### The measurement

#74 **run 10** (build `5501ef3`) is the first live-verify run with no mid-run intervention, so it is
the first clean read on this ADR's own acceptance gate. It measured **#59 precision at 22% — 6
genuine mints of 27.** This supersedes run 9's 32% (6 of 19), which was flattered by that run's two
deadlocks cutting the source-reading phase short: reading more repo prose produces proportionally
more false positives, so the uninterrupted number is the lower one.

The 21 false positives are dominated by ordinary capitalised English words, seven of them minted as
`person`:

| minted `person` | minted `term` |
|---|---|
| `Pass`, `Pass 1`, `Pass 2`, `Both`, `Named`, `Exists`, `Resolve`, `Surrogate` | `Data`, `Ledger`, `Provisional`, `Engagement`, `Store directory`, `Local Services`, `Local Operations`, `Secret-Store`, `Presidio-for-L1` |

`Pass 1` and `Pass 2` minted as *people* is the sharpest signal: a bare English verb, or a verb plus
a digit, is being classified as a personal name.

**Four of run 10's five blocked exchanges came from this class**, not from any planted entity (items
10 `Local Operations`, 11 `Surrogate`, 12 `Store directory`, 26 `Resolve`). Once such a word is a
known real, the model writing that ordinary word in its own prose trips `leak_gate`. Run 10 recovered
each time — 503s interleaved with 200s — but run 9 deadlocked on exactly this shape and needed a
manual `reject`. **Precision failure in this class is an availability failure, not inbox noise.**

### The three existing layers cannot reach this class, measured rather than assumed

- **Payload-region confinement (#301) correctly declines to fire.** Every one of the 21 false
  positives has at least one constituent token occurring in `messages[]`. This is not a regression in
  that layer; it is a change of *source*. Run 7's flood was the harness's own system-prompt taxonomy
  (25 of 43 mints system-confined). Run 10's flood is **repo file content arriving as `Read` tool
  results in `messages[]`** — which is what #59's acceptance target actually does for a living. The
  #301 layer is structurally unable to help with file-reading traffic, and no widening of it would
  change that without becoming the region skip Alternatives rejects.
- **Declared tool vocabulary (#302) cannot see them.** None of `Pass`, `Resolve`, `Data`, `Both`,
  `Exists`, `Named` is a declared `tools[].name` in run 10's traffic; they occur in tool
  *descriptions*, which this ADR's Decision 3 deliberately keeps L3 out of.
- **Seeding them is rejected for the third time.** Open-class, and run 10 is the proof rather than
  the assertion: the observed vocabulary changed *completely* between runs 7 and 10 because the
  source changed. A seed curated against run 10 would have been useless for run 7 and vice versa.

### What this condition actually changes, stated without euphemism

The signal itself is **not new**. ADR-0033's `_is_positional_case_noise` already tests lowercase
vocabulary evidence — its condition (a). It does not fire on this class because it is conjoined with
a **positional gate**: the token must never appear capitalised mid-sentence. `Pass`, `Both` and
`Resolve` all do appear mid-sentence, so the gate disqualifies them by design, exactly as the
"Update (issue #301)" section already recorded when it tested and falsified widening that gate.

CONTEXT.md names that positional gate as the **Don/Mark/Stone failure mode** guard. So this update's
honest description is: **a new condition that bypasses ADR-0033's guard for tokens ADR-0033 keeps.**
It is additive, not a relaxation of ADR-0033 — that heuristic and its gate stay exactly as they are,
and keep earning their place for German, where all nouns capitalise mid-sentence and vocabulary
evidence rarely fires.

**Evidence scope is the dominant variable, not an implementation detail.** Measured on run 10:

| scope of the lowercase evidence | false positives suppressed | genuine referents lost |
|---|---|---|
| per **hop** (ADR-0033's existing `text` argument) | 7 / 21 | 0 / 6 |
| per **request** (the whole untouched payload) | 14 / 21 | 0 / 6 |
| run-wide / workspace-lifetime | 15 / 21 | 0 / 6 |

Per-hop is not viable: `Pass` occurs in 456 hops but only 87 carry lowercase `pass` in the *same*
hop, and `Named` and `Exists` have same-hop evidence in **zero** of their 14 hops. The condition
therefore needs payload scope, which is a second change beyond dropping the gate.

Two further parameters were measured, both decided by data:

- **Conjunctive over tokens, not disjunctive.** Suppress only when *every* capitalised token of the
  candidate has evidence. Disjunctive suppresses one more false positive (16/21) and *lowers*
  precision to 44%, because it loses `Harrowgate Metrics` and `Project Larkmoor`: a real entity name
  reliably pairs a distinctive token with a generic one, so any-token matching preferentially eats
  real names. Same asymmetry #341's artifact checker relies on with its generic-word stoplist.
- **Prose-lowercase only.** Occurrences inside email addresses, URLs, and dotted-or-hyphenated
  identifiers or filenames do **not** count as evidence. This single exclusion is the whole
  difference between losing a genuine referent and losing none: `harrowgate`'s only lowercase
  occurrence anywhere in run 10's traffic is inside the email domain `harrowgate-metrics.example`,
  and counting it costs the real client org `Harrowgate Metrics`. The rule generalises — a lowercase
  occurrence inside a machine identifier is evidence about encoding conventions, not about whether
  humans write the word as a common noun, and this heuristic's entire premise is the latter.

### Amended decision

A fifth suppression condition joins the four already in Decision 2, at the same granularity:

> **Case-inconsistency suppression.** A candidate token is suppressed from L3 novelty discovery when
> its **prose-lowercase** form occurs in the same **request payload** — excluding occurrences inside
> email addresses, URLs, and dotted-or-hyphenated identifiers or filenames. For a multi-word
> candidate the condition is **conjunctive**: every capitalised token must have such evidence.

Mechanically the same shape as the #301 layer and inheriting its discipline: a per-request
`frozenset` computed at the app boundary from the **untouched** payload before any hop is blinded,
threaded down to `select_candidate_spans` as a plain parameter, never state on the detector. Per
request rather than workspace-lifetime, deliberately: it costs exactly one false positive
(`Provisional`, whose evidence is present in 17 of the 25 payloads carrying it) and preserves #261's
invariant that candidate selection is a pure function of its inputs. #302 broke that invariant
because it had to — the minting request structurally could not see the tool name — and no such
necessity exists here, since the evidence and the candidate are in the same payload by construction.

Scope discipline unchanged: suppression removes **L3 novelty discovery only**. L1 and L2 still run,
so PII-shaped values and any registered Term or entity-graph surface are still blindfolded, and
protection still wins (`known_surfaces` is checked first).

### The aggressiveness threshold is deliberately left open, and the fixture decides it

Two candidate rules are **indistinguishable on run 10's evidence** and diverge sharply in risk:

- **(i) bare presence** — one prose-lowercase occurrence anywhere in the payload suffices.
- **(ii) proportionate evidence** — suppress only where lowercase occurrences dominate the
  capitalised ones, so pervasive vocabulary (`pass`) separates from incidental (`mark`).

Both score 0 of 6 genuine referents lost on run 10, so run 10 cannot choose between them. This ADR
therefore adopts payload scope, the conjunctive rule and the prose-only exclusion, and records the
threshold as **undecided pending the #342 fixture** rather than settling it by argument.

The reason it cannot be settled by argument is the fixture's bias: **every planted name in the #74
brief is a deliberately novel non-dictionary word** — both given-name/surname pairs and codename
tokens alike — by design, so the brief is structurally incapable of exercising the Don/Mark/Stone
case. "0 of 6 lost"
is therefore reassurance the evidence has not earned. Across 23,560 hops of coding-agent traffic
*some* hop almost certainly contains a lowercase `mark`, so rule (i) would suppress a real person on
incidental evidence while (ii) would keep the name. Expectation is that (ii) wins; an expectation is
not a decision.

**Blocking prerequisite.** The condition does not ship until a fixture carrying a real referent whose
name is an ordinary lowercase-able word (a person `Mark Stone`, an org `Northern Data`) demonstrates
which rule keeps protecting it. The gate is a **deterministic offline test** driving the real blinder
and L3 cascade over a scripted payload, not another live run: it is repeatable, runs in CI, and pins
the property permanently, where a live run can only show the failure did not happen that time. #339
is the precedent — verified deterministically plus twelve guard tests, and judged stronger than a
live absence. The existing #74 brief must **not** be mutated to carry the new name: its byte
stability is the only reason runs 5-10 are comparable.

### Verification, with the acceptance bar made numeric

This ADR's original Verification said the deferred heuristics "ship only if still flooded", and
#74/#59 share an acceptance criterion phrased as prose — *"the review inbox is not flooded"*. Nine
runs have produced no checkable verdict against it. Two bars replace the prose, because they fail
independently:

- **#59 inbound code-token precision ≥ 80%.**
- **Zero terminal blocks** in the run — a block from which the session cannot recover without a
  human `reject`.

Precision alone would have passed run 8, which died without producing a deliverable; blocks alone
would tolerate a 22%-precision inbox. Projected against run 10, this condition (14 of 21 suppressed)
composed with #341 (which deletes the five ADR-0036 prose names from the repo) leaves **6 genuine of
7 mints — 86%**, clearing the precision bar. That projection is the target this condition is
measured against in run 11, not a claim already banked.

### Consequences

- **The residual, stated plainly:** a novel real referent whose name is also an ordinary word used
  lowercase in the same payload is no longer discovered. This is a **wider and more predictable**
  residual than the #301 layer's, because coding-agent traffic is enormous and lowercase evidence for
  a common word is nearly certain to appear somewhere in it. Registering the referent as a Term
  restores full protection. Per Decision 1 a wrongly-suppressed token has the risk profile of a human
  `reject` — but that framing is weakest here, since a human reject is a deliberate act and this
  fires silently.
- **ADR-0033's Don/Mark/Stone guard no longer holds globally.** It still holds for its own per-hop
  heuristic; it does not constrain this condition. Anyone reading CONTEXT.md's *positional case
  heuristic* entry as a system-wide guarantee about real first names would now be wrong, which is why
  the glossary carries this condition as its own entry rather than a clause on that one.
- **German is affected differently, and less.** German capitalises all nouns, so prose-lowercase
  evidence for a German noun is rarer — the same English-benefiting/German-neutral asymmetry
  ADR-0033 already records.
- **This ADR is now at its practical limit as an append-only document.** The operative decision —
  what the five suppression conditions are, and what each costs — is reconstructable only by reading
  the base decision plus four updates in order, and Context premise 1 is already documented as stale.
  A consolidating successor ADR is filed separately; deliberately not done here, so that what #342
  decided stays legible.
- **Adjudicator-side kind assignment is a separate defect.** `Pass 1` classified as `person` is a
  cascade precision bug no suppression layer addresses, and it is filed separately. Not fixed here,
  on this ADR's own #301 precedent that no prompt distinguishes a product name from a company name
  without knowing whose data is protected — but the *kind* error is a narrower and more tractable
  question than the *detection* error, and should not be silently folded into this one.
