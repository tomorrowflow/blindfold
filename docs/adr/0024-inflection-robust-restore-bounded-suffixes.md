# ADR-0024: Inflection-robust restore — word-boundary matching + bounded German suffix set

**Status:** Accepted
**Date:** 2026-07-08

**Numbering note:** issue #75 was scoped against "ADR-0023" before the L3-suppression
design pass (issue #59) claimed that number first (merged same day, commit `cd7da6d`).
This decision record is the same content the issue describes, filed as ADR-0024 to
avoid colliding with the accepted `0023-l3-suppression-token-granularity.md`. The
CONTEXT.md glossary entries this issue depends on (**Closed-world restore**'s
suffix-aware wording) were already sharpened by that same commit, so no further
CONTEXT.md change is needed here.

## Context

Restore (`_restore_text`, `engine.py`) is plain substring replacement:
`result.replace(surrogate, real)`. That cuts both ways:

- It accidentally handles the German genitive: surrogate `"Weber"` inside the
  provider's echo `"Webers"` restores to `real + "s"` — a quality property nobody
  designed but that real German traffic depends on.
- It also **over-restores sub-tokens** (DESIGN.md Top Risk #2, live behavior):
  surrogate `"Weber"` inside the unrelated common noun `"Weberei"` becomes
  `real + "ei"` — a coincidental substring match, not a reference to the surrogate
  at all. Closed-world restore (ADR-0006) is supposed to guard against restoring
  things that were never actually injected; a sub-token hit slips past that guard
  because plain substring search doesn't know where word boundaries are.

## Decision

Restore matches an injected surrogate **at a word boundary**, optionally followed by
**one suffix from a small closed set of German morphological suffixes**, and
transfers that suffix onto the real value. Nothing else restores.

The closed suffix set (a reviewed list — growing it is a code change with tests, not
a runtime tuning knob):

```
s, n, en, 's, '
```

Implementation: a single compiled-per-surrogate regex, `\b<surrogate>(?:suffix-alt)?\b`,
shared by every restore path via `_restore_text`. `\b` on both ends is what kills the
sub-token bug for free — `"Weber"` immediately followed by `"ei"` in `"Weberei"` has no
suffix in the closed set that swallows `"ei"`, and even with zero-width suffix
consumption the position between `"r"` and `"e"` is not a boundary, so the whole
pattern fails to match at that position. The surrogate is left untouched, not
half-restored.

Scope: **all three restore paths** — non-streaming response restore, sliding-window
streaming restore, and tool-call JSON restore (`restore_response`,
`StreamingRestorer`, `restore_tool_call_json`) — all route through `_restore_text`, so
one regex-based implementation covers all three. `StreamingRestorer`'s tail buffer
grows by the longest suffix's length (2 chars, `"en"`) on top of the longest injected
surrogate, so a suffix split across a stream chunk boundary is not mistaken for "no
suffix" before the rest of it has arrived.

`resolution_gate` (the post-restore detection gate, ADR-0020/SEC-6) is updated to the
same word-boundary check, so it does not fail-close on a benign sub-token containment
(`"Weberei"` still containing the literal characters `"Weber"`) that restore correctly
left alone. It stays free to be *stricter* than the restorer in general (catching a
genuinely unresolved surrogate is its whole job) — it just must not be strict on a
string that was never actually a restore target.

The **blindfold**-side of German inflection (an inflected real form appearing in
outbound traffic) is explicitly **not** this decision's scope: an inflected real form
is a **Variation** of its entity, resolved by the existing entity-graph / coreference
machinery, not by the restore path.

## Consequences

- Restore keeps the accidental-but-useful genitive/plural handling, now as a
  deliberate, bounded feature instead of an accident of substring search.
- Sub-token over-restoration (DESIGN.md Top Risk #2) is closed for the restore path.
- The suffix set is exact and pinned by tests; extending it (e.g. genitive apostrophe
  variants, other inflections) is a deliberate ADR-visible change, not silent drift.
- `StreamingRestorer`'s held-back tail grows by a small constant (2 chars) — a minor,
  bounded latency cost for streaming, not proportional to payload size.
- Multi-word surrogates (e.g. `"Berta Vogel"`) only take a suffix at their end, matching
  German surname-genitive placement (`"Berta Vogels"`); mid-string inflection of a
  first name inside a full-name surrogate is out of scope.

## Alternatives considered

- **Fuzzy/edit-distance matching** — rejected: unbounded false-positive surface,
  directly reopens the sub-token over-restoration this ADR exists to close.
- **A seek/stem model for German morphology** — rejected: heavyweight for a bounded,
  closed problem; the suffix set is small and enumerable.
- **Leaving plain substring replacement as-is** — rejected: it's the live Top Risk #2
  behavior; "restores too much" is not an acceptable trade against "sometimes doesn't
  handle the genitive."
- **Handling inflection only via entity-graph Variations, no restore-side change** —
  rejected as the sole fix: Variations are a **blindfold**-side (outbound) concept: a
  real inflected form the user types. They don't address a *surrogate* the provider
  echoes back with a German suffix appended, which only restore ever sees.
