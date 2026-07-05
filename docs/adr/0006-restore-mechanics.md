# ADR-0006: Restore mechanics — closed-world + verify pass + sliding-window + tool-call reassembly

**Status:** Accepted (verify-pass mechanics superseded by [ADR-0020](0020-verify-pass-split-into-egress-gates.md))
**Date:** 2026-06-17

## Context

Restore is the make-or-break path (ADR-0001). It must work on streamed responses and on
tool-call JSON, must not restore tokens that only coincidentally look like surrogates,
and must never let a real value leak or leave an injected surrogate unresolved.

## Decision

- **Closed-world restore:** restore only surrogates actually injected for *this*
  exchange — never a coincidentally-emitted lookalike.
- **Verify pass:** after restore, assert no real value leaked and no injected surrogate
  was left unresolved; warn on failure. Split into a pre-egress leak gate + post-restore
  resolution gate by [ADR-0020](0020-verify-pass-split-into-egress-gates.md) — the leak
  check moved before egress instead of running only after the fact.
- **Sliding-window streaming restore:** emit the safe prefix while holding back a tail
  buffer at least as long as the longest known surrogate, so surrogates split across
  stream chunks are matched before emitting.
- **Tool-call JSON reassembly:** fully reassemble streamed tool-call JSON before
  restoring inside its string values, preserving escaping; surrogates used in code must
  be valid-looking identifiers.

## Consequences

- These properties are the contract enforced everywhere by the `leak-audit` skill.
- Streaming gains a bounded tail latency (≥ longest surrogate) — acceptable for UX.
- Sub-token over-restoration is the key risk; closed-world + careful maps + verify pass
  mitigate it.

## Alternatives considered

- **Open-world global replace** — rejected: restores coincidental lookalikes (e.g. a
  provider-emitted "Martin").
- **Buffer the whole stream before restoring** — rejected: destroys the streaming UX.

_Migrated from DESIGN.md decision log rows 5 and 12._
