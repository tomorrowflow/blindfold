# ADR-0057: Claude Desktop in 3P Gateway mode is a redirectable client — in scope, no interception, no new proxy

**Status:** Proposed — flips to Accepted when the live contract spike (#372, HITL) confirms the wire contract; the two defects it names are Accepted-grade fixes under ADR-0006 / ADR-0051 regardless
**Date:** 2026-08-27
**Resolves:** issue #62 (scope + feasibility of closed clients, starting with Claude Desktop)
**Amends:** ADR-0027 (error envelope, §D4 below)

## Context

Issue #62 asked whether Blindfold should support **closed clients whose endpoint can't be
redirected**, starting with Claude Desktop, and required a decision before any
implementation. Two drafts preceded this ADR: a July design that reached Claude Desktop by
MITM (TLS interception, a private CA in the OS trust store, TLS-fingerprint impersonation, a
parser for claude.ai's private completion wire format), and an August draft that noticed
Claude Desktop had grown an officially supported **third-party (3P) inference** mode and
proposed building "a plain Anthropic-Messages-compatible gateway" for it — but described that
gateway in vocabulary Blindfold does not use (a per-conversation "vault", delimited
`⟦…⟧` placeholders, a server-held upstream key).

The verified platform facts (`docs/research/claude-desktop-3p-gateway.md`) change the
premise of #62:

- Claude Desktop in 3P mode dials a **gateway base URL the operator configures** and speaks
  the Anthropic Messages API: "`POST /v1/messages` with streaming and tool use is required.
  `GET /v1/models` is optional." Conversation history is on-device. Chat, Cowork and Code all
  route through it; Chat is admin opt-in (`chatTabEnabled: true`).
- Auth is the client's own credential, sent as `Authorization: Bearer` (default) or
  `x-api-key`. Static custom headers (`inferenceCustomHeaders`) ride on every request.
- The per-user config is a flat JSON profile under
  `~/Library/Application Support/Claude-3p/configLibrary/`, read once at launch; a managed
  `com.anthropic.claudefordesktop` plist overrides and locks it. A vendor (Ollama) already
  ships a config writer that points Desktop at `http://127.0.0.1:<port>`.

So Claude Desktop is no longer a *non-redirectable* client. The CONTEXT.md non-goal —
"Intercepting apps whose endpoint can't be redirected … Scope is tools where the base URL is
configurable" — does not need reopening; the client moved to the side of the line Blindfold
already serves. What remains is to say so, to name exactly which parts of the existing
request path the Desktop contract exercises that Claude Code does not, and to reject the
parts of the drafts that would have made Blindfold drift.

What already holds, and is only *confirmed* here (file anchors as of this commit):

- Every **hop** is blindfolded — `system` (string or block array), every `messages[]` turn
  including **assistant** turns, `tool_result` content, `tool_use.input` in history, tool
  descriptions and schema prose (`engine.py: blindfold_payload`, ADR-0002, ADR-0023 §3).
  Because Desktop stores *restored* history on-device and resends it every turn, the
  assistant-turn hop is load-bearing; it is already a hop with no role filter.
- The mapping is **global and deterministic** (ADR-0005, ADR-0007, ADR-0051): the same real
  value re-mints to the same surrogate on every resend with no session state. Blindfold has
  no conversation identifier and needs none — the draft's `conversation_id`-scoped vault was
  solving a problem the deterministic blinder does not have.
- **Sliding-window restore** (ADR-0006, ADR-0024, ADR-0036) already handles a surrogate split
  across SSE deltas for `text_delta` and reassembles `input_json_delta` before restoring
  (`app.py: _process_sse_event`). The draft's "only genuinely new code" exists.
- `anthropic-*` request headers pass through as an open prefix; `x-blindfold-workspace` is
  consumed, never forwarded (ADR-0054). Desktop's `inferenceCustomHeaders` is the natural
  carrier for the workspace header — the same mechanism the Connect page already documents
  for Claude Code (`ANTHROPIC_CUSTOM_HEADERS`).
- The inbound client credential **is** the upstream credential; the proxy holds none
  (ADR-0019). The real-value side of the mapping is encrypted at rest under the mapping
  cipher when a Store key or Transit is configured (ADR-0043, ADR-0045).

## Decision

**D1 — Claude Desktop in 3P Gateway mode is in scope, as an ordinary redirectable client.**
It is served by the existing `POST /v1/messages` request path. We will not build a second
proxy, a second wire-format parser, or a per-client code path. The CONTEXT.md non-goal is
sharpened, not reversed: interception of non-redirectable endpoints stays out; Claude
Desktop is in *only* through its Gateway mode.

**D2 — No interception, ever.** MITM, TLS interception, certificate injection into a trust
store, and TLS-fingerprint impersonation are rejected categorically for this client and any
future one. A design that reintroduces any of them has drifted back to the July draft and
stops. This also closes #62's "feasibility spike on interception mechanisms": the spike is
moot because the vendor provided the redirect.

**D3 — The proxy still holds no upstream credential.** ADR-0019 stands. The Desktop user
enters their own Anthropic (or Bedrock/Vertex/Foundry-compatible) key as a *static API key*
in the Desktop profile; Blindfold forwards it verbatim. The August draft's "inject the real
key server-side, the real key never reaches the client" is rejected: it inverts the trust
model (the client *is* the key's owner), creates a secret the proxy must custody (a
non-goal), and turns a loopback single-owner proxy into a credential-bearing service.

**D4 — Error bodies gain the Anthropic error envelope (amends ADR-0027).** Blocks stay HTTP
errors, never synthetic model responses — ADR-0027's decision is unchanged. What changes is
the *shape*: every Blindfold-authored error body (fail-closed block, leak-gate block,
upstream-error mapping) carries the Anthropic envelope — top-level `"type": "error"` with
`error.type` and `error.message` present — **in addition to** the existing Blindfold fields
(`code`, `sub_reason`, `event`, `reason`, `remedy`, `management_url`, `workspace`), which
are preserved byte-for-byte so nothing that parses them today breaks. ADR-0027 assumed a
client that renders `error.message` verbatim and named Claude Code as that client; its own
reopening clause ("If a client that matters is found to swallow error bodies silently, that
fact reopens this decision") is what this amendment exercises. The HTTP status code of a
block (`503`) is *not* changed by this ADR; whether Desktop retries a 503 silently is a
question for the contract spike (#372), and if it does, the status choice reopens as a follow-up
under this ADR rather than being guessed now.

**D5 — `GET /v1/models` stays unimplemented; the Desktop profile pins `inferenceModels`.**
The existing rationale (`app.py` module docstring: a passthrough discloses what the
configured credential can access; a hardcoded list goes stale) holds for Desktop too, and
the platform behaviour makes the absence cheap: "an error response makes the app fall back
to the `inferenceModels` list immediately". The profile Blindfold emits therefore lists full
model IDs (which also skips the discovery call) — Opus, Sonnet and Haiku so Cowork sub-agents
resolve. An empty `inferenceModels` with no `/v1/models` yields an empty picker; the emitted
profile must never leave it empty.

**D6 — Two request-path defects are fixed as defects under their governing ADRs, not as
Desktop features.**

1. **Streaming restore does not cover `thinking_delta`.** `_process_sse_event` restores
   `text_delta` and reassembles `input_json_delta`, but a `thinking_delta` falls through the
   pass-through branch. Non-streaming restore *does* cover `thinking` (deny-by-default block
   walk, ADR-0051 amendment / #323), so the two modes disagree. A surrogate emitted inside a
   streamed thinking block reaches the client unrestored and then trips the post-restore
   resolution gate *after* the bytes are on the wire — the worst shape of failure. Desktop
   with summarized thinking on is the client most likely to hit it. Fix: `thinking_delta.
   thinking` goes through the same sliding-window restorer with the same per-block flush on
   `content_block_stop`. ADR-0006 governs.
2. **`redacted_thinking.data` is walked by the blinder.** `_BLOCK_NON_HOP_KEYS` shields
   `type`, `id`, `tool_use_id` and `signature`, but not `data` — so the deny-by-default walk
   runs deterministic blinding *and L3 candidate production* over a provider-encrypted blob.
   A hit inside it corrupts ciphertext the provider must verify on resend. Fix: `data` on a
   `redacted_thinking` block joins the closed non-hop set with the same argument `signature`
   made (#323) — it is never prose, and the provider verifies it byte-exact. Per ADR-0051's
   symmetry rule, whatever the blinder skips the pre-egress leak gate skips identically.

**D7 — Onboarding is a profile, authored by Blindfold, applied by the operator.** The Connect
page gains a Claude Desktop section that emits the flat-key profile JSON (`inferenceProvider:
gateway`, `inferenceGatewayBaseUrl: http://127.0.0.1:<port>`, `inferenceGatewayAuthScheme`,
`chatTabEnabled: true`, pinned `inferenceModels`, `inferenceCustomHeaders` carrying
`x-blindfold-workspace`) with the apply-and-relaunch steps, and a CLI writer
(`blindfold connect claude-desktop [--restore]`, the Ollama precedent) that writes it into
`configLibrary/` and `_meta.json`. The writer never touches a managed plist — fleet rollout
is the operator's MDM exporting the same keys, and a managed profile locking the endpoint is
the compliance-grade path, not something the proxy does.

## Consequences

- #62's three acceptance criteria are met: the decision (this ADR), the feasibility question
  (moot — vendor redirect, D2), and the slice plan: #372 HITL contract spike; #373 `thinking_delta` restore; #374
  `redacted_thinking` opacity; #375 Anthropic error envelope; #376 Connect page profile;
  #377 CLI writer (#376/#377 are blocked by #372).
- The **Connect page** and **BETA.md** currently name Claude Desktop as unsupported
  ("Non-redirectable clients unsupported", row #62). Both stay true until D7 ships, then the
  BETA row is retired and the Connect copy updated — by #376, not before.
- **Thinking `signature` round-trip becomes a live invariant to watch.** Upstream signs the
  thinking text it produced — text that contains surrogates. Blindfold restores it for the
  client and leaves `signature` untouched; on resend the assistant hop is re-blindfolded, and
  the signature verifies only if every real value re-mints to the *identical* surrogate. A
  provisional surrogate promoted on confirm, a curator's surrogate edit, or a pool
  reassignment between turns invalidates it. Claude Code rarely resends signed thinking;
  Desktop resends full history every turn, so this fires more. The contract spike measures
  it; if it bites, the follow-up is a decision (e.g. drop `thinking` blocks from resent
  history, which the API permits) rather than a silent workaround.
- **Latency budget.** Desktop runs a 300-second byte-watchdog on the stream, and Blindfold
  emits no bytes until blindfolding (including L3 minting on a large first hop) finishes.
  Claude Code has the same shape and survives; the spike confirms Desktop's first-turn
  behaviour with a real Cowork task rather than assuming.
- **The management surface shares the loopback port** (`/v1/management/*`, the SPA), gated
  by header-only identity. This is unchanged from today (ADR-0021: loopback is the
  boundary; ADR-0019 defers proxy auth to #38) and is not made worse by Desktop — but
  Cowork's VM reaches the gateway through Desktop's own egress path, so the spike checks that
  the VM cannot reach the management routes any more than Claude Code's subprocesses can.
  A negative result reopens #38 with a concrete client.
- **`anthropic-beta` is mostly suppressed on 3P.** Desktop strips experimental betas by
  default "because strict gateways reject" them. Blindfold forwards whatever arrives
  (ADR-0054), so nothing breaks; the processing trace will simply show fewer beta names.
  The Desktop-side `?beta=true` query string is dropped on the upstream leg today (Claude
  Code sends it too and works) — noted, not changed.
- **No conversation scoping, by design.** `x-claude-code-session-id` is still dropped and not
  consumed (ADR-0054 §4). If a future per-conversation feature needs it, that is ADR-0054's
  "separate, future decision", not a Desktop prerequisite.

## Alternatives considered

- **MITM / TLS interception (the July draft).** Rejected (D2). Even before the vendor shipped
  3P mode it was fragile (certificate pinning, fingerprint churn, a private wire format) and
  legally/ToS-adjacent; after it, it is strictly worse than the supported path.
- **A separate Desktop gateway service beside the Code proxy.** Rejected (D1). The Desktop
  contract is the Messages API Blindfold already serves; a second service duplicates the
  request path and halves the test coverage each gets.
- **Server-held upstream credential (the August draft).** Rejected (D3), see above.
- **Per-conversation mapping scope (`conversation_id` vault).** Rejected: the deterministic
  global mapping already yields consistent re-blindfolding of resent history, and
  per-conversation scoping was explicitly rejected in ADR-0007 for the store.
- **Delimited placeholders (`⟦PERSON_7⟧`).** Rejected: Blindfold's surrogates are plausible
  words (ADR-0005) or reserved-namespace opaque tokens (ADR-0052) precisely so the model
  answers naturally and so the corpus-collision argument holds; the sliding-window restorer
  already handles word-like surrogates split across chunks without delimiters.
- **Implement `GET /v1/models` as an upstream passthrough.** Rejected (D5): the
  config-disclosure argument stands and the platform's fallback is immediate. Reopen if a
  client is found that hard-fails on the endpoint's absence rather than falling back.
- **Keep the Blindfold-private error body as-is.** Rejected (D4): a client that receives
  `{"error": {...}}` without the `type: "error"` envelope may render a generic gateway
  failure; adding the envelope is additive and costs nothing.
- **Reverse the CONTEXT.md non-goal wholesale.** Rejected: the non-goal is about
  non-redirectable endpoints and is still right for claude.ai web and ChatGPT desktop/mobile.
