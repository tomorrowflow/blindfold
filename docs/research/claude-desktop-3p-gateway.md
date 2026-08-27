# Claude Desktop third-party (3P) inference — Gateway mode, verified facts

**Date:** 2026-08-27
**Question:** Can Claude Desktop be pointed at Blindfold without interception, and what
exactly does it expect from the gateway? (Issue #62; supersedes the July MITM draft and the
August "3P Gateway mode" implementation draft that motivated this note.)

Primary sources: `https://claude.com/docs/third-party/claude-desktop/*` (installation,
gateway, chat, configuration-reference, data-storage, mdm, in-app-configuration) and
`https://code.claude.com/docs/en/llm-gateway-protocol`. Quotes are verbatim from those
pages unless marked **[secondary]**.

## Verdicts

| # | Claim in the draft | Verdict | Evidence |
|---|---|---|---|
| 1 | Developer Mode → *Configure Third-Party Inference* → provider **Gateway**, speaks the Anthropic Messages API | **Confirmed** | gateway page: "The gateway must implement the Anthropic Messages API: `POST /v1/messages` with streaming and tool use is required. `GET /v1/models` is optional." |
| 2 | GA on 2026-07-09 | **Unverified** | Only secondary sources say so. The `inferenceProvider` / `inferenceGatewayBaseUrl` keys predate 2026-04-16 in the changelog; the feature is documented as production, not beta. |
| 3 | Chat is dropped on 3P routes | **Contradicted** | chat page: "Chat is off by default and is enabled with a single configuration key" (`chatTabEnabled: true`). Feature matrix: "Chat ✓ (admin opt-in)". No per-provider restriction. The "Chat unavailable in 3P" statement comes from an AWS sample README **[secondary]** that predates Chat's 3P beta (v1.13576.0, 2026-06-16; GA v1.21459.0, 2026-07-14). On ≥1.26832.0 Chat and Cowork merge into one **Home** surface. |
| 4 | History is on-device | **Confirmed** | data-storage: "There is no sign-in step, no cloud-stored conversation history, and no per-user state on Anthropic infrastructure." macOS: `~/Library/Application Support/Claude-3p/local-agent-mode-sessions/` (Chat + Cowork) and `claude-code-sessions/` (Code). |
| 5 | Auth: Bearer or `x-api-key`, static key | **Confirmed** | `inferenceGatewayAuthScheme`: "One of: `bearer`, `x-api-key`. Defaults to `bearer`." `inferenceCredentialKind`: `static` (default), `helper-script`, `interactive` (OIDC PKCE), `vendor-profile`, `oauth`, `workforce`. |
| 6 | Static custom headers on every request | **Confirmed** | `inferenceCustomHeaders`: "Sent on every inference and model-discovery request (joined into the CLI's `ANTHROPIC_CUSTOM_HEADERS`) … Do not put API keys, bearer tokens or other credentials here". |
| 7 | `anthropic-version` / `anthropic-beta` forwarded | **Confirmed, with a twist** | `anthropic-version: 2023-06-01`. Desktop **suppresses experimental `anthropic-beta` headers by default on 3P** "because strict gateways reject the experimental `anthropic-beta` request headers"; `toolSearchEnabled` re-enables them. Inference posts to `/v1/messages?beta=true` — "match on the path, not the full URL". |
| 8 | A stable per-conversation identifier reaches the gateway | **Partial** | Code sessions send `x-claude-code-session-id` / `-agent-id` / `-parent-agent-id` (gateway-protocol page). Not documented for Chat/Cowork. The system prompt carries "a short attribution block … containing the client version and a fingerprint derived from the conversation". `metadata.user_id`: undocumented. |
| 9 | Empty model list → picker from `GET /v1/models` | **Confirmed** | "When `inferenceModels` is unset, Claude Desktop on 3P populates the model picker from your gateway's `GET /v1/models` response. Auto-discovery shows only models whose IDs are recognizably Claude" (or that carry `anthropic_family_tier`). "an error response makes the app fall back to the `inferenceModels` list immediately, while an endpoint that accepts the request and hangs delays the model list by up to 10 seconds at launch." Full IDs in `inferenceModels` skip the call. |
| 10 | Use `127.0.0.1`, not `localhost`; plain `http://` loopback OK | **Unverified (works in practice)** | The `127.0.0.1` rule is documented only for the OIDC redirect URI. No primary statement on scheme/host for `inferenceGatewayBaseUrl`. Ollama's shipped writer uses `http://127.0.0.1:11435` **[secondary, source code]**. Bootstrap-*server* responses reject loopback URLs — irrelevant to local/MDM config. |
| 11 | Config read at launch only; Test connection exists | **Confirmed** | configuration reference: "Configuration read once at launch. Full quit and relaunch required after any change." In-app window "tests the connection against your endpoint". |
| 12 | Export + MDM via `com.anthropic.claudefordesktop`; managed config locks the endpoint | **Confirmed** | mdm page: exports `.mobileconfig`, `.reg`, ADMX, Profile Manifest, Bootstrap JSON. `/Library/Managed Preferences/com.anthropic.claudefordesktop.plist`. "When a managed source sets any key other than the update keys, the managed configuration owns the device … the in-app configuration window becomes read-only". |
| 13 | Mobile has no 3P | **Confirmed** | Feature matrix: "Mobile ✓ / —". |
| 14 | Windows MSIX `rootfs.vhdx` bug | **Confirmed (historic)** | anthropics/claude-code#55946, closed not-planned; v1.28929.0 (2026-08-11) fixed MSIX/roaming-profile persistence. No pinned build is recommended; "Leave auto-update enabled". Gateway-relevant fixes: v1.34493.1 prompt caching on gateways; v1.26832.0 idle-timeout on custom gateway Code sessions. |
| 15 | Org "Approved Models" interacts with the picker | **Contradicted** | No such key in 3P. `inferenceModels` is the only allow-list; "User identity: Local device identity only" — claude.ai org settings do not apply. |

## Where the per-user config lives (macOS)

`~/Library/Application Support/Claude-3p/configLibrary/<id>.json` plus `_meta.json`
(`appliedId`, `entries[{id,name}]`). Flat keys, same names as the MDM keys. "Ignored when a
managed profile is present." Logs: `~/Library/Logs/Claude-3p/main.log`.

Ollama's `ollama launch claude-desktop` **[secondary, source: `cmd/launch/claude_desktop.go`]**
additionally writes `"deploymentMode": "3p"` into `claude_desktop_config.json` under both
`Claude/` and `Claude-3p/` (this key is not in Anthropic's reference), quits the app via
AppleScript, re-applies the profile *after* the app has exited ("Claude persists settings
while shutting down"), then relaunches. It supports `--restore`. Its profile:
`inferenceProvider: gateway`, `inferenceGatewayBaseUrl: http://127.0.0.1:11435`,
`inferenceGatewayApiKey`, `inferenceGatewayAuthScheme: bearer`, `deploymentDisplayName`,
`chatTabEnabled: true`, `disableDeploymentModeChooser`, `coworkEgressAllowedHosts: ["*"]`,
telemetry off, and it *deletes* `inferenceModels` (relying on `/v1/models`).

## What a gateway must actually do

- Serve `POST /v1/messages` — streaming SSE and tool use required; forward `ping` events;
  Desktop runs a **300 s byte-watchdog** on the stream.
- `GET /v1/models` is optional. Returning an error is the fast path to the pinned list;
  hanging is the slow path. Return `data[].id` containing "claude" or add
  `anthropic_family_tier` if implemented.
- `POST /v1/messages/count_tokens` and `HEAD /api/hello` are used by the Code surface.
- Auth arrives as `Authorization: Bearer <key>` (default) or `x-api-key`. Per-session
  headers only via a credential-helper script; static ones via `inferenceCustomHeaders`.
- Cowork runs in a VM; its egress is governed by `coworkEgressAllowedHosts`, which must
  admit the gateway host.
- Besides the gateway, Desktop needs egress to `downloads.claude.ai` (VM bundle) unless
  the offline installer is used.

## Unresolved (to be settled by the live contract spike)

- Whether Desktop accepts `http://` and/or `localhost` in `inferenceGatewayBaseUrl`
  (no primary statement; loopback `http://127.0.0.1` has a shipping vendor precedent).
- Whether Chat/Cowork requests carry `x-claude-code-session-id`, and whether `metadata`
  is populated.
- Which endpoint the **Test connection** button probes.
- How the UI renders a non-2xx gateway body — specifically whether `error.message` is
  shown and whether a `503` is silently retried.
- Semantics of the undocumented `deploymentMode` key.
- Whether `configLibrary/<id>.json` accepts the nested "v2" bootstrap schema (flat keys are
  documented and are the safe choice).
