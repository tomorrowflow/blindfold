# ADR-0059: Payload inspection — armed, bounded retention of rewritten leaves

**Status:** Accepted
**Date:** 2026-09-21

## Context

ADR-0035 decision 4 holds that a **Processing trace** record carries "stage outcomes/
counts/timings and surrogate/hashed references only — never a real value, raw hop
content, candidate-span text, or a payload diff". ADR-0047 added a **Diagnostic
session** as the one place real payload content is written down, and made its absence
from every release artifact a gated, positively-controlled property.

Between them, the operator can see *that* an exchange was protected and *which*
surrogates were injected, but never *what the provider actually received*. The product's
central claim is taken on faith. ADR-0047 accepted that for shipped code and moved the
answer to a source checkout — reasonable for debugging, and wrong for the person who
installed Blindfold to be reassured by it. Trust is the product; a privacy tool whose
operator cannot look at its output is asking for belief rather than offering evidence.

ADR-0047 also explicitly rejected "a devtools HTTP route — a route implies a view and a
view implies a bundle". This ADR reverses that for a **product** surface, not a
diagnostic one, and the distinction is load-bearing (see §1).

### What a prototype established before this was written

A throwaway viewer was run against real **Exchange captures** from several live-verify
runs (40 exchanges, six dates). It falsified three assumptions this ADR would otherwise
have encoded, and the corrections are decisions §2 and §3 below.

- **Hop text does not contain the replacements.** `blindfold_devtools.hop_text`'s
  `content_text()` joins only `type: "text"` blocks. Run against a representative agent
  exchange it surfaced **none** of the substitutions: they sat in a `tool_result` body, a
  `tool_use` input, and a tool `description`. A hop-text-grained view would have shipped a
  switch that, on that exchange, changed nothing. Tool descriptions are not hops at all —
  the engine rewrites them via `_blindfold_tools_messages` with no `session.hops.append`.
- **The surrogate pair table does not enumerate the replacements.** `ExchangeSession.
  injected` carries the PII/closed-world restore pairs only. In the reference exchange it
  held **7** pairs while the payload had **51** substitutions; every graph-known entity
  rewrite was absent from it. A view that highlights by pair-table lookup shows roughly a
  quarter of what changed.
- **Diffing real against blindfolded is not a viable substitute.** It finds all 51, but it
  cannot attribute them: word-level alignment glues punctuation and adjacent markup onto a
  span, and on dense interleaved substitutions the matcher collapses many small rewrites
  into one large ambiguous region. More decisively, the shipped surface has no real text to
  diff against — that is the whole point of §2. **The engine is the only component that
  knows which span it rewrote**, so the span record has to come from it.

A fourth observation shaped §5: over-redaction is visible and unremarkable in real
traffic. The reference exchange rewrote a SQL keyword as a person name, and rewrote a
domain label inside an address so the result was neither a valid address nor a
category-appropriate surrogate. `CONTEXT.md` already holds that over-redaction is not
free; this surface makes it observable for the first time.

## Decision

We add **Payload inspection**: an armed, bounded, in-memory retention of the payload
leaves Blindfold rewrote, rendered in the management SPA as the Processing trace's next
grain level, with an audited exchange-level **Reveal** switch.

### 1. It is a product surface, and it is not a mode

It ships. It is *not* a "dev mode", a "debug mode", or a build variant — `CONTEXT.md`
keeps all three under _Avoid_, and ADR-0047 §13's hard cut of `BLINDFOLD_DEV_MODE` stands.
The **Diagnostic session** remains exactly what it was: source-only, absent from every
artifact, gated with a positive control. Nothing here weakens that gate, and
`blindfold_devtools` gains no route, no view and no bundle.

### 2. What is retained is the rewritten leaf, not the hop, and not the body

Retention stores, per exchange, **every string leaf the blindfold pass actually rewrote**,
in the **blindfolded** form only — plus the span offsets recorded at mint time (§3). Leaves
the pass left untouched are not retained.

A leaf is identified by a **stable walk-order id plus a display label** derived from where
it was rewritten (hop kind, and the block or field type — a tool-call input, a tool
description, a user text block, a tool-result body). A verbatim JSON path was considered and
rejected: the nine rewrite sites mutate in place and carry no path, threading one through
all of them is a large mechanical change, and what the view needs is *description* — that a
substitution happened in a tool-call input rather than a user turn — not addressability.

This is narrower than "the outbound body" and *wider* than "hop text": it reaches
`tool_result` bodies, `tool_use` inputs and tool `description`s, which is where the
prototype found the substitutions actually live. It is also much smaller — on the
reference exchange, the rewritten leaves were about 15% of the payload.

Two consequences, stated so neither is discovered later:

- **A real value in a leaf the pass never rewrote is never shown here.** The pre-egress
  leak gate still checks it and still blocks it (ADR-0054), so this is a gap in the
  *view*, not in the protection. But the view's silence there is not evidence of absence.
- **The real side is never retained.** There is no stored copy of the inbound payload and
  no payload diff. ADR-0035's refusal of a payload diff survives intact.

### 3. Spans are recorded at mint time, by the engine

`_HopContext` folds spans into counters and `_finish_hop()` freezes an already-scrubbed
`HopDetail`; ADR-0047 §3 correctly calls this "span detail is destroyed, not hidden" and
rejected retaining `DetectedSpan` lists *for a diagnostic purpose*. The purpose here is a
shipped product feature, and the alternatives are exhausted: the pair table is incomplete
(§Context) and a diff is both unavailable and unattributable.

So the engine records, for each rewritten leaf, the offsets it rewrote **in the
blindfolded text** plus the surrogate written there. It records **no real value** — the
real side is resolved at serve time through the existing audited **Re-identify** path, and
the offsets alone are not a real value. The record is built only while Payload inspection
is armed (§4); when disarmed the engine's behaviour is byte-identical to today.

**Recorded spans may overlap.** `_apply_spans` is deliberately permissive for L3's splice
(`assert_no_overlap=False`): two different novel-entity mints can legitimately claim the same
substring today — a coalesced multi-word mint and a bare-component coverage sweep both claim
the component (issue #292). That is a pre-existing property of L3's mint/coverage-sweep
interaction, not something this ADR introduces or fixes. The record therefore represents
overlapping and nested spans faithfully rather than dropping or merging them, and §7's
rendering must handle them — a renderer that assumes disjoint spans loses one silently.

This is a request-path change, and ADR-0047's "the request path gains no branch, flag or
observer seam **for diagnostics**" is not violated — but the spirit of it is engaged, so:
the flag is read once per exchange, it gates a record rather than a behaviour, and **no
detection, minting, gating or restore outcome may depend on it**. Detection reproducibility
(`CONTEXT.md`) binds here: an exchange must be adjudicated identically armed and disarmed.

### 4. Armed, bounded, announced — off by default

Off on a fresh install. Enabled explicitly in Settings by an `admin`, which is an **audit
event**. While armed it retains the last **5** exchanges and **auto-disarms after 30
minutes**; it disarms on proxy restart and the retained leaves evaporate with it. In
memory only — never the **store**, never disk.

This is the **Unprotected mode** shape (ADR-0038) applied to retention rather than to
protection, and for the same reason: a capability that weakens the default posture is not
switched on by a rogue local process one loopback `POST` away.

**Announced in-app only.** A persistent banner in the SPA while armed, plus the
auto-disarm notification. The **supervisor**'s menu bar alarm state is deliberately *not*
reused: its single meaning is "protection is off", and an icon that also means "text is
being retained" is an icon whose alarm gets discounted. Protection is fully on here.

**Blocked exchanges are retained**, marked "never sent" — a fail-closed block is the most
interesting exchange to look at, and it is the one where a blindfolded payload was fully
constructed and then discarded. The mark is load-bearing: a reader must never conclude
that a retained payload egressed.

**Nothing is retained while Unprotected mode is active.** There the pipeline is skipped, so
the outbound payload *is* the real payload and this capability's entire justification —
"the retained text is entity-free by construction" — evaporates. Retaining it with a stark
label was considered and rejected: it turns a bounded, justified retention into an
unbounded one the moment a second feature is switched on, which is how a privacy
regression arrives, via two individually-sound decisions meeting.

### 5. The switch is a bulk Reveal, at exchange level, audited once

One switch per exchange, defaulting to **blindfolded**. Flipping it resolves the
surrogates in every expanded leaf at once and is **one audit event**, gated on
`re-identifier`. It reverts when the exchange collapses or the operator navigates away; no
timer (a 12-second self-clear works for one revealed chip and is hostile applied to a
document being read), and no persistence across reload, which would fire the audit event on
page load and detach it from intent.

Without `re-identifier` the switch is **disabled, not hidden** — a denied attempt is itself
an audit event (SEC-8).

Per-leaf switches were rejected: they split one intentional act into fragments and make the
audit log a function of how many cards happened to be expanded.

**It resolves `confirmed` surrogates only.** ADR-0035 decision 13's reveal lifecycle already
classifies each as `confirmed` / `pending` / `rejected`; `pending` and `rejected` stay
visibly unresolved with their existing affordances. The real view is therefore **partial by
construction** and must render as such — it is a reconstruction with holes, not the original
payload.

Resolving `pending` candidates out of the **review inbox** was rejected on an access-control
ground worth recording: `GET /v1/management/review-inbox` renders real plaintext gated on
`viewer`, while Reveal requires `re-identifier`. Reading pendings through the inbox would let
this switch hand real values to someone not permitted to Reveal them, through the lower gate.
That asymmetry predates this ADR (issue #152 gated the inbox deliberately); this feature must
not become the first thing to make it load-bearing.

### 6. What it claims: transformation, not verification

The surface's claim is *"here is what your prompt became"*. It demonstrates; it does not
prove. It surfaces the pre-egress leak gate's own verdict, which is already witnessed and
costs nothing, and it states in the UI that it does not detect leaks.

This limit is structural, not a scoping choice: **a missed entity renders identically in
both switch positions**, because a value that was never detected was never rewritten and so
has no span. The prototype confirmed this by planting one — it was invisible in every
rendering tried. Rendering "unchanged" as a first-class visual was rejected: it invites the
reader to read unhighlighted text as "checked and safe", which is precisely the false
assurance the scrubbing regime exists to avoid. Exhaustive verification stays in the
Diagnostic session, where ADR-0047 §10's offline leak check already lives.

### 7. Rendering: elided diff, at the Processing trace's next grain

The Processing trace has grown by grain level — exchange row (ADR-0035 decision 5), per-hop
cards (12), per-hop surrogate chips with audited Reveal (13). This is the next: **the
rewritten text**. Not a new route, and not a native window — `BlindfoldMenuBar` is a
logic-free shell (ADR-0040) and cannot hold RBAC, re-identification and audit.

Each retained leaf renders as a card showing **only the regions around its spans**, with
unchanged runs collapsed to a labelled, expandable elision. Four renderings were built and
compared against real exchanges: a whole-document swap (unreadable at payload scale), hop
cards (hides the interesting thing behind a click), a replacements-first table, and the
elided diff. The elided diff won on the question "can a real exchange be read at all"; the
replacements-first table is retained as a secondary view because it is the one that made
the over-redactions in §Context obvious at a glance.

### 8. No date filter on the shipped surface

The prototype grew one and it disproved itself: with a bound of 5 exchanges and a 30-minute
auto-disarm (§4), every retained exchange is from the current process's last half hour, so
every date range but "today" is empty by construction. Time-ranged selection belongs to the
Diagnostic session's on-disk captures, where `blindfold_devtools captures` already lists by
time. If a future bound makes retention span days, this is the decision to revisit.

### 9. Narrowed by issue #415 (ADR-0051's #406 amendment): the span half of §3 is always-on

§3's last sentence — "The record is built only while Payload inspection is armed (§4); when
disarmed the engine's behaviour is byte-identical to today" — no longer holds for the
**offsets** half of the record. ADR-0051's #406 amendment found a second consumer for the
same splice-derived accumulator §3 introduced: the blinder's own self-poisoning guard
(`_injected_surrogate_ranges`, ADR-0022 issue #68), which used to locate its own prior output
by *searching* the text for surrogate values — unable to tell "we spliced this here" from "the
client typed a string that happens to equal a live surrogate". A privacy control cannot run on
a record that exists only while an unrelated operator toggle happens to be on, so issue #415
split the record in two: `ExchangeSession`'s per-leaf **span offsets** (`_LeafAccumulator.spans`)
are recorded on every exchange, armed or not; the **text** field, `rewritten_leaves()`, the
bounded store, its endpoint, and the Unprotected-mode check all keep exactly the behaviour §3-§4
describe, gated on arming precisely as written.

This text is left as written rather than rewritten: §3 meant what it said, and this is the
decision that narrowed it. The **one** externally visible consequence: a real value in
client-typed text that merely resembles a live surrogate is now blinded rather than silently
skipped, regardless of whether Payload inspection is armed — more protection, not less. See
ADR-0051's #406 amendment for the full mechanism and the leaf-identity contract this promotion
depends on.

## Consequences

- The operator can finally see what the provider received, in a release build, without any
  shipped surface gaining a **new** way to show a real value: the real side comes from the
  audited Re-identify path that already existed.
- **ADR-0035 decision 4 is narrowed.** Its "never a real value … or a payload diff" clauses
  stand unchanged; its "raw hop content" clause no longer holds absolutely — rewritten
  leaves may be retained, under §4's arming, outside the ring buffer. A forward-pointing
  note is added to ADR-0035; its text is not rewritten, so a reader can see that it meant
  what it said and when that stopped being true.
- **ADR-0047 is not reversed.** No diagnostic code ships; the absence gate and its positive
  control are untouched; `blindfold_devtools` gains nothing. Its rejection of "a devtools
  HTTP route" is unaffected — this is a product route, not a devtools one.
- The request path gains a mint-time span record (§3). This is the cost the prototype
  surfaced and the one to watch: it must never influence a verdict, and the detection
  reproducibility invariant is the test of that.
- A default install's privacy posture is **unchanged** — an operator who never arms Payload
  inspection pays none of its cost.
- Over-redaction becomes observable for the first time, which will surface quality defects
  that already exist and are currently invisible. That is the point, but it should be
  expected rather than read as a regression.
- **Open, deliberately not decided here:** whether retained leaves need redaction of their
  own (an entity-free payload still carries API keys and source code — ADR-0047 left the
  same question open for captures); whether the `viewer`/`re-identifier` asymmetry on the
  review inbox should be closed; and whether `/v1/chat/completions` reaches parity, since
  devtools' own live capture does not cover it today.

## Alternatives considered

- **Keep it source-only, in the Diagnostic session** — rejected: it answers the debugging
  question and not the trust question, and the person who needs reassurance is the one who
  will never run a source checkout.
- **Retain the whole outbound body** — rejected: strictly more exposure for the sake of
  leaves the pass never touched, and it would retain material the view cannot render.
- **Retain hop text only** — rejected on evidence: it contains none of the substitutions in
  a representative agent exchange (§Context).
- **Derive spans from the surrogate pair table** — rejected: incomplete by construction, and
  silently so. It would mark a quarter of the rewrites and look correct doing it.
- **Derive spans by diffing retained real against blindfolded text** — rejected twice over:
  it requires retaining the real payload, and the diff cannot attribute dense substitutions.
- **Always-on retention in the existing ring buffer** — rejected: every install would hold
  recent payload text whether or not anyone ever opens the view, and the memory cost at ~200
  records is unbounded in practice.
- **A "watch the next exchange" arming instead of retention** — rejected: the exchange worth
  looking at is always the one that already happened.
- **Reusing the supervisor's alarm icon while armed** — rejected: see §4. One alarm, one
  meaning.
- **Per-leaf reveal switches, or one audit event per surrogate** — rejected: see §5.
- **Refusing to run against a shared store**, mirroring ADR-0047 §7 — rejected, and the
  reasoning is the point: devtools refuses because `explain` reads the entity graph's
  ciphertext *outside* the audited path. This feature goes *through* that path, gated and
  audited, and the proxy is per-machine, so the retained leaves are only ever this
  operator's own. The rule does not transfer because its reason does not.

## Amendment (2026-09-23, issue #431): its own destination, a selectable retention window, and window-relative filters

Decided by the operator after using Payload inspection live. **Decided; not re-derived.**
Three changes, additive to §4, §7 and §8; nothing else about the surface moves.

### §7 narrowed: Payload inspection gets its own primary-nav destination

It moves out of the Processing trace's expansion into its own primary-nav destination,
named **Payload inspection** (the glossary term; _Avoid_: prompt viewer, preview, payload
diff).

**Provenance.** §7 read its placement off an instruction to integrate the prototype "into
the existing [UI], not a separate UI." The prototype was a standalone HTML file, so a
destination *inside the management SPA* already satisfies that instruction — it never
required nesting under the Processing trace. That grain-level argument is superseded; the
rest of §7 stands unchanged: an elided diff renders primary, the replacements-first table
is the secondary view, and it is not a native window (`BlindfoldMenuBar` still holds no
RBAC, re-identification or audit).

**Why it needed to move.** The Processing trace is a scrubbed engineering follow-along —
ADR-0035 decision 4 promises "never … a payload diff" for it — while Payload inspection
answers a different reader's question, *"what did my prompt become?"* Nesting the second
inside the first made the trace's own promise false and buried the user-facing question
behind an engineering table.

The Processing trace keeps a per-row link to the retained exchange, when one exists, and
stops rendering retained leaves inline.

### §4 narrowed: a selectable retention window

Arming is no longer one fixed shape. An `admin` picks one of three windows when arming,
each with its own count bound; the ring evicts the oldest exchange on whichever bound is
hit first:

| Window | Auto-disarm | Count bound |
|---|---|---|
| 30 minutes (default) | after 30 min | 25 exchanges |
| 2 hours | after 2 h | 100 exchanges |
| Until disarmed | never on a timer; bounded by the limit only | 200 exchanges (matches the Processing trace's own ring, decision 3) |

Everything else in §4 stands: off by default; arming is `admin`-only and an **audit
event**, which now also records the chosen window; in memory only, never the **store** and
never disk; it disarms on proxy restart in every window; announced in-app only; blocked
exchanges are retained and marked "never sent"; nothing is retained while **Unprotected
mode** is active.

**Consequence, recorded so it is not missed later:** "until disarmed" is exactly the
always-on-retention shape the Alternatives section rejected for *every install*. It is
acceptable here only because it stays opt-in, `admin`-armed, bounded by count, bannered
while active, and restart-scoped — properties the rejected always-on alternative had none
of. That distinction is the one to preserve if a future change proposes making any window
the default.

### §8 narrowed: filters return, relative to the window

§8 named its own revisit condition: "if a future bound makes retention span days." A
200-exchange, until-disarmed window meets that in spirit, so the exchange list gains
filters. They stay relative to the retention window, never calendar ranges — retention is
in memory and restart-scoped, so the prototype's month presets and from→to date inputs stay
rejected:

- time presets (last 15 minutes / last hour / today / all retained), plus a per-hour
  histogram
- an outcome filter (sent / never sent)
- a search over the **blindfolded** text only — it can never match or reveal a real value,
  because no real text is retained
