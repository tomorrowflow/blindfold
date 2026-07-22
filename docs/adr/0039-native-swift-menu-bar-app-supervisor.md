# ADR-0039: macOS front door — a native Swift menu bar app that supervises the proxy

**Status:** Accepted
**Date:** 2026-07-22

## Context

Blindfold's only supported launch today is `blindfold serve` (ADR-0021: loopback bind,
root-token guard). That's correct for headless/dev/Linux use, but a Mac user who wants
Blindfold in front of their LLM tools should never have to touch a terminal. We want a
macOS menu bar item — modeled on the oMLX tray item — that shows protection status at a
glance and is the Mac user's front door.

Two shapes were possible: a *satellite* (status + shortcuts only, proxy launched
separately) or a *supervisor* (the menu bar app launches and owns the proxy). We chose
supervisor — that is what makes a menu bar genuinely useful on a Mac. The supervisor
choice creates a process pair the glossary now names: the **menu bar app** (GUI
supervisor, holds no entity data) and the **proxy** (the loopback interceptor it runs as
a child).

## Decision

We will build the menu bar app as a **native Swift app** (SwiftUI `MenuBarExtra` /
AppKit `NSStatusItem`), shipped as a signed, self-contained `.app`, structured so the
privacy-relevant logic is machine-verifiable (see ADR-0040).

- **Artifact layering inverts ADR-0026.** ADR-0026 vendors the React bundle *into* the
  Python package and ships a wheel. Here the top-level distributable is the `.app`, and
  the layering is **`.app` ⊃ frozen-proxy ⊃ `ui_dist`**: the proxy is frozen
  (PyInstaller onefile) into a self-contained binary embedded in the bundle, the React
  `ui_dist` rides inside the frozen proxy unchanged (still served at `/ui/`), and Swift
  spawns the frozen proxy as its child. The target Mac needs **no Python, no `uv`, no
  Node** — the same no-toolchain-on-target promise as ADR-0021/0026, in a new direction.
- **`macos/` is a new top-level dir** (sibling of `frontend/` and `src/`) holding the
  Xcode/SwiftPM project. Swift/Xcode is a **dev-and-CI-only toolchain**, exactly as Node
  is for `frontend/` — never required on the end user's machine.
- **The wheel and `blindfold serve` stay first-class.** The `.app` is an *additional*
  front door for Mac users, not a replacement — Linux/server/headless users and
  Sandcastle's own Python tests never touch Swift.
- **The app is a supervisor and shortcut surface, never a data plane.** It holds no
  entity data; it talks to the proxy only over loopback (`/v1/status` + the ADR-0038
  control call) and deep-links into `/ui/*`. Its icon renders a five-state machine —
  **Stopped / Starting / Protected / Degraded / Refused** (fed by proxy liveness the app
  owns directly + `/v1/status`'s `state`), plus the ADR-0038 alarm state. The
  **Refused** state (startup guard tripped: root token / non-loopback L3) is the GUI
  surface for the refusal that previously only printed to a terminal — the menu shows the
  scrubbed reason and a remedy (open Settings / open logs).

## Consequences

- Menu elements to build (mapped from the oMLX reference): state header; Start/Stop
  Proxy; two clickable count deep-links (`review_inbox.pending` → `/ui/inbox`,
  `blocks.count` → `/ui/status`); a `Finish setup →` deep-link shown when
  `empty_store:true`; Open Blindfold (`/ui/`); Settings (`/ui/settings`); the ADR-0038
  Unprotected-mode submenu; About; Quit (stops the child proxy first). Dropped: "Chat"
  (no analog). Deferred: in-app update channel (no release/update infrastructure yet).
- A second language and toolchain enter the repo. Verification cannot use the existing
  Linux sandbox for the UI layer — addressed by ADR-0040 (a testable Swift core +
  self-hosted macOS runner gate).
- Freezing the proxy (PyInstaller) adds a release step and a failure mode (a frozen build
  that misses a dependency); this is dev/CI-time only, off the target's install path.
- Code-signing and notarization become release concerns; distribution (DMG / update
  channel) is out of scope for the first cut.

## Alternatives considered

- **Satellite (status + shortcuts only)** — rejected: a much thinner, lower-value widget;
  a Mac user still needs a terminal to actually run Blindfold.
- **Python + `rumps`, `.app` via py2app/Briefcase** — rejected: keeps everything in one
  language and one release, but the tray UI can't match the native polish of the oMLX
  reference (SF Symbols, submenus, ⌘-glyphs, first-class notifications/launch-at-login),
  and Python `.app` bundling is itself fiddly. The `/v1/status` contract keeps the shell
  swappable, so this stays a fallback if the Swift toolchain cost outweighs the polish.
- **Require system Python on the target** — rejected outright: breaks the
  no-toolchain-on-target promise that motivated the supervisor + `.app` in the first place.
