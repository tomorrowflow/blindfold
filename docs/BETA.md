# Blindfold Beta — Scope, Trust Boundary, Known Limitations

This document is the honest contract for the invited beta: what this release is
tested for, where its trust boundary sits, and which sharp edges are known and
tracked. Read the **Trust boundary** and **What the guarantee is — and is not**
sections before sending real traffic through the proxy.

## Beta scope

The beta is deliberately narrow. Inside scope — and verified end to end:

- **macOS**, single machine, single user.
- **Claude Code** as the client, connected via `ANTHROPIC_BASE_URL` (the Connect
  page renders the exact snippet for your install).
- The **embedded SQLite store** (the default) with the local mapping cipher;
  the opt-in shared Postgres backend works but is not the beta's focus.

Outside scope for this beta (tracked, not forgotten — see
[Known limitations](#known-limitations)): Windows, OpenAI-protocol clients,
Codex, and clients whose base URL cannot be redirected (e.g. Claude Desktop).

## Install & connect

- Install and first run: follow the **Quickstart** and **Management app build**
  sections of the [README](../README.md). Setup provisions the default
  detection model (~197 MB download) and ends with a **restart** — that restart
  is expected, not a failure; detection activates on the next process start.
- Connect Claude Code: open the management app's **Connect** page and use the
  `ANTHROPIC_BASE_URL` snippet it renders. Then verify *inside Claude Code*
  (`/status`) that the base URL actually points at the proxy — an env var set
  in the wrong shell profile is the most common silent miss.

## Trust boundary

Blindfold's proxy and management app run **unauthenticated on localhost, by
design**. The deployment model is a single-owner machine: the proxy is always
local and serves one person; nothing about it is multi-tenant.

Concretely, that means:

- **Anything on this machine that can reach the proxy port can send requests
  using your configured provider credential** — and will receive **restored
  responses containing real values**. The proxy cannot tell you apart from
  another local process.
- The **management app is equally localhost-trusted**: local access to its port
  is access to the review inbox and, for the role-gated views, to re-identify
  actions under your identity.
- The **mapping store is encrypted at rest**, but the running proxy necessarily
  holds the key material needed to blindfold and restore. Disk encryption
  protects the store when the process is not running; it does not protect
  against code running as your user while it is.

If your machine is shared, or you run untrusted local software, this trust
boundary is the first thing to evaluate. An application-wide authentication
layer is planned (issue #38) but is explicitly **not** part of the beta.

## What the guarantee is — and is not

**What is verified.** The make-or-break property — *no real entity value leaves
the machine* — is checked three ways: every hop of every outbound request is
rewritten before egress; a post-restore **verify pass** confirms the response
contains no unresolved surrogates; and a failure anywhere in that path **fails
closed** (the request is blocked with a scrubbed error rather than sent
unprotected). The current build passed six consecutive live verification
sessions — full agentic Claude Code sessions driven through the proxy — with
zero real values found in any captured outbound payload.

**What it is not.**

- **Reversible pseudonymization, not anonymization.** The data leaving your
  machine is safe while (a) surrogates carry no identifying signal and (b) the
  local mapping never leaks. The mapping is the crown-jewel secret; treat the
  store directory and backups accordingly.
- **Structure can still whisper.** Surrogates are relationship-consistent by
  design (that is what keeps the model's reasoning intact), so an adversary
  with strong side knowledge could in principle draw inferences from the
  *shape* of a conversation even with every name replaced. A systematic
  adversarial evaluation of this residual risk is planned (issue #78).
- **Novel-entity detection is probabilistic.** Entities in your curated graph
  are replaced deterministically. *Novel* entities — names the graph has never
  seen — are caught by a detection cascade that is measured, not perfect. The
  acceptance gate the beta shipped under allows a small per-session budget of
  false positives (harmless: a fake name appears where none was needed, and
  the item lands in the review inbox for one-click dismissal) and is designed
  to over-protect rather than under-protect. Expect the review inbox to be
  part of normal use, especially in the first sessions while your graph is
  still thin.

## Known limitations

Each of these is a tracked issue, stated here so nobody discovers it the hard
way:

| Limitation | Status |
|---|---|
| **Claude Code only.** Codex now requires the `/v1/responses` endpoint, which Blindfold does not yet serve; OpenAI-SDK clients work at the transport level but are not beta-verified. | #263 |
| **macOS only.** The Windows tray supervisor has a known key-injection defect that needs real hardware to diagnose. | #236 |
| **Non-redirectable clients unsupported.** Claude Desktop and similar closed clients cannot be pointed at a local base URL today. | #62 |
| **Unsigned app.** Until code signing and notarization land, the menu-bar app is ad-hoc signed: install from source per the README, or expect Gatekeeper friction on a downloaded bundle. | #198 |
| **Restart after Setup.** Detection activation takes effect on the next process start; Setup's final restart instruction is by design. | ADR-0034 |
| **Added latency.** Blindfolding adds per-request latency dominated by novel-entity handling; a formal per-hop cost model is planned. Sessions feel slower than direct connections, most noticeably on the first requests of a session. | #58 |
| **Rare blocked requests on phone-shaped strings.** Digit runs that look like phone numbers can, in narrow cases, block a request in the default configuration; the default posture is under active decision. Blocked requests self-describe and retry cleanly. | #278 |
| **Brand-new Claude Code capabilities may lag.** Request headers are currently forwarded from a fixed allowlist, so a header introduced by a future Claude Code release could be stripped until the open-prefix decision lands. | #266 |

## Feedback

File a GitHub issue in this repository. For suspected privacy defects — anything
where you believe a real value reached the provider — please include the
processing-trace excerpt from the management app rather than raw session
content, and flag the issue as privacy-relevant so it is triaged first.
