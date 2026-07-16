# ADR-0032: L3 dismissal log — opt-in local capture to curate the seeded allowlist

**Status:** Proposed
**Date:** 2026-07-16

## Context

ADR-0023 introduced the **seeded allowlist**: a curated, evidence-first data file
(`seeded_allowlist.txt`) that suppresses novelty discovery for public
framework/vendor/tool tokens which flood L3 in coding-agent traffic. Its curation
rule is deliberately conservative — a token qualifies only if it is "implausible as a
protected referent when unregistered" — and ADR-0023 kept the seed small on purpose,
since every seeded token is a permanent novelty-discovery blind spot until v2
provenance lands.

That leaves a gap in the evidence loop. Two populations of L3 candidate spans have a
place to go today:

- **Confirmed candidates** (`is_entity: true`) flow to the review inbox (ADR-0010),
  where a human confirms or rejects them, feeding the learned allowlist.
- **Suppressed tokens** never reach the adjudicator at all (ADR-0023).

But **dismissed candidates** — spans `L3Detector.detect()` adjudicated
`is_entity: false` — have nowhere to go. They are, by definition, exactly the
population the seeded-allowlist curation rule targets: tokens L3 saw, weighed, and
judged not to be protected referents. A curator building the next iteration of
`seeded_allowlist.txt` currently has to guess at this population from live-verify
transcripts rather than read it directly. The same word can be dismissed hundreds of
times across one system prompt, so any capture must dedup to be usable.

Crucially, a dismissed token is *not* protected data: L3 judged it a non-entity, so
it already egresses to the provider un-blindfolded on the request path exactly as it
does today. Capturing it locally introduces no new provider egress and no new
mapping — it is not the real-value side of any surrogate mapping, so ADR-0008's
plaintext-storage prohibition (Transit ciphertext + blind index) does not apply. If
anything the log is privacy-positive: a curator reading it can catch an L3 false
negative (a real referent wrongly dismissed) that is already leaking today.

## Decision

We will add `BLINDFOLD_L3_DISMISSAL_LOG` — a file path; empty/unset is the default
and means off, preserving today's exact behavior with no file created or written.

When set, `L3Detector` appends a dismissed candidate's **bare token text** to the file
the first time that exact token is dismissed in the process's lifetime:

- **Token text only, never `candidate.context`.** The ADR-0023 curation rule is a
  property of the word itself, not the sentence it appeared in. Every context window
  not captured is real prose that never touches disk — the conservative choice.
- **Confirmed candidates (`is_entity: true`) are never written.** They already flow
  to the review inbox unchanged; the log covers only the dismissed population that has
  nowhere to go today.
- **Dedup via a small in-process `set[str]`**, deliberately separate from
  `L3ContentCache`'s `(text, context)`-keyed cache: the same token dismissed 200
  times across one system prompt writes exactly one line, not 200.
- **Append-on-first-sight** (open-append immediately, not buffered until shutdown) so
  a `serve` process killed rather than cleanly stopped does not lose the session's
  dismissal data.
- **Format matches `seeded_allowlist.txt`** (one token per line) so a curator reviews
  the log and hand-appends qualifying lines directly.

This is purely additive: no change to `L3ContentCache`, `select_candidate_spans`, the
review inbox, or the request/restore path. `detect()`'s return value is byte-identical
with and without the log; the write is a side-effect only.

v1 curation is manual by design — a human reads the log and hand-edits
`seeded_allowlist.txt`, the same evidence-first method issues #71/#87 used.

## Consequences

- Curators get direct evidence of the dismissed-token population instead of inferring
  it from transcripts, tightening the ADR-0023 seed loop.
- The dedup set grows with the process's distinct-dismissed-token vocabulary
  (unbounded in principle, small in practice); it is per-process and not persisted.
- No leak-audit surface changes: the log captures only `is_entity: false` tokens that
  already egress un-blindfolded, never `candidate.context`, never a confirmed entity,
  and only to a local file the operator opts into. The pre-egress leak gate, restore,
  closed-world restore, and fail-closed paths are untouched.
- **Deferred:** a settings-app roundtrip / UI for reviewing the log and promoting
  entries (tracked as a follow-up issue); allowlist provenance (`seeded` vs
  `learned`) still deferred per ADR-0023.

## Alternatives considered

- **Log the `(text, context)` pair** — rejected: the curation rule is token-scoped,
  and writing context windows would put real surrounding prose on disk for no curation
  benefit.
- **Reuse the review inbox for dismissals** — rejected: the inbox is the
  confirm/reject learning loop for *provisional* candidates (ADR-0010); dismissals are
  already-decided negatives and would pollute it. Their destination is the seeded
  allowlist, a different mechanism.
- **Dedup on the content cache's `(text, context)` key** — rejected: that writes one
  line per distinct context window, reinstating exactly the flood this log exists to
  make reviewable. Dedup must be per-token.
- **Buffer writes until shutdown** — rejected: a killed `serve` process would lose the
  session's evidence; append-on-first-sight is cheap and durable.
- **On by default** — rejected: writing to disk is an operator choice; unset must
  reproduce today's behavior exactly.
