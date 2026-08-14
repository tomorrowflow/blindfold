# ADR-0049: The GLiNER cascade is the default detection path, provisioned at Setup

**Status:** Accepted
**Date:** 2026-08-14

## Context

ADR-0033 §2 introduced the GLiNER cascade and ADR-0034 §1/§2 made it **opt-in**: activated
by `BLINDFOLD_L3_PROVIDER=gliner` or by a persisted flag the Settings → Detection view sets
(issue #147). `DEFAULT_L3_PROVIDER` is `"ollama"` — the bare LLM tier. Nothing in first-run
Setup turns the cascade on.

Both ADRs were written before anyone measured what the two paths actually detect. Measured
2026-08-14 on local hardware against the shipped model (`gliner-pii-base-v1.0`) and a local
oMLX inner adjudicator, sampling pinned (ADR-0048 / issue #259), 24 hand-labelled synthetic
spans:

| detection path | recall | precision |
| --- | --- | --- |
| bare LLM, `gemma-4-e2b-it-4bit` (solo) | **21%** (3/14) | 100% |
| bare LLM, `gemma-4-e2b-it-nvfp4` (solo) | 36% | 100% |
| bare LLM, `gemma-4-e4b-it-nvfp4` (solo) | 86% | 100% |
| **GLiNER cascade** → any of the three | **93%** (13/14) | 93% |

The cascade number is *identical* across all three inner models, because GLiNER decides 16
of 24 candidates — including 13 of the 14 entities — and rescues whatever the inner model
misses. On the small models the bare LLM misses every person name, every German surname,
the private org, and a codename whose sentence says it is a codename.

Three configurations exist, and only one of them is dangerous:

1. **No LLM configured** — `_build_inner_l3_adjudicator` returns `_UnconfiguredAdjudicator`,
   every candidate raises, and the mint pass turns that into a scrubbed 503 (ADR-0009). The
   install fails closed and the operator is told.
2. **LLM configured, GLiNER on** — the 93% path.
3. **LLM configured, GLiNER off** — the 21–36% path. It reports healthy. It reaches
   `state: protected`. Nothing distinguishes it, from the outside, from configuration 2.

Configuration 3 is the current default for anyone who follows the setup path far enough to
work at all, and it is the only one of the three whose failure mode is invisible. A miss is
a privacy bug; a fail-closed 503 is not. Today the product's default lands users in the one
configuration that produces the former silently.

## Decision

**The GLiNER cascade is the default detection path. First-run Setup provisions the model
and the "Enhanced local detection" opt-in defaults to on.**

Corollaries:

1. **`DEFAULT_L3_PROVIDER` alone does not change.** Flipping the constant without
   provisioning would send every fresh install into `_UnconfiguredAdjudicator`
   (`app.py:241-243`) and 503 on any hop containing a candidate. Provisioning is the
   load-bearing half of this decision; the constant is a consequence of it.
2. **The cascade default changes nothing for an install with no LLM configured.** GLiNER
   negatives must still escalate to the LLM (ADR-0033 §2's fail-closed rule is unchanged),
   so configuration 1 keeps failing closed exactly as it does now. This decision moves
   configuration 3 to configuration 2, and touches nothing else.
3. **Detection quality must never depend on ambient disk state.** See the rejected
   auto-detect option below.
4. **The air-gapped path is unchanged**: `BLINDFOLD_L3_GLINER_MODEL_PATH` (ADR-0034 §4)
   remains the escape hatch for installs that cannot download at Setup time.

## Considered options

**Flip `DEFAULT_L3_PROVIDER` to `"gliner"` and nothing else.** Rejected: fresh installs 503
until someone visits Settings → Detection. Fails closed rather than dangerously, but it
makes the product unusable out of the box.

**Default to the cascade when a provisioned model is present, else the bare LLM.** The
tempting option, and the one most readers will reach for first — no new Setup step, no
download to explain, degrades gracefully. Rejected, and it is worth being explicit about
why: it makes what gets protected a function of **ambient disk state**. Two installs with
byte-identical configuration would protect the same hop differently depending on whether a
download happened to have completed, which is the same class of defect as the cache-state
coupling `CONTEXT.md`'s **Detection is reproducible** invariant forbids (issue #261 /
ADR-0048 corollary 2). It also converts a failed provisioning into a silent 4× recall
regression — the exact invisible failure this ADR exists to remove — where the chosen option
converts it into a visible Setup error.

**Leave it opt-in and improve the discoverability of Settings → Detection.** Rejected: the
measured gap is 21% versus 93% recall. A default that costs three-quarters of detection
recall is not a discoverability problem, and "the user could have turned it on" is not a
defensible answer to a miss.

## Consequences

- **GLiNER's precision becomes the default precision ceiling.** ADR-0033 §2 short-circuits a
  GLiNER positive with no LLM veto, so the cascade's precision cannot exceed the classifier's
  (measured 93% German / 80% English). Over-redaction that reached only opt-in users now
  reaches everyone. Issue #280 tracks this; its cheap half — auditing `seeded_allowlist.txt`
  against what GLiNER labels `organization` for public-software tokens — is sequenced
  **before** the default flip, and its structural half (whether Position A should survive at
  all) is not a blocker. The asymmetry is what makes that sequencing safe: the flip's
  downside is over-redaction, which lands in the review inbox where a human can see and clear
  it, while the status quo's downside is a miss, which nobody can see.
- **Setup grows a ~197MB download** with the progress, failure and resume affordances that
  implies. This is the real cost of the decision and most of the implementation work.
- **ADR-0034 §1/§2's opt-in framing is superseded** on the question of the *default*. Its
  activation mechanism — the persisted flag, env precedence, store-gating — is unchanged and
  still correct; only the default value of the opt-in moves.
- **ADR-0033 §2's "a GLiNER false positive is over-redaction — a quality bug, not a privacy
  bug — and is safe to accept" no longer stands unqualified.** `CONTEXT.md`'s Key invariants
  record that over-redaction is not free. The Update blocks added under issue #140 already
  note this; #280 carries the decision.
- **The bare-LLM path is not removed.** `BLINDFOLD_L3_PROVIDER=ollama|omlx` stays supported
  for operators who want it, and remains the only option on hardware that cannot run the
  ONNX classifier. It is now a deliberate downgrade rather than the path of least resistance.
- **Inner-model choice stops being urgent.** The 21%-versus-86% spread between `e2b` and
  `e4b` collapses to nothing once the cascade is in front, so no model recommendation is
  made here. Ranking inner models needs a corpus built from GLiNER-negatives, which is
  #258's territory; the sample behind this ADR had exactly one such positive and every
  configuration missed it.
