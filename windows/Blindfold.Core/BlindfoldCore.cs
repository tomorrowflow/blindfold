namespace Blindfold.Core;

/// <summary>
/// Blindfold.Core — the cross-platform, WinForms-free logic core of the Blindfold
/// Windows tray app (ADR-0041 / ADR-0042), the Windows analog of the Swift
/// <c>BlindfoldCore</c> (macos/BlindfoldCore).
///
/// All privacy-relevant logic lands here so it is unit-testable inside Sandcastle's
/// Linux sandbox: the <c>/v1/status</c> client + five-state reducer, the presentation
/// rules, and the loopback-only egress guard (this core only ever talks to loopback,
/// and never persists or logs an entity value). Both cores are held to the same
/// language-neutral golden-vector contract (ADR-0041). The WinForms tray shell stays a
/// thin, logic-free binding layer built and gated separately on the hosted
/// platform-verify runner (ADR-0042).
/// </summary>
public static class BlindfoldCore
{
    /// <summary>
    /// Tracer marker proving the project compiles, links, and tests inside the sandbox.
    /// Replaced by real logic as the ADR-0041 slices land (issue #194).
    /// </summary>
    public const string Name = "Blindfold.Core";
}
