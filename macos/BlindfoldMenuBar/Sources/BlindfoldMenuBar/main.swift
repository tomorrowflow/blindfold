import Foundation
import SwiftUI
import AppKit
import BlindfoldCore

/// Session-scoped single-instance guard path (issue #213, ADR-0039/0041's macOS-appropriate
/// equivalent of Windows' named mutex): under the same app-data convention
/// `~/Library/Application Support/blindfold/` already used by `resolve_data_dir`
/// (`src/blindfold/config.py`), so a second menu bar app launch can't spawn a second proxy
/// and collide on `StatusPollingModel.proxyPort`.
private let singleInstanceLockPath = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/blindfold/menubar-single-instance.lock")
    .path

/// Held for the whole process's lifetime -- releasing (deliberately or via process death)
/// is what lets a *later* launch acquire it, so this must not be a short-lived local.
private let singleInstanceGuard = SingleInstanceGuard()

/// The supervisor-owned launch environment store (ADR-0044): a dedicated UserDefaults
/// suite under the app's own bundle identifier, distinct from `.standard` so it can never
/// collide with an unrelated preference. Not `private`: `BlindfoldMenuBarApp.swift`'s
/// construction site shares it.
let launchEnvironmentStore = LaunchEnvironmentStore(suiteName: "dev.tomorrowflow.blindfold.launchEnvironment")

/// Reduces the real ambient environment plus the launch environment store's held
/// `BLINDFOLD_*` values into the child's actual environment (ADR-0044) -- the one place
/// both supervisor-construction sites (the real app and `--smoke-launch-full`) build the
/// value `ProxySupervisor` hands verbatim to the launcher.
func childEnvironment() -> [String: String] {
    LaunchEnvironment.reduce(ambient: ProcessInfo.processInfo.environment, launchEnvironment: launchEnvironmentStore.values())
}

/// Headless-safe entry point (`.sandcastle/mac-verify-prompt.md`'s contract, mirroring
/// `windows/Blindfold.Tray/Program.cs`'s `--smoke-test`): constructs the loopback wiring
/// and exits 0 without an `NSApplication` run loop or any interactive dialog, so it can't
/// block the hosted platform-verify runner. Deliberately does not construct
/// `StatusPollingModel` -- unlike a WinForms `Timer`, a Swift `Task` starts running the
/// moment it's created, with or without a run loop pumping, so building the real poll loop
/// here would leave a live network task behind at process exit; constructing `StatusClient`
/// directly proves the same wiring (the egress guard, the core's reduction calls) without that.
func runSmokeTest() -> Int32 {
    do {
        let baseURL = URL(string: "http://127.0.0.1:\(StatusPollingModel.proxyPort)/v1/status")!
        _ = try StatusClient(baseURL: baseURL, fetcher: URLSessionStatusFetching())
        let unprotectedModeURL = URL(string: "http://127.0.0.1:\(StatusPollingModel.proxyPort)/v1/unprotected-mode")!
        _ = try UnprotectedModeControlClient(baseURL: unprotectedModeURL, sender: URLSessionUnprotectedModeSending())
        _ = MenuBarPresentation.iconState(for: .stopped)
        _ = MenuBarPresentation.headerText(for: .stopped)
        print("Blindfold menu bar app (macOS): smoke-test OK")
        return 0
    } catch {
        FileHandle.standardError.write(Data("--smoke-test failed: \(error)\n".utf8))
        return 1
    }
}

/// `--smoke-launch-full` (issue #213, mirrors `windows/Blindfold.Tray/Program.cs`'s
/// `RunSmokeLaunchFull`): drives the real `ProxySupervisor` + `StatusClient` poll loop
/// headlessly -- starts the frozen (or dev-mode-fallback) proxy, polls `/v1/status` until
/// `AppStateMachine` reduces to a non-error terminal state (Protected OR Degraded) or a
/// bounded timeout elapses, then stops the child. Exit 0 once the proxy has spawned,
/// answered `/v1/status` at least once, and been reduced through the supervisor -- the
/// menu bar app's actual supervision contract. Deliberately does NOT require Protected:
/// that depends on the L3 dependency being configured via the ambient environment, which
/// platform-verify.yml's hosted runner doesn't set up -- the exact trap the Windows
/// equivalent fell into across several attempts (issue #197). A Refused startup or a
/// timeout with no successful poll both exit 1 with a scrubbed/generic diagnostic on
/// stderr, never raw process output.
func runSmokeLaunchFull() async -> Int32 {
    let bundledExecutableDirectory = Bundle.main.executableURL?
        .deletingLastPathComponent()
        .path ?? FileManager.default.currentDirectoryPath
    let located = ProxyBinaryLocator.locate(
        bundledExecutableDirectory: bundledExecutableDirectory,
        proxyHost: "127.0.0.1",
        proxyPort: StatusPollingModel.proxyPort,
        fileExists: { FileManager.default.fileExists(atPath: $0) }
    )
    let supervisor = ProxySupervisor(
        launcher: RealProxyProcessLauncher(),
        exePath: located.exePath,
        args: located.args,
        environment: childEnvironment()
    )

    let statusClient: StatusClient
    do {
        statusClient = try StatusClient(
            baseURL: URL(string: "http://127.0.0.1:\(StatusPollingModel.proxyPort)/v1/status")!,
            fetcher: URLSessionStatusFetching()
        )
    } catch {
        FileHandle.standardError.write(Data("--smoke-launch-full: failed to construct the status client: \(error)\n".utf8))
        return 1
    }

    supervisor.start()

    let deadline = Date().addingTimeInterval(30)
    var lastStatus: StatusPayload?
    var lastPollErrorDescription: String?
    var pollAttempts = 0

    while Date() < deadline {
        if case let .refused(reason) = supervisor.currentLiveness() {
            FileHandle.standardError.write(Data("--smoke-launch-full: proxy refused to start: \(reason)\n".utf8))
            return 1
        }

        pollAttempts += 1
        do {
            let status = try await statusClient.poll()
            lastStatus = status
            supervisor.notifyHealthy()
            let state = AppStateMachine.reduce(liveness: supervisor.currentLiveness(), status: status)
            // A successful poll means the proxy spawned, bound its port, answered
            // /v1/status, and the supervisor+reducer wired it through -- the app's actual
            // supervision contract. Reduce() maps a running proxy with any status to
            // Protected or Degraded (never the error states Refused/Stopped/Starting), so
            // either terminal is success here. Protected-vs-Degraded turns only on L3
            // dependency health, asserted one-hop elsewhere (see the doc comment above).
            if state == .protected || state == .degraded {
                supervisor.stop()
                return 0
            }
        } catch {
            lastPollErrorDescription = "\(error)"
        }

        try? await Task.sleep(nanoseconds: 500_000_000)
    }

    if let lastStatus {
        FileHandle.standardError.write(Data(
            ("--smoke-launch-full: proxy never reduced to a terminal state within the timeout -- "
                + "last /v1/status: state=\"\(lastStatus.state)\", dependencies_down=\(lastStatus.dependenciesDown) "
                + "(\(pollAttempts) polls attempted)\n").utf8
        ))
    } else {
        FileHandle.standardError.write(Data(
            ("--smoke-launch-full: proxy never answered /v1/status within the timeout -- "
                + "unreachable in \(pollAttempts) attempts; last error: \(lastPollErrorDescription ?? "<none>")\n").utf8
        ))
    }
    supervisor.stop()
    return 1
}

/// A plain `var` captured by the `Task` closure below would be a Swift 6 strict-concurrency
/// error (`sending value of non-Sendable type risks causing data races`, the same class of
/// error `StatusPollingModel`'s poll loop hit -- see commit 187aa0c) -- the semaphore
/// establishes a happens-before between the write and the read, but the compiler can't see
/// that, so the write goes through a `@unchecked Sendable` box instead.
private final class ExitCodeBox: @unchecked Sendable {
    var value: Int32 = 1
}

/// Bridges `runSmokeLaunchFull`'s async poll loop into the synchronous top-level entry
/// point (this file has no `@main`/async support -- plain top-level code).
func runSmokeLaunchFullSync() -> Int32 {
    let box = ExitCodeBox()
    let semaphore = DispatchSemaphore(value: 0)
    Task {
        box.value = await runSmokeLaunchFull()
        semaphore.signal()
    }
    semaphore.wait()
    return box.value
}

if CommandLine.arguments.contains("--smoke-test") {
    exit(runSmokeTest())
} else if CommandLine.arguments.contains("--smoke-launch-full") {
    exit(runSmokeLaunchFullSync())
} else {
    let lockDirectory = (singleInstanceLockPath as NSString).deletingLastPathComponent
    try? FileManager.default.createDirectory(atPath: lockDirectory, withIntermediateDirectories: true)

    guard singleInstanceGuard.acquire(lockFilePath: singleInstanceLockPath) else {
        let alert = NSAlert()
        alert.messageText = "Blindfold"
        alert.informativeText = "Blindfold is already running."
        alert.alertStyle = .informational
        alert.runModal()
        exit(1)
    }

    BlindfoldMenuBarApp.main()
}
