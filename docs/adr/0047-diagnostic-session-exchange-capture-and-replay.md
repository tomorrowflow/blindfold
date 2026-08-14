# ADR-0047: Diagnostic session — source-only exchange capture and replay

**Status:** Accepted
**Date:** 2026-08-11

## Context

Blindfold's observability surfaces are scrubbed by design and that design is correct.
ADR-0035 states the **Processing trace** carries "stage outcomes/counts/timings and
surrogate/hashed references only — never a real value, raw hop content, candidate-span
text, or a payload diff". An **Audit event** records real-space crossings and refusals,
not content. The **Review inbox** holds **mapping cipher** ciphertext. The **Supervisor
log** (ADR-0046) is process lifecycle only.

The consequence is deliberate and total: **nobody — including an `admin` — can answer
"what did the provider actually see, and was my entity protected"**. The operator takes
the product's core claim on faith, and faith is not verification. The one failure that is
observable today is a **Restore** miss, and only because a **surrogate** appears in the
client's output by eye.

Three facts about the codebase shaped what is possible:

- `DetectedSpan` (`detection.py`) already carries `start`, `end`, `text`, `real`,
  `surrogate`, `pass_name` — but `_HopContext` (`engine.py`) folds spans into counters and
  `_finish_hop()` freezes an already-scrubbed `HopDetail`. **Span detail is destroyed, not
  hidden.** No seam can recover it, and that is the privacy property holding, not an
  oversight.
- `ExchangeSession.injected` is a plaintext `surrogate -> real` dict, because
  **closed-world restore** requires it, and `blindfold_payload()` returns the session. The
  authoritative pair table survives.
- `app.py` is built on FastAPI dependency providers with `app.dependency_overrides` as the
  established substitution mechanism, already load-bearing for the test suite.

And one about the release artifact: the shipped proxy is a **PyInstaller onefile binary**
(`packaging/blindfold-proxy.spec`, ADR-0039), whose `Analysis` walks imports from a single
entry point — so unreachable code is absent by construction.

## Decision

We will add a **Diagnostic session** — a source-run-only capability, physically absent from
every shipped artifact, that captures and replays exchanges so blindfold and restore can be
validated.

### 1. Source-run only; there is no diagnostic artifact

A Diagnostic session is `blindfold serve` run from a source checkout with the devtools
extra installed, on the loopback port the client already targets. There is **no second
freeze target and no diagnostic `.app`**: an artifact that can be built is one that can be
handed to someone else by mistake.

### 2. `blindfold_devtools` is a sibling top-level package

It lives at `src/blindfold_devtools/` and is **never imported by any `blindfold.*` module**,
so it is unreachable from `packaging/blindfold_proxy_entry.py` and PyInstaller never bundles
it. It is *not* `src/blindfold/devtools/`: `datas=collect_data_files("blindfold")` sweeps
every non-`.py` file under the `blindfold` package directory, so a devtools fixture would
ride into the release binary even with the module excluded. A sibling package is confirmed
untouched by that sweep.

Devtools-only dependencies (e.g. `rich` for rendering) are free: they never reach a release
artifact.

### 3. An **Exchange capture** has two sections of different provenance

One file per exchange, hops nested, JSONL-encoded:

- **`observed`** — the real inbound payload, the blindfolded outbound payload, the provider
  response as received, the restored response as returned, `session.injected`'s complete
  pair table, stage outcomes, timings, the resolution-gate verdict. Witnessed.
- **`reconstructed`** — offsets, `pass_name`, per-pass detail, L3 verdicts. Produced by
  replay. Every reconstructed field is **visibly marked**; a reader must never mistake a
  replayed `pass_name` for an observed one.

**Their disagreement is the primary signal** (see §9). The capture covers the **round
trip**, not egress only: Restore is the half that is currently observable only by eye, and
the **surrogate component** / bounded-suffix rules (ADR-0024/0036) are where quiet
wrongness lives.

The schema is **separate** from the Processing trace's but **reuses its field vocabulary**
verbatim where meanings coincide (`hop_index`, `hop_kind`, `endpoint`, `streamed`,
`outcome`, `reason`, `duration_ms`, `upstream_duration_ms`, `l3_provider`). ADR-0035 is
**not amended**: its refusal is about what shipped code holds, and nothing here changes
that.

### 4. Live capture composes on existing seams and refuses to start on drift

Devtools installs `app.dependency_overrides` for `get_upstream_client`, `get_mapping` and
`get_l3_detector`, and wraps `blindfold.app:app` in its own ASGI middleware. **No new hook
is added to the request path.** Streamed bodies are teed through the `UpstreamClient`
wrapper's `open_stream` and through the middleware — *not* via httpx `event_hooks`, which
fire on response start and cannot see a streamed body.

Devtools resolves its override targets at startup and **fails loudly if any is missing or
has changed shape**. A capture that silently omits the surrogate table is worse than no
capture, because the reader would conclude the exchange was clean.

### 5. Capture is incremental, bounded, and opt-in by named directory

`BLINDFOLD_EXCHANGE_CAPTURE_DIR`, read by devtools' **own** settings and **absent from
`blindfold.config`** — a release binary has never heard of the variable and cannot parse it,
so there is no path where the name is recognised but inert.

Records append as they occur (header · hops · outbound · provider chunks · restored chunks ·
footer), so nothing accumulates in memory and a proxy killed mid-stream still yields
everything that had arrived. A per-capture **size cap** writes a marked `truncated` record;
an unmarked truncation would masquerade as a restore failure. The **footer is the
completion marker**, so an in-flight capture is visible and a crash-abandoned one reads as
evidence ("no footer, last record is a provider chunk" ⇒ died waiting on upstream).

The directory is **count-bounded**, oldest evicted at capture *start*, never the live one.
Unbounded growth here means plaintext prompts accumulating on disk indefinitely.

### 6. `blindfold explain` replays; selection is a listing plus an id

`blindfold explain` runs the **real** pipeline over a request payload (either dialect,
auto-detected; `--text` wraps a bare file; an **Exchange capture** is accepted directly by
reading its `observed` inbound payload) and emits an Exchange capture. **It never sends
anything upstream.**

It reads the operator's **entity graph** read-only — validating against the vendored seed
would answer the wrong question — and passes `inbox=None`, so no **novel entity** from a
test payload can reach the real **Review inbox** or grow the graph through the **learning
loop**. Mints are harmless: `SurrogateMapping` is in-memory with no store handle, and (issue
#274) so is the ephemeral `ReviewInbox()` the engine substitutes for the call whenever a
detector is wired and `inbox=None` — it mints and adjudicates exactly as the live path does,
but is discarded with the call, never attached to a store, so a confirmed candidate can reach
neither the real Review inbox nor the entity graph.

An unwired L3 does **not** make `explain` fail closed — fail-closed protects **egress**, and
`explain` has none — but an L1/L2-only run (no adjudicator configured) stamps itself in the
artifact via `l3_wired`, which now means what it says: a wired detector actually adjudicated.
Before issue #274, `inbox=None` silently suppressed the run itself regardless of what was
wired, which made `explain`'s claim to double as the corpus evaluator (§11) structurally
false for the L3 tier — fixed at the source, not by weakening the claim.

Selection: `blindfold captures` lists (id, time, endpoint, hop count, detected count,
outcome, an excerpt of the first user hop), `blindfold explain <id>` resolves against the
capture directory, `--last` covers the common case. A printed table is greppable,
scriptable and pasteable; an interactive picker is deferred.

**The listing prints real prompt text to the terminal.** It is the operator's own machine
and own opted-in directory, and the file already holds the full plaintext — recorded here so
the property is signed off rather than emergent.

### 7. Every door into the capability carries the shared-store refusal

`blindfold_devtools` defines its own `refuse_if_shared_store(settings)` plus a named
exception, mirroring `serve.py`'s established `refuse_if_*` family, called from **every**
devtools entry point — the diagnostic proxy *and* `explain`/`captures`. A Diagnostic session
runs against a local SQLite store with the **Local key cipher** only.

Starting ordinary `blindfold serve` against a shared store is **not** a bypass: devtools is
not in the loop, so nothing is captured. But `explain` needs the **mapping cipher** to read
the graph's ciphertext, so it would touch other people's data — hence the refusal binds it
too. **The refusal is a property of the capability, not of the process.**

### 8. Replay is not the live run

L3 verdicts vary and the store moves on between capture and replay. **Surrogate** stability
means known entities reproduce; a heisenbug in L3 adjudication will not. This is the
accepted price of keeping the request path hook-free.

### 9. Comparison semantics: real values replaced, not surrogates

The comparable set is **which real values were replaced, per hop** — detection, not minting.
Surrogate identity is process-dependent for everything not already in the graph: a novel
entity enters the graph only on human confirm (and `explain` never confirms), and `mint_pii`
allocates from in-process counters, so a mid-session capture and a fresh replay will not
agree. Comparing surrogates would manufacture noise by construction.

Severity ladder, banner on the top two only:

- **`leak`** — a real value found in the observed blindfolded outbound payload.
- **`defect`** — a real value replaced live but not on replay, or vice versa, for a value
  the entity graph already knows.
- **`expected`** — novel/unconfirmed referent, PII counter position, L3 instability.
- **`unknown`** — the tool admitting it cannot classify.

A banner that fires on every exchange is one nobody reads, at which point the two-section
design is decoration rather than a safeguard.

Expected divergence is **derived, never curated**: graph membership decides whether a
surrogate must be stable, and reserved-namespace shape (`PII-RESERVED-…`, `ID-RESERVED-…`,
the reserved IBAN form) identifies an L1 PII mint from the string alone. This needs no new
field and no `pass_name` — which is what makes it viable, since `_HopContext` destroys
attribution.

### 10. The capture carries an offline leak check

Scan the observed outbound payload for every real value in `session.injected` **and** every
real value the entity graph knows. This is the offline twin of the **pre-egress leak gate** —
exhaustive where the inline gate must be fast — and it **validates that gate rather than
trusting it**. Distinct from #61 (inline structural re-check) and #78 (residual-content
leakage).

### 11. The assertive tier: `must` / `must_not`, two corpus tiers, deterministic gating only

A fixture carries `must` and `must_not` lists of real values (plus optional `layer`/`kind`)
over the same comparable set as §9. **`must_not` is not an afterthought**: over-redaction is
not free (`CONTEXT.md`), and a corpus asserting only misses would score a wrongly-blindfolded
code token as perfect.

Two tiers: a **shipped corpus** of published, licensed, provenance-pinned datasets (the
`tests/fixtures/pupa_subset.json` pattern) plus **Sample data**, which gates CI; and a
**private corpus** of the operator's own entities, pointed at by path, never committed and
never gating. A record carries `text` **or** `payload`, because hop structure is where the
interesting failures live.

**The gating corpus asserts only what L1/L2 deliver.** CI has no local L3, and L3 is not
reproducible while sampling is unpinned; L3-dependent expectations are marked and skipped
when the adjudicator is unwired. Reuse the `full_pupa_corpus` marker pattern.

### 12. The absence gate proves the guarantee, and `excludes` stays empty

Four checks:

1. **Static import check** — no `blindfold.*` module imports `blindfold_devtools`; ordinary
   `pytest`, every PR, naming the offending import.
2. **Binary containment** — no `blindfold_devtools` in the release binary's PKG entries,
   embedded PYZ, or `base_library.zip`, matched in both dotted-name and path form at a name
   boundary. Devtools-only dependencies ride the same name list.
3. **Frozen importability** — the binary itself reports the module unimportable, via a
   stdlib-only self-check flag on `packaging/blindfold_proxy_entry.py`.
4. **Positive control** — a canary binary built with `hiddenimports=["blindfold_devtools"]`
   on which checks 2 and 3 must **fail**; plus a canary smuggling the module through `datas`.

Checks 2–4 run **in the same job that freezes each platform's binary**, before assemble and
codesign. A gate in a different job than the freeze can be green while the artifact is dirty.

**`packaging/blindfold-proxy.spec` keeps `excludes` empty.** With `excludes` set, a
reachability regression produces a *clean* binary that passes every check and ships, failing
as a runtime `ImportError` in a user's hands; with it empty, the same regression bundles
devtools and fails the build. `excludes` does not add defence — it converts a detectable
build failure into an undetectable shipped one.

Hard fail everywhere. **PyInstaller is pinned exactly** (the `freeze` group currently floats
at `>=6.21`): check 2 uses an internal API and the mechanics are version-specific. If the
archive reader meets an entry it cannot inspect, it must fail loudly rather than skip — a
check that silently declines to look is the vacuous pass in its purest form.

### 13. `BLINDFOLD_DEV_MODE` is retired by hard cut

It means exactly one thing today — permit startup against a root Transit token (SEC-2,
ADR-0021) — and a second notion of "dev" would make the name a lie by aggregation. It becomes
`BLINDFOLD_ALLOW_ROOT_TRANSIT_TOKEN`, with a **named startup refusal** if the old name is
still set, following the existing `refuse_if_legacy_l3_env_vars` / `LegacyEnvVarError`
precedent. **No general "dev mode" flag is created**; `CONTEXT.md` lists "dev mode" and
"debug mode" under _Avoid_.

## Consequences

- The operator can finally answer the product's central question — *"this payload contains
  no real value, and here is the proof"* — without any shipped surface gaining the ability
  to show a real value.
- The shipped privacy posture is **unchanged**. No `if dev:` branch enters the request path,
  no new real-value surface exists in a release artifact, and ADR-0035 stands unamended.
- Debugging requires **reproducing** a failure on a source-run proxy. A one-shot failure on a
  release build remains unobservable. Accepted: failures here are systematic (a name class, a
  token class), not one-shot.
- A Diagnostic session writes **plaintext prompts to disk** in a directory the operator
  named. Bounded by count and opt-in, but real — and the listing prints prompt excerpts to
  the terminal.
- Devtools composes on `dependency_overrides`, which is **not a devtools-only convention**
  but the test suite's own mechanism; a rename that would break capture breaks many tests on
  `main` first, and the startup drift check catches the rest.
- The absence gate makes "no diagnostic code ships" a **property with a positive control**
  rather than an intention, at the cost of pinning PyInstaller exactly and re-validating on
  every bump.
- **Open, deliberately not decided here:** whether captured exchanges need redaction of their
  own (a real prompt can carry API keys that are not **entities**); the `pyinstaller40`
  entry-point hook vector, by which *any installed distribution* can inject a build hook with
  no diff in this repo; and whether the tray/menu bar path interacts with source-run at all.
- **L3 adjudication is non-deterministic** (no sampling parameters are pinned anywhere).
  Tracked outside this ADR; until it is fixed, L3-only differences classify as
  `expected/unstable` and the assertive tier cannot gate L3.

## Alternatives considered

- **A runtime `dev mode` flag** (env var) — rejected: it is what one reaches for first and
  the one that erodes; every future privacy guard grows an `if dev_mode:` branch, and a
  single boolean unlocking everything is exactly the shape being avoided.
- **A build-frozen flag** baked into the `.app`/tray build — rejected: weaker than it looks.
  A flag frozen at build is still a branch in shipped code, and Blindfold is not code-signed
  yet (#198), so the "signed release" half of the guarantee does not exist.
- **A no-op observer seam** in the shipped request path (events to registered observers,
  nothing registered in release) — rejected: the seam *is* present in shipped code, and "no
  observer installed" is a materially weaker promise than "no code exists".
- **Always capturing into an ephemeral in-memory buffer**, rendered by devtools — rejected
  outright: shipped code would hold raw hop plaintext in memory, precisely the surface
  ADR-0035 refuses.
- **Retaining `DetectedSpan` lists in `_HopContext`** so capture could read attribution —
  rejected: it would make shipped code retain real values per span for no shipped purpose.
  The current discarding is the privacy property holding.
- **Keeping `excludes=["blindfold_devtools"]` for defence in depth** (with a warn-file
  assertion to recover the lost signal) — rejected: see §12. It guarantees the gate can never
  catch the regression it exists to catch, and the recovery grep is a version-fragile read of
  a debug artifact.
- **A second frozen target / diagnostic `.app`** — rejected: it exists only to save typing
  one command, and it can be handed to someone else.
- **A single buffered JSON object per capture** — rejected: a proxy killed mid-stream would
  yield nothing, which is the case incremental capture exists for.
- **A `.partial` suffix renamed on completion** — rejected: it hides the one capture most
  worth seeing when the proxy hangs mid-exchange.
- **A devtools HTTP route** alongside the shipped app — rejected: a route implies a view and
  a view implies a bundle; that is a second frontend for reading files already on disk.
- **Comparing surrogates between observed and reconstructed** — rejected: process-dependent
  for everything not in the graph, so it would fire on nearly every exchange.
- **A curated list of expected divergences** — rejected: it rots and ends up suppressing real
  defects.
- **Re-identify-annotated Processing trace** (resolving a scrubbed record's surrogates through
  the audited `re-identifier` path) — a good idea, ruled **out of scope**: it is a product
  decision about unmasking, not a dev-tooling one.
