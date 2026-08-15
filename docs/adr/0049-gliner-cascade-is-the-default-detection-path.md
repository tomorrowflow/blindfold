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
  - **Update (issue #281): the cheap half is done.** A probe corpus of public-software/
    framework/product tokens in coding-agent-shaped sentence context was run through the
    real `GlinerOnnxClassifier.classify_span` (`gliner-pii-base-v1.0`, 2026-08-15).
    `Transit` — this ADR's own cited live finding — plus ten other confirmed, unambiguous
    public-brand false positives (`Docker`, `Nginx`, `Datadog`, `Figma`, `Netlify`,
    `Heroku`, `Cloudflare`, `Bitbucket`, `Firefox`, `Kubernetes`) are now seeded. Eleven
    more confirmed false positives were measured and deliberately left unseeded because
    the bare token collides with a real surname, place name, or generic dictionary word
    (`Vault`, `Kafka`, `Jenkins`, `Confluence`, `Notion`, `Zoom`, `Stripe`, `Scala`,
    `Chrome`, `Vercel`, `Teams`) — seeding is permanent novelty-discovery loss for that
    literal token in every future context, not just the measured sentence. See
    `tests/fixtures/gliner_org_probe_corpus.json` and `tests/test_gliner_org_seed_audit.py`
    for the full measurement and the per-token rationale. #280's structural question
    (whether Position A should survive at all) is untouched by this update.
  - **Update (issue #281, maintainer re-measurement): two labels corrected.** A trusted
    maintainer re-ran all 39 probes on real GLiNER-provisioned hardware — the agent
    sandbox that originally authored the fixture has neither the `gliner` extra nor a
    provisioned model, so it could not have measured them itself. Result: 37/39 confirmed
    as recorded; `Kubernetes` and `Teams` were both wrongly recorded `none` (no false
    positive) when the real classifier flags both `organization`, deterministic across 3
    runs. `Kubernetes` — a purpose-coined public brand, no dictionary-word or surname
    collision — is now seeded on the same grounds as `Docker`/`Nginx`. `Teams` — an
    ordinary English plural noun that is also a product, independently corroborated
    flagging on real traffic in issue #74's live-verify run 3 — is recorded `organization`
    but stays unseeded per the corpus's own curation rule (same class as `Vault`/
    `Confluence`). See `tests/fixtures/gliner_org_probe_corpus.json`'s
    `_provenance.measurement_authenticity` for the full re-measurement record.
  - **`Transit`'s seeding is a maintainer instruction, not an agent judgment call — recorded
    here because a review cycle blocked on it.** `Transit` was rejected once already
    (`_CURATION_REJECTS`, issue #87, 2026-07-10) as bare generic prose that could plausibly
    name a deployment's own secret. Issue #281's own **Acceptance Criteria** name the
    supersession explicitly, by literal token: *"Confirmed public-token false positives are
    added to `seeded_allowlist.txt`, `Transit` among them."* That sentence is the human
    ratification of the reversal — the maintainer who filed #281 wrote the token into the
    issue's acceptance criteria themselves, rather than leaving its seeding to whatever a
    probe measurement turned up. The distinction matters because the general curation rule
    (public/coined identifier, no surname or dictionary-word collision) would not by itself
    settle `Transit` — it is an ordinary English word — and #281 resolves that tension with
    product-specific context (`Transit` is OpenBao's/Vault's own named key-wrapping engine,
    not a generic noun in this project's traffic) that only the issue author could supply.
- **Setup's provisioning flow already exists and is smaller work than it looks.** `Setup.tsx`
  (issue #146) already renders the "Enhanced local detection" checkbox with the ~197MB help
  text, calls `POST …/gliner-provision`, handles failure, and shows a restart screen on
  success. What this ADR changes is the checkbox's **default value** and
  `DEFAULT_L3_PROVIDER` — not the download machinery.

- **A failed download must become visible, narrowing ADR-0034 §5.** That section made
  provisioning failure deliberately non-blocking, and `Setup.tsx` implements it by falling
  through "exactly as if the toggle had been left unticked". That is benign for a default-off
  toggle and is the silent 4× recall regression this ADR exists to prevent once the toggle is
  default-on. **Non-blocking is retained — the operator always reaches their workspace — but
  the reduced-detection state must be visibly and persistently surfaced, with a retry.**
  Blocking Setup on a network failure is rejected: it would strand air-gapped installs that
  intend to use the `BLINDFOLD_L3_GLINER_MODEL_PATH` hatch.

- **Activation still requires a process restart, and that is accepted for now.** The persisted
  flag takes effect on the *next* start (ADR-0034 §1, "no mutable runtime config"), so a
  default-on install runs the bare LLM until it restarts, and Setup's last step becomes a
  restart instruction for every new install. Making the flag hot-swappable is **rejected**:
  ADR-0034 §1's startup-resolved rule is what keeps "which detector is running" answerable
  from configuration alone, and mutable mid-process detection config would reintroduce the
  request-history dependence `CONTEXT.md`'s **Detection is reproducible** invariant forbids.
  The better fix is for the **supervisor** to offer the restart as a button rather than an
  instruction — it already owns the proxy lifecycle (ADR-0044) and has the primitives
  (`ProxyProcessKit.launch`/`kill`, `ProxySupervisor.Start`), though not a restart command.
  That is deliberately **not** a prerequisite: the comparison is one restart at 93% recall
  against no restart at 21%, and gating the recall win on native-shell work in two languages
  — one of which has no sandbox verification path — would let polish gate substance. Note
  ADR-0041's "no auto-restart after a crash" is untouched; a user-initiated restart is a
  different thing.
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
