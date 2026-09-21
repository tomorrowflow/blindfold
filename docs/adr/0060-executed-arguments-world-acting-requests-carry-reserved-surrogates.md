# ADR-0060: A world-acting request carries only reserved-namespace surrogates, and they are never restored

**Status:** Accepted
**Date:** 2026-09-21

## Context

Issue #397, from the #372 live contract spike, run 3. Values are deliberately absent from
this record; the captures on the host carry the specifics.

Blindfold's threat model has always had one consumer on the far side of the blindfold: a
model that *reads* the payload. **Run 3 measured a second consumer that acts on it.**

### The mechanism

Claude Desktop declares a client-side search tool. The model emits an ordinary tool call for
it. Desktop then executes that call by **fanning out a separate request back through the
gateway** — a single-purpose sub-agent call — and *that* request declares the provider-side
web-search server tool with forced `tool_choice`. On the fan-out request the query is not a
tool argument at all: it is plain user prose in `messages[0]`, and the provider executes it
against the live public web.

Traced end to end, the ownership is unambiguous:

| step | what happens |
|---|---|
| 1 | Blindfold blinds a real entity to a plausible person-name **surrogate** (ADR-0005) |
| 2 | the model composes a search tool call carrying that surrogate |
| 3 | **Restore** rewrites the tool-call arguments, so the client receives the real value |
| 4 | the client fans the search out as a new request carrying the real value |
| 5 | **Blindfold blinds it back to the surrogate** — in a request that declares a provider-executed tool |
| 6 | the provider searches for the surrogate and returns profiles, employer and location of a **real person who bears that name** |

Step 5 is Blindfold, with full control of the payload, placing a plausible name into the one
request that will be executed. The deciding signal — a provider-executed tool declaration —
is present in that same payload.

### Why this is more than the deadlock it was filed as

1. **The substitution is invisible.** Restore repairs the tool-call arguments on the way
   back, so the client transcript shows the search running on the name the user typed. A
   user who never hits a block receives a fluent, cited answer about a different human
   being with no reason to doubt it.
2. **A real stranger's name is entered into a public search engine** on the user's behalf.
   By ADR-0005's design, surrogates are names real people have.
3. **The results poison the workspace.** Search output about the surrogate's namesake is
   dense with novel real names, minted as provisional entities with a provenance no
   operator chose.
4. **Then it deadlocks.** The surrogate's own components collide with those fresh mints,
   the pre-egress **leak gate** blocks, and the block is deterministic — the input never
   changes, so the verdict never changes.

Privacy in the strict sense held throughout: no real entity value reached the provider in
any observed exchange, and nothing in this decision relaxes that.

### Why the fix is not at the blinder or the gate

#386, #394 and #387 each closed a real asymmetry between the deterministic blinder's
substitution set and the leak gate's checked set. All three are sound at their own layer and
none could have prevented this, because the colliding input is **manufactured upstream of
that layer** — at mint, in a request whose executability nothing in the system modelled. A
fourth fix at that layer would have been the third wrong answer to the right symptom.

## Decision

### 1. The vocabulary the codebase lacked

- An **executed argument** is a value in an outbound payload that is *acted upon* outside
  the model rather than merely read by it — a search query, a fetched URL, a shell command,
  a file path, a message recipient.
- A **world-acting tool** is a tool that has at least one executed argument.
- A **world-acting request** is an outbound request that declares a world-acting tool whose
  executor sits on the **far side of the blindfold**.

The distinction that matters is not what a tool does but **which side of the blindfold its
executor sits on**. A client-executed tool receives *restored* values and acts on the user's
own data at the user's own intent, across a hop ADR-0002 never claimed. A provider-executed
tool receives *surrogates*, and acts on them as though they were real.

### 2. A world-acting request is identified structurally, not from a list

> A request is world-acting if **any declared tool lacks an `input_schema`**, or if
> **`mcp_servers` is present**.

A tool declared *with* an `input_schema` was defined by the client, so the client must
execute it — the provider has no implementation to run. This is a structural guarantee, not
a heuristic, and it holds even for programmatic tool calling, where a custom tool invoked
from inside provider-side code execution is still relayed back to the client to run.

A tool declared *without* one is provider-defined and may be provider-executed. The rule
deliberately does **not** try to distinguish within that class: the versioned type string
does not determine the executor (the bash, text-editor and memory tools are provider-defined
and client-executed; the computer tool may be either), and the provider's server-tool roster
is versioned and grows. Run 3 carried a search tool type that has since been superseded — a
hardcoded list would already have been stale on the day it was written.

This keeps ADR-0052's principle: a **closed syntactic class**, decidable in O(1), never an
open list maintained against a third party's release notes. It over-contains the handful of
provider-defined client-executed tools, which is the safe direction.

### 3. Containment is scoped to the request, not to an argument position

In a world-acting request, **every surrogate drawn from a plausible named pool — person and
org — is instead drawn from the reserved namespace** of ADR-0052 (`BFP0007`, `BFO0003`).
Dates, numbers and non-named entities are unaffected: they cannot summon a locatable human.

The unit is the request because the model, not Blindfold, authors the executed argument. The
model copies a name it was handed in ordinary prose several turns earlier, so a rule scoped
to an argument position bites nothing — on the measured leg that position is empty and the
executed string is user prose. Containment fires on **declaration**, never on `tool_choice`:
forcing is a client convention and no guarantee the model will not search unprompted.

The request-scoped surrogate is **additive and non-durable**. It does not replace, rewrite
or persist over the entity's stable surrogate, so no in-flight restore breaks and the store
gains no second **mapping** for the same referent. Nothing needs to look it up later, because
of §4.

### 4. A reserved-namespace surrogate is never restored

Restore does not reverse a reserved-form token, on any leg, ever.

Without this, containment does not remove the deception — it inverts it. The provider
searches an opaque token, finds nothing, and the model faithfully reports no results. The
client feeds that back into the main conversation where the mapping is still live, restore
rewrites it, and the user reads a confident **false negative about a real person**, arrived
at with exactly the invisibility of the original defect.

A marker cannot carry this. The statement the user reads is synthesized one leg later, in a
different request, after the client has dropped everything that is not payload bytes. The
non-restoring token is the only mechanism in play that survives that boundary, **because it
travels in the bytes**. The opaque token *is* the disclosure.

This amends ADR-0006: restore stays closed-world, and omitting an entry can never leak. It
costs legibility — the user sees an opaque token in their answer and must ask what it is —
and that cost is the point.

### 5. The invariant, restated

ADR-0052 established that a surrogate issued *without* a corpus-disjointness check is
reserved. This decision adds a second, deliberate path into the reserved namespace: a
surrogate issued *into a world-acting request*, whether or not it was checked. The reserved
form is therefore **no longer exclusively a pool-exhaustion signal**, and any code or
operator procedure that reads it as one must be re-checked.

The property the request path asserts is not "no surrogate reaches the client" but:

> **No surrogate drawn from a plausible named pool ever reaches the client, and no
> plausible named surrogate is ever emitted into a world-acting request.**

The exemption is decidable in O(1) by the reserved-form recognizer. Anything reaching the
client that is *not* of reserved form remains a failure, and the post-restore resolution
gate (ADR-0020) is unchanged.

### 6. What this predicts, and how it is verified

If this decision is correct, the deadlock class dissolves at its source: a contained request
searches an opaque token, returns nothing, contributes no novel real names, mints nothing
that can collide with the surrogate's components, and never blocks. **This is a prediction,
not a claim.** Its verification is a live re-run of the run-3 search scenario from a fresh
seed, not an argument. #386, #394 and #387 remain in place and their tests stay green; they
become defence in depth rather than the only thing between a user and a wedged session.

## Consequences

- The provider's coherent surrogate world fractures across a fan-out boundary: the model may
  see a plausible name in prose and an opaque token for the same referent in search results.
  This is the clause most likely to be misread as contradicting ADR-0005. It does not — the
  stable surrogate is untouched; a second, request-scoped one is added alongside it.
- Reasoning quality in world-acting requests degrades to what ADR-0052 already accepted for
  the exhaustion fallback. The measured cost profile is small: in run 3 a provider-executed
  tool was declared in **2 of 16** captures, both single-sentence fan-out requests. The main
  conversation declared 28 client-side tools and no provider-executed tool, so it is
  untouched.
- The user can no longer be silently told a false thing about their own entity, but can be
  told an opaque one. Making that legible is a management-app affordance, not a request-path
  concern, and is deliberately out of scope here.
- Search-on-a-real-name does not work through Blindfold and cannot be made to. The honest
  outcome — no results, under a token that shows why — replaces a fluent answer about a
  stranger. This is a real product loss and is accepted knowingly.
- `mcp_servers` is currently **never traversed by the blinder** (which visits only `system`,
  `messages` and `tools`) while the leak gate scans the whole serialized body. A real value
  in an MCP server URL or name is therefore gate-blocked and never substituted: a
  deterministic block with no escape. That is an ADR-0051 symmetry violation independent of
  this decision, surfaced by writing §2, and filed separately.
- Streaming restore registers buffers only for the client tool-call block type, so a streamed
  provider-side or MCP tool-call argument is never restored and fail-closes into a block.
  Filed separately; it is a gap in coverage, not a leak.
- The trust level of **tool-result content as a detection input** — consequence 3 of #397,
  where a world-acting tool's output seeded the workspace with third-party names — is *not*
  decided here. It is a larger question about provenance and mint eligibility, and folding it
  in would have made this record unreviewable.

## Alternatives considered

- **Reserved-namespace substitution at the executed argument position** (#397's option A as
  worded) — rejected as unimplementable at the position named. Blindfold does not author the
  argument; the model copies a surrogate handed to it in prose, and on the measured leg the
  argument position holds no surrogate at all. Adopted at the *request* granularity instead.
- **Refuse or block the call** (#397's option B) — rejected. A reserved-namespace value in a
  sending position already fails loudly against non-routable reserved forms (ADR-0005), so
  refusal adds a second failure path and covers nothing extra.
- **Warn the user and let the substitution stand** (#397's option C) — rejected as the whole
  answer, adopted as the effect. It addresses invisibility and nothing else: a stranger's
  name still reaches a public search engine and their profile still seeds the workspace. §4
  achieves the disclosure as a property of the bytes rather than as metadata the client will
  drop.
- **Draw plausible surrogates that name no locatable real person** (#397's option D) —
  rejected. ADR-0005 already rejected this reasoning for contactable PII in 2026-06, and it
  fails worse here: the pool is finite, the world is large, and the corpus-disjointness check
  cannot consult the live web.
- **Pass the real value through on a world-acting request**, on the grounds that the user
  asked to search for a real person and a search engine is a hop they chose — rejected
  flatly. It violates ADR-0002. It is recorded because it is the alternative a future reader
  will propose.
- **Identify provider-executed tools by their versioned type string** — rejected on evidence:
  the provider-defined bash, text-editor and memory tools carry versioned types and no
  `input_schema` yet are client-executed, and the computer tool may be either.
- **A maintained list of world-acting tool names or semantics** — rejected for #328's reason:
  an open class, unenforceable, and for a client-side or MCP tool the only inputs are a name
  and a description written by a third party. Whether an argument is executed is not knowable
  from them.
- **A fourth blinder/gate symmetry fix** — rejected; see Context.

## References

Issues: #397 (this decision), #372 (the live contract spike), #386, #394, #387 (the three
downstream fixes), #391 (detection precision against this corpus).
ADRs: ADR-0002 (blindfold every hop), ADR-0005 (surrogate generation — the plausible/reserved
split this extends), ADR-0006 (restore mechanics — amended by §4), ADR-0020 (egress gates),
ADR-0036 (component restore), ADR-0050 and ADR-0051 (gate scope and set symmetry),
ADR-0052 (the reserved namespace and its closed syntactic class), ADR-0057 (Claude Desktop
in 3P Gateway mode).
