# ADR-0031: Provider-agnostic L3 adjudicator — add oMLX (OpenAI-compatible) alongside Ollama

**Status:** Proposed
**Date:** 2026-07-14

## Context

CONTEXT.md already defines L3 as a role, not a model choice: "any on-device
implementation behind the adjudicator seam (LLM via Ollama today; a small local
classifier or a cascade tomorrow) is L3." ADR-0022 wired the *first* concrete
implementation behind that seam — a local Ollama daemon — and the code has since
drifted from the seam's own framing: `OllamaAdjudicator` is the only client,
`Settings.ollama_addr` / `Settings.ollama_model` / `BLINDFOLD_OLLAMA_ADDR` /
`BLINDFOLD_OLLAMA_MODEL` name the vendor rather than the role, and the local-only
startup guard (`is_cloud_model`, the Ollama `:cloud`-tag check) is Ollama-specific.

oMLX (and similar MLX-native servers on Apple Silicon) is materially faster than
Ollama for local inference and exposes an OpenAI-compatible surface
(`POST /v1/chat/completions`, `GET /v1/models`) rather than Ollama's native
`/api/generate` / `/api/tags`. Operators want to point L3 at it. This is not a
config swap: it needs a second real client behind the `L3Adjudicator` seam, and —
more importantly — its own local-only enforcement story, since ADR-0022's absolute
invariant ("L3 runs on-device only", CONTEXT.md "Key invariants") was encoded as a
check against one vendor's remote-execution tag and does not generalize for free.

Separately, oMLX's own default port (`localhost:8000`) collides with blindfold's
own default serve port (`DEFAULT_PORT = 8000`, `config.py`). That collision is
tracked as its own decision below since it's a config-default change, not a new
seam implementation, but it's recorded here because both changes are prerequisites
for running blindfold and oMLX on the same machine.

## Decision

### 1. Generalize config names to the role, not the vendor

Rename (with the same env-var delivery mechanism — nothing about *how* config is
read changes, only the names):

- `BLINDFOLD_OLLAMA_ADDR` → `BLINDFOLD_L3_BASE_URL`
- `BLINDFOLD_OLLAMA_MODEL` → `BLINDFOLD_L3_MODEL`
- New: `BLINDFOLD_L3_PROVIDER` — `ollama` (default, preserves today's behavior
  exactly for anyone with existing config) | `omlx`.

`Settings.ollama_addr` / `Settings.ollama_model` become `Settings.l3_base_url` /
`Settings.l3_model`; `Settings.l3_provider` selects the client. This is a breaking
rename (not a back-compat shim, per project convention of not carrying
backwards-compatibility hacks) — operators with existing `BLINDFOLD_OLLAMA_*` env
vars must update them. The CLI/status output should say so plainly if unconfigured
vars are detected under the old names — a clear startup error, not a silent
no-op — but no code path reads the old names going forward.

### 2. A second real client behind the same seam: `OpenAICompatibleAdjudicator`

Add a client (parallel to `OllamaAdjudicator` in `ollama.py`) that speaks:

- `POST {base_url}/v1/chat/completions` with `response_format: {"type": "json_object"}`
  (or the equivalent structured-output mechanism the target server supports),
  carrying the same adjudication prompt template `OllamaAdjudicator` already uses.
- `GET {base_url}/v1/models` as the liveness probe (replaces `GET /api/tags` for
  this provider), same shape as `ping_ollama` → generalized `ping_l3`.

`app.py`'s wiring (`_build_l3_adjudicator` or equivalent) selects
`OllamaAdjudicator` or `OpenAICompatibleAdjudicator` by `settings.l3_provider`,
both behind the unchanged `L3Adjudicator` protocol in `l3.py` — the mint pass
(`_blindfold_text`) and fail-closed 503 path (ADR-0022 §1) don't change at all.

### 3. Local-only enforcement is provider-specific and explicit — no generic catch-all

ADR-0022 already rejected "loopback base URL is sufficient" as a local-only check,
because a local Ollama daemon can transparently proxy a `:cloud` model to a remote
host even when reached over loopback. The same risk applies here in a different
shape: "OpenAI-compatible" is not a safety property — the real, remote OpenAI API
is trivially "OpenAI-compatible" with itself, and at least one MLX-native server in
this space (Rapid-MLX) advertises its own optional cloud routing. A generic
`BLINDFOLD_L3_PROVIDER=openai_compat` pointed at an arbitrary base URL would
reopen exactly the off-device leak ADR-0022 closed.

So `BLINDFOLD_L3_PROVIDER` takes an explicit, vetted value per server — `omlx`
today — not a generic "any OpenAI-compatible endpoint" escape hatch. Each new
provider value added in the future must document its own local-only story in an
ADR amendment before being accepted, matching ADR-0022's "no override" stance.

For `omlx` specifically: plain oMLX (unlike Rapid-MLX) has no remote/cloud-routing
feature at all — it only serves MLX-format weights it holds locally — so the
necessary condition ADR-0022 already requires (a loopback `BLINDFOLD_L3_BASE_URL`
host: `127.0.0.1` / `localhost`) is also sufficient for this specific, named
provider. `refuse_if_cloud_model`-equivalent startup guard for `omlx`: refuse to
start unless `BLINDFOLD_L3_BASE_URL`'s host is loopback. No override, for the same
reason ADR-0022 gives none — sending real candidate spans off-device categorically
defeats the product.

### 4. `BLINDFOLD_PORT` default moves off 8000 (separate, smaller decision)

Blindfold's own `DEFAULT_PORT` (`config.py`) collides with oMLX's own default
listen port. Change `DEFAULT_PORT` to a value outside the crowded 80xx/local-LLM
port band (Ollama 11434, oMLX/LM Studio 8000, mlx-omni-server, etc.). This is a
pure default-value change — `BLINDFOLD_PORT` remains overridable exactly as today
— tracked as issue work, not requiring its own ADR since no new mechanism is
introduced.

## Consequences

- Operators can point L3 at oMLX for materially faster on-device adjudication,
  without weakening the local-only invariant, provided they use the `omlx`
  provider value (not a generic passthrough).
- Existing `BLINDFOLD_OLLAMA_ADDR` / `BLINDFOLD_OLLAMA_MODEL` deployments must
  rename their env vars; this is a deliberate breaking change, not silently
  shimmed.
- `docs/`, `README.md`, `tests/` and any dev tooling (`frontend/vite.config.ts`)
  that hardcode `8000` need updating alongside the port default change.
- Every future L3 provider addition requires its own explicit local-only
  enforcement design (an ADR amendment), not a generic "OpenAI-compatible" bucket
  — a deliberate constraint to keep the absolute local-only invariant honest.
- `status.py` / `app.py` comments referencing "Ollama" as if it were the only L3
  backend (e.g. "probe storm against Ollama/Transit", the `detail="ollama
  unreachable"` string) should be reworded to the provider-agnostic "L3" framing
  CONTEXT.md already uses, to stop the code re-drifting toward vendor-specific
  language.

## Alternatives considered

- **Generic `BLINDFOLD_L3_PROVIDER=openai_compat` pointed at any base URL** —
  rejected: "OpenAI-compatible" describes a wire format, not a locality guarantee;
  this would let an operator (or a misconfigured default) point L3 at a real cloud
  OpenAI-compatible endpoint with no refusal at startup, defeating ADR-0022's
  invariant outright.
- **Keep `BLINDFOLD_OLLAMA_*` names, add oMLX as a special case under them** —
  rejected: further entrenches vendor-named config for what CONTEXT.md already
  calls a role-based seam, and reads backwards the next time a third provider
  shows up.
- **Silently accept both old and new env var names (back-compat shim)** —
  rejected per project convention: no feature-flag/back-compat shims when the
  code can just be changed; a clear rename with a loud startup error for the old
  names is preferred over a quiet dual-read path.
