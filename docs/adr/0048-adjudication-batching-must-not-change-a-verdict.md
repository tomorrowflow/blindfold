# ADR-0048: Adjudication batching may amortise cost, but must never change a verdict

**Status:** Accepted
**Date:** 2026-08-12

## Context

Issue #142/#157 introduced batched **L3** adjudication: N **candidate spans** are sent as one
numbered list in a single call, instead of one call each. It was adopted as pure round-trip
amortisation — `_BATCH_PROMPT_TEMPLATE` explicitly instructs the model to "adjudicate each
numbered candidate independently, using only that candidate's own context — do not let one
candidate's verdict influence another's."

It does not get that. Measured 2026-08-12 against local oMLX (`gemma-4-e2b-it-4bit`) using the
production templates, with sampling **fully pinned** (`temperature: 0`, issue #259), one
candidate — `Helvetia` in "The Helvetia rollout slipped by two weeks.", a secret-project name
and exactly the class L3 exists to catch:

| request shape | verdict |
| --- | --- |
| solo prompt (`_PROMPT_TEMPLATE`) | **True** |
| batch of 1 (`_build_batch_prompt`) | False |
| batch of 2–5, target **first** | False |
| batch of 2–5, target **last** | **True** |
| batch of 6, target last | False |

The verdict moves with the template, with the candidate's **position** in the batch, and with
the batch's **size**. Over a 20-candidate run, batched and solo disagreed on 2 — both in the
**miss** direction.

This collides with the domain model. `CONTEXT.md` defines a **Candidate span** as "a flagged
span … handed to L3, plus minimal context": the unit of adjudication is one span with its own
context. The measurement says a span's batch-mates are also context. One of the two had to
give.

It also matters more than a quality wobble, because in the shipped path batch membership is
not a function of the input at all. `L3Detector.detect` batches only the candidates that miss
the process-wide `L3ContentCache`, so composition — and therefore position and size — is a
function of **request history**. The same hop text is protected differently by a warm process
and a cold one.

## Decision

**Batching is an optimisation and must remain semantically invisible. A candidate span's
verdict must not depend on whether, with what, or where it was batched.** The solo
adjudication of a span is the reference answer; any batching scheme that disagrees with it is
defective, not merely different.

Corollaries:

1. **The batch is not a domain concept.** No `Adjudication batch` term enters `CONTEXT.md`.
   Naming it would legitimise the coupling and make "which batch was it in?" a legitimate
   question to ask about a privacy outcome. It is not one.
2. **Batch composition must be a pure function of the hop's inputs**, never of cache state or
   process age (issue #261). `select_candidate_spans` is already pure in
   `(text, known_entities, allowlist, declared_tools)` and yields candidates in document
   order; only the grouping in `L3Detector.detect` breaks that, and it is what changes.
3. **Whether a conforming batching scheme exists at all is still open** (#260). This ADR fixes
   the *rule*, not the mechanism. If no batch prompt can be made to agree with solo
   adjudication, the resolution is to stop batching — not to redefine the verdict.

## Considered options

**Accept the batch as the unit of adjudication** — declare batched verdicts authoritative,
name the batch in the glossary, and drop the reproducibility ambition for L3. Rejected: it
makes a privacy outcome depend on request history by design, permanently blocks #258's corpus
from gating L3, and answers #249's invariant question with "no".

**Drop batching and recover the latency with concurrency across the seam** — the obvious fix,
and the reason this ADR records a measurement rather than just a rule. It does not work on the
primary provider. Over 20 candidates:

| shape | time | vs solo sequential |
| --- | --- | --- |
| solo sequential | 8.6s (0.43s/candidate) | — |
| batched (4×5, shipped) | **3.4s (0.17s/candidate)** | **2.49×** |
| solo concurrent, k=5 | 7.2s | 1.19× |

oMLX effectively **serialises** concurrent requests, so parallelism buys 1.19× where batching
buys 2.49×. Concurrency is verdict-safe — solo-concurrent agreed with solo-sequential 20/20,
confirming the coupling is the batch *prompt* and not parallelism — but it is not a
substitute. **This figure is provider-specific**: a future provider (or an oMLX that grows a
concurrent scheduler) must re-measure rather than inherit it, exactly as ADR-0031 requires a
new provider to re-derive its own local-only story.

## Consequences

- The 2.49× stands for now; nothing here removes batching. It is kept under an explicit
  correctness constraint rather than as a free win.
- Every future adjudicator optimisation — speculative decoding, prefix caching, cross-hop
  batching — inherits this rule and owes the same solo-versus-optimised comparison.
- #249's reproducibility invariant cannot be written into `CONTEXT.md` until #259 (pinned
  sampling) and #261 (input-determined grouping) have both landed; until then the honest
  predicate would have to include "and the same process history".
- #58's latency budget is downstream of #260's outcome: if no conforming batch scheme exists,
  the per-candidate cost roughly doubles on this provider.
