# ADR-0041: Windows front door — a native .NET tray app that supervises the proxy

**Status:** Accepted
**Date:** 2026-07-23

## Context

ADR-0039 gave macOS a native front door: a menu bar app that launches and owns the
**proxy**. The Python core is already essentially cross-platform — the only local-LLM
coupling ("oMLX") is an OpenAI-compatible HTTP client, GLiNER runs on ONNX-CPU (Windows
wheels exist), and the whole request path is a loopback ASGI app. So the Windows gap is
not the engine; it is the desktop front door. Windows users deserve the same
zero-terminal experience Mac users get from the menu bar app.

Adding Windows forces a choice ADR-0039 never had to make: **two native front doors, or
one cross-platform shell?** We keep going native per platform. The front door is
deliberately thin and logic-free (ADR-0040), so "maintaining two" is really maintaining
two small binding layers over one tiny logic contract — and ADR-0039 already paid a
toolchain for native polish on the strength of that reasoning. The `/v1/status` contract
was built to keep the front door swappable; the Windows tray app is a second consumer of
exactly that seam.

The **supervisor** process pair from ADR-0039 is now the canonical, platform-neutral
concept (CONTEXT.md): the supervisor renders as the **menu bar app** on macOS and the
**tray app** on Windows. Both hold no entity data; both talk to the proxy only over
loopback and deep-link into `/ui/*`.

## Decision

We will build the Windows front door as a **native .NET (LTS) tray app using WinForms
`NotifyIcon`**, published self-contained single-file, structured so the privacy-relevant
logic is machine-verifiable — the Windows sibling of ADR-0039/0040.

- **WinForms `NotifyIcon`, not WPF/WinUI.** There is no app window to justify a XAML
  framework — the management UI is the proxy-served `/ui/*` SPA, and the tray app only
  needs a tray icon, a context menu, a child `Process`, and `ShellExecute` to open the
  browser. `NotifyIcon` is the smallest, most battle-tested tray API and the exact analog
  of the thin Swift binding layer.

- **Artifact layering mirrors ADR-0039** — `tray-app ⊃ frozen-proxy ⊃ ui_dist`. The proxy
  is frozen with the existing PyInstaller onefile spec into `blindfold-proxy.exe`; the
  React `ui_dist` rides inside it unchanged (served at `/ui/`); the tray app spawns it as
  a child. The target Windows machine needs **no Python, no `uv`, no Node**.

- **Anti-drift: parallel C# core + a language-neutral golden-vector fixture (extends
  ADR-0040).** The risk-bearing logic — the `/v1/status` client, the five-state reducer,
  the icon/header presentation rules, the loopback-egress guard — is re-authored in a
  testable `Blindfold.Core` C# class library. To stop the Swift and C# cores from
  drifting, the *behavior spec* (reducer truth table `liveness × status → state`,
  presentation strings, loopback accept/reject cases) is extracted into a repo-checked
  JSON fixture that **both** the Swift and C# test suites assert against. A change to the
  contract breaks both suites until both cores are updated.

- **Supervisor contract, defined once, cross-platform** (spawn/stop is greenfield on
  macOS too):
  - liveness: a spawned child is `running`; the reducer shows **Starting** until the
    first `/v1/status` lands.
  - child **early-exit / non-zero before first healthy status** → **Refused**, capturing
    the *scrubbed* stderr reason (root token, non-loopback L3, port in use) — the GUI
    surface for a startup-guard refusal.
  - child **crash after being healthy** → **Stopped, no auto-restart.** A privacy tool
    fails visible, not silently respawning; auto-restart-with-backoff is a later ADR if
    it proves needed.
  - **single-instance** front door (named mutex) so two supervisors can't both spawn a
    proxy and collide on port 25463; a foreign process already on the port makes the
    child's bind fail → surfaces as Refused ("port in use").

- **The wheel, `blindfold serve`, and the macOS `.app` stay first-class.** The tray app is
  an *additional* front door for Windows users; Linux/server/headless users and
  Sandcastle's own Python tests never touch .NET.

## Consequences

- **Windows verification runs on a GitHub-hosted `windows-latest` runner** via the
  platform-verify gate (ADR-0042) — not a self-hosted sibling of the macOS runner.
  PyInstaller cannot cross-compile, so the frozen `blindfold-proxy.exe`, the WinForms
  build, and the smoke-launch happen there. The **C# core's golden-vector tests run in the
  Linux sandbox** (.NET is cross-platform), not on the runner. The .NET SDK is a
  **dev-and-CI-only toolchain**, exactly as Swift and Node are — never on the end user's
  machine.
- **Menu elements** match the macOS reference (ADR-0039): state header; Start/Stop Proxy;
  count deep-links (`review_inbox.pending` → `/ui/inbox`, `blocks.count` → `/ui/status`);
  `Finish setup →` when `empty_store:true`; Open Blindfold (`/ui/`); Settings
  (`/ui/settings`); the ADR-0038 Unprotected-mode submenu; About; Quit (stops the child
  first). ⌘-glyphs/SF Symbols become Windows accelerators/icons — rendering only.
- **OS integration:** data dir `%LOCALAPPDATA%\Blindfold` (Local, never Roaming — the
  store and the ~197 MB GLiNER model must not sync); this adds a `win32` branch to
  `resolve_data_dir()` (today Windows falls through to XDG). Autostart via a
  `HKCU\...\CurrentVersion\Run` toggle, default off. The ADR-0038 auto-revert
  notification uses a `NotifyIcon` balloon tip in the first cut (works unpackaged),
  upgraded to a WinRT toast once an installer can register an AUMID.
- **Distribution is a portable side-by-side folder in the first cut** — `blindfold.exe`
  and `blindfold-proxy.exe` in one directory, the tray discovering the proxy by relative
  path. An installer (WiX/Inno), Authenticode signing (the notarization analog; without
  it SmartScreen warns), and an update channel are **deferred** — the same posture
  ADR-0039 took for DMG/notarization.
- **Unprotected-mode is unchanged:** the submenu calls the proxy control endpoints, the
  capability is off by default, and the flag + expiry timer live in the proxy so the
  guarantee and auto-revert survive a supervisor crash (ADR-0038). The tray app enforces
  nothing locally.

## Alternatives considered

- **One cross-platform shell** (Python+pystray, .NET+Avalonia, or Tauri) superseding the
  Swift app — rejected: sacrifices the native macOS polish ADR-0039 just paid for and
  reopens a settled decision, for a maintenance saving the thin-shell architecture already
  minimizes.
- **WPF + `H.NotifyIcon` or WinUI 3** — rejected: they earn their weight only with a real
  app window, which we don't have; WinUI's tray story is second-class and pushes toward
  MSIX. Unnecessary machinery over `NotifyIcon`.
- **Python + `pystray` bundled into the frozen proxy** — rejected for the same reason
  ADR-0039 rejected rumps on macOS (can't match native tray polish, muddies the
  supervisor/proxy process separation), though it remains the low-effort fallback the
  `/v1/status` seam keeps available.
- **Embed-and-extract the proxy exe** as a .NET resource — rejected for the first cut:
  closest to the mac single-artifact feel but adds extraction complexity and
  antivirus/SmartScreen false-positive risk over a plain side-by-side folder.
- **Installer + Authenticode signing in the first cut** — deferred, not rejected: matches
  ADR-0039's minimal-first-cut posture; revisited in a later distribution ADR.
