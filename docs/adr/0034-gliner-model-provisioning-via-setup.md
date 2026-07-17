# ADR-0034: GLiNER model provisioning — interactive Setup opt-in, restart to activate

**Status:** Proposed
**Date:** 2026-07-17

## Context

ADR-0033 §2 wired the **GLiNER cascade adjudicator** behind a config knob
(`BLINDFOLD_L3_PROVIDER=gliner` + `BLINDFOLD_L3_GLINER_MODEL_PATH`, issue #139) and
made its measurement the precondition for the deferred `wordfreq` lever (§3). But
#139 shipped the *wiring* only — the config knob, health probe, and startup guard —
not the ability to run. Live testing (2026-07-17) surfaced the gap:

- No GLiNER ONNX model exists on disk, and nothing provisions one — the Seed bundle
  (ADR-0029) is entity-graph data only, and Setup (ADR-0029/0030) offers no model step.
- The `gliner` package and `onnxruntime` are neither declared in `pyproject.toml` nor
  installed; `l3_gliner.py`'s deferred `from gliner import GLiNER` would `ImportError`
  the moment the cascade activates.
- Consequently `BLINDFOLD_L3_PROVIDER=gliner` fail-closes at startup, so ADR-0033's
  "measure Mikheev **+ GLiNER**, then decide on wordfreq" has an unmet precondition.

Meanwhile the running instance (oMLX / `gemma-4-e2b-it-4bit` adjudicator, no GLiNER)
floods the **review inbox** with common English words capitalized by heading/bullet
position (`Title`, `Recalled`, `Primary`, `Contents`, `Merge`, …) — 24/24 inbox
items in one session were false positives. The Mikheev **positional case heuristic**
(ADR-0033 §1) cannot catch single-occurrence heading words (its condition (a) needs a
lowercase twin in the same hop), and GLiNER — the layer meant to backstop this — is
unprovisioned.

Two facts constrain the shape hard:

1. **Config is env-only and resolved once at startup.** `get_settings()` reads
   `os.environ`; the fail-closed local-only guards (`serve.py`) run at startup; the
   L3 adjudicator is built once (`app.py`). There is no persisted settings store and
   no runtime reconfiguration API.
2. **The default store is in-memory and ephemeral.** With `BLINDFOLD_DATABASE_URL`
   unset, workspaces/entities/RBAC/mapping are module-level singletons lost on
   restart. Blindfold has no install-global on-disk **data directory** today.

## Decision

We will make GLiNER model provisioning an **interactive, opt-in Setup step**, and
enable the cascade by **restart** rather than live reconfiguration.

### 1. Download in Setup, persist a flag, activate on restart

An opt-in toggle (**"Enhanced local detection"**, off by default) in Setup downloads
the model and persists an activation **Setting** in the **store**. `get_settings()`
gains a persisted-overlay-on-env source read **at startup**; the next start reads the
flag and activates the cascade. The UI shows *"Restart Blindfold to activate enhanced
detection."* The startup-only, fail-closed config model is preserved unchanged — no
mutable runtime config, no dynamically-run local-only guards.

### 2. Gated on a persistent store; in-memory is dev/demo

The interactive option is offered **only when a persistent store is configured**
(Postgres today). On the ephemeral in-memory default, restart-to-activate is
incoherent — it would wipe the just-created workspace — so GLiNER stays env-only
there. Real Setup (ADR-0029/0030: name a workspace, import company entities) already
presupposes persistence; the in-memory default is a dev/demo mode.

### 3. Model lives in a new install-global data directory

We introduce Blindfold's first **data directory** (see CONTEXT.md), rooted at
`BLINDFOLD_DATA_DIR`, defaulting to the OS app-data convention
(`~/Library/Application Support/blindfold/` on macOS, `$XDG_DATA_HOME/blindfold/` on
Linux). The model lands at `<data_dir>/models/gliner-pii-edge-v1.0/`. The existing
`BLINDFOLD_L3_GLINER_MODEL_PATH` remains the low-level override / air-gapped escape
hatch; Setup computes the default path under the data directory.

### 4. Pinned source, integrity-verified, refuse on mismatch

The model is `knowledgator/gliner-pii-edge-v1.0` (UINT8 ONNX), fetched via
`huggingface_hub.snapshot_download` pinned to a **specific repo revision**, with
expected file digests verified before the model is considered usable. A model that
fails verification is **refused, not activated** — we do not run an unpinned or
tampered model on the privacy-critical detection path. The download is the only
outbound network call and is triggered solely by the opt-in; its help text names the
source and size (~197 MB).

### 5. Offline detection; non-blocking, retryable provisioning

If a model already exists at the data-dir path (or `BLINDFOLD_L3_GLINER_MODEL_PATH`),
Setup detects it and skips the download ("already provisioned"); air-gapped operators
place the files manually. A download failure never blocks completing Setup (as with
Sample data, ADR-0030). Because the model is **install-global, not per-workspace**,
retry lives on a new **detection/settings** management view, not the entity list.

### 6. Optional `gliner` dependency extra

`gliner` + `onnxruntime` ship as an optional extra (`blindfold[gliner]`), not a base
dependency — the 197 MB model and ONNX runtime are opt-in weight. A missing extra
produces a clear actionable error at provision/activation time, never a raw
`ImportError`.

## Consequences

- ADR-0033's measurement precondition is met: GLiNER becomes runnable, so residual
  English false-positive volume can be measured and the `wordfreq` lever (§3)
  re-evaluated on evidence.
- Blindfold gains a persisted-config source and an install-global data directory —
  both reusable beyond GLiNER (future model assets, caches).
- Restart-to-activate is a deliberate cost accepted to keep config startup-resolved
  and fail-closed guards static; live reconfiguration was rejected (below).
- The feature is unavailable on the in-memory default, which exposes a **separate
  pre-existing gap**: first-run Setup's persistence promise is misleading on the
  ephemeral default store. Tracked separately; not fixed here.
- `wordfreq` frequency scoring remains deferred (ADR-0033 §3), now genuinely gated on
  the GLiNER-on measurement this ADR unblocks.

## Alternatives considered

- **Live reconfiguration** (persisted settings + management API rebuilding the L3
  adjudicator in-process, no restart) — rejected: makes privacy-critical config
  mutable at runtime and forces the startup fail-closed guards to run dynamically, a
  large security-surface change for a first-run convenience.
- **Make on-disk the default store** so restart preserves everything — rejected as
  out of scope: a foundational persistence change beyond this feature.
- **Bundle the 197 MB model in the package** — rejected: heavy wheel; the model is
  opt-in weight most installs won't use.
- **Setup asks the operator for a model path** (no data dir) — rejected: pushes
  filesystem decisions onto first-run; poor UX.
- **Unpinned / latest-revision download** — rejected: running an unverified model on
  the detection path is unacceptable for a privacy tool.
- **Full first-run Setup integration on the in-memory path** — rejected: restart
  would wipe the store; the flow is only coherent with persistence.
