# ADR-0044: The supervisor owns the proxy's launch environment

**Status:** Accepted
**Date:** 2026-07-25

## Context

The macOS **supervisor** (ADR-0039) shipped able to spawn, supervise and stop the **proxy**,
but with **no way to configure it**. Launched on a Mac with no environment prepared, the proxy
starts and sits permanently in **Degraded**: `BLINDFOLD_L3_MODEL` defaults to empty, which
means "L3 unconfigured" and fails closed (ADR-0009). Nothing in the menu can change that.

Two things make this worse than a missing settings panel.

**The environment the supervisor passes on is accidental.** The launcher never set the child's
environment, so the proxy inherited whatever the supervisor inherited. A GUI app launched from
Finder, the Dock, or a login item (#216) gets a *login* environment with no shell profile — so
`BLINDFOLD_*` variables exported in `.zshrc` or sourced from a `.env` are simply absent. The app
therefore reached **Protected** only when launched from an already-prepared terminal, and was
silently Degraded every other way. This is the same wall the Windows tray hit in #197, where the
smoke test eventually stopped requiring Protected because env propagation to the child was
unreliable.

**Settings cannot live only in the management SPA.** `/ui/settings` is where settings already
live (#188's Unprotected-mode capability toggle), but the SPA is served *by the proxy*. If
configuration is what stops the proxy starting, the settings that would fix it are unreachable —
a deadlock. ADR-0034 also assigned GLiNER provisioning to **Setup**, which is likewise
SPA-served, and CONTEXT.md scopes Setup to workspace/admin/entities rather than machine wiring.

Compounding it, the ambient environment is not merely unreliable but actively hazardous: ADR-0031
made `BLINDFOLD_OLLAMA_*` legacy, and their *mere presence* hard-refuses startup. A stale export
in a shell profile is enough to stop the proxy dead.

## Decision

The **supervisor** owns a **launch environment** (CONTEXT.md) and is its sole author.

- **The supervisor owns the configuration, and injects it.** It already owns the child's
  lifecycle, so owning its launch parameters is cohesive. This is what breaks the deadlock:
  configuration stays editable when the proxy will not start.

- **All-or-nothing on `BLINDFOLD_*`.** The child inherits the *general* environment (`PATH`,
  `HOME`, locale — the dev-fallback path shells out to `uv`, which needs them) but **every**
  `BLINDFOLD_*` value comes from the launch environment, and any ambient one is **stripped**.
  Behaviour is then identical from a menu, a terminal, or a login item, and the ADR-0031 legacy
  variable refusal becomes *structurally unreachable* rather than a documented hazard.

- **Secrets included, stored natively.** `BLINDFOLD_L3_API_KEY` and `BLINDFOLD_OPENBAO_TOKEN`
  are in scope — excluding them would leave the two most consequential values unconfigurable, so
  a Finder-launched app still could not reach Protected. Secrets go to the **Keychain** in
  bundled builds and `UserDefaults` in dev builds, because an ad-hoc signature changes on every
  rebuild and Keychain ACLs would re-prompt each time (a pattern already proven in a sibling
  macOS app on the same machine). This does not worsen the posture: whichever path is taken the
  secret ends up in the child's environment, readable by a same-user process, exactly as today.

- **The proxy does not learn to read `.env`.** A `.env` fallback would *silently defeat* the
  stripping rule: the supervisor removes ambient `BLINDFOLD_*`, then the proxy refills them from
  whatever `.env` sits in its working directory — and the dev-fallback spawn runs with the repo
  as cwd, so a `postgresql://` DSN there would quietly override the ADR-0043 SQLite default.
  CLI users opt in explicitly (`uv run --env-file`); the supervisor offers a **one-shot import**
  that copies a `.env` into the launch environment, after which the store is the authority.

- **Fields that shadow a store setting must be able to defer.** The L3 provider field is
  tri-state and defaults to **automatic**, which *omits* `BLINDFOLD_L3_PROVIDER` so ADR-0034's
  persisted activation flag still decides. Without this, a Mac user could flip ADR-0034's
  "Enhanced local detection" toggle, be told *"Restart Blindfold to activate"*, restart — and
  silently get nothing, because the supervisor re-injected a concrete provider. Explicit env
  beating the persisted flag is the documented rule; omission is how the supervisor opts out of
  exercising it. This generalizes to any future field shadowing a store setting.

- **Restart is required, but not gratuitous.** Proxy configuration is startup-resolved by design
  (ADR-0034 §1 preserves the startup-only, fail-closed config model). Saving therefore prompts
  for a restart when the proxy is healthy — it is in the request path, and the GLiNER cascade
  costs roughly two minutes to boot — but starts immediately when the proxy is **Stopped** or
  **Refused**, where there is no in-flight traffic to protect.

- **Validation is advisory and shared.** The supervisor pre-checks only *locally decidable*
  rules — is an oMLX base URL loopback, does a model tag end in `:cloud`, are legacy keys
  present — and those rules live in `BlindfoldCore`, keeping them Linux-testable and
  leak-audit-reachable (ADR-0040). The proxy's five startup guards remain authoritative; the
  remaining two are not client-side implementable (the root-token check needs a live Transit
  call, the model check needs the **Data directory**). To stop the duplicated rules drifting,
  `fixtures/supervisor-golden-vectors.json` becomes a **three-language** contract, read by the
  Python tests as well as Swift and C#.

## Consequences

- A **third** source of proxy configuration now exists — the real environment, the store's
  persisted settings, and the launch environment. This is why the term is in CONTEXT.md: "the
  config" was already ambiguous and would only get worse.
- The supervisor becomes a place secrets live. It still holds **no entity data** and is still not
  in the request path, so the CONTEXT.md constraint holds, but the qualifier is now "no entity
  data", not "nothing sensitive".
- `set -a; . ./.env` before launching the app **stops working** — deliberately. The one-shot
  import is the migration path.
- Swift's `StartupRefusalReason` must grow reasons for the legacy-variable, `:cloud`-model and
  missing-model guards: three of the five currently collapse to `"startup failed"`, which no
  settings UI can guide a user out of. These are configuration facts, not entity values, so
  naming them costs nothing in privacy.
- ADR-0034 §2 gated its Setup toggle on "a persistent store (Postgres today)". **ADR-0043
  un-gated that** by making SQLite the persistent default, which is why the activation flag is
  live on a default Mac install and why the deferral rule above is load-bearing rather than
  theoretical.
- Provider discovery (probing the conventional Ollama and oMLX ports and listing their models)
  becomes worthwhile once a launch environment exists to write the answer into.

## Alternatives considered

- **Settings in the management SPA only** — rejected: deadlocks. The SPA is served by the very
  process the settings would repair.
- **The proxy owns a config file the supervisor edits** — rejected for now: one source of truth
  is attractive, but the proxy is deliberately env-only and startup-resolved, so this means
  adding a config-file layer to the cross-platform component rather than a settings window to
  the platform one. Revisit if non-supervisor installs need persisted local config.
- **Merging the launch environment over the inherited one** — rejected: friendlier to a prepared
  shell, but behaviour then depends on *how the app was launched*, which is the defect being
  fixed, and it leaves the legacy-variable landmine armed.
- **Supervisor stores only non-secret configuration** — rejected: leaves the oMLX API key and
  the Transit token unconfigurable, so the Mac app still cannot reach Protected from a Finder
  launch. Half a settings surface is worse than none, because it looks complete.
- **The supervisor stores a Keychain reference and the proxy reads Keychain itself** — rejected:
  keeps secrets out of the supervisor, but requires teaching cross-platform Python a
  macOS-specific secret store.
- **Duplicating all five startup guards in Swift** — rejected: best feedback, worst drift, and
  the root-token guard is not implementable client-side anyway.
