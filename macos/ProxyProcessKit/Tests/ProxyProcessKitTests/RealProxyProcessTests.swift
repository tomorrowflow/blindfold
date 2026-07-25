import Testing
import Foundation
#if canImport(Glibc)
import Glibc
#elseif canImport(Darwin)
import Darwin
#endif
import BlindfoldCore
@testable import ProxyProcessKit

/// Issue #219's own AC: "a regression test covers the slow-start path with a stub child
/// that delays before answering, so this cannot silently return". Every prior slice on
/// this issue verified `RealProxyProcess`/`RealProxyProcessLauncher` against a real spawned
/// child only in a disposable throwaway SwiftPM package outside the repo -- never a
/// persisted regression, because this file's production code lived inside
/// `BlindfoldMenuBar`'s single executable target, which fails to build in-sandbox at
/// `main.swift`'s `import SwiftUI`. Splitting it into `ProxyProcessKit` (a plain-Foundation
/// target, no SwiftUI/AppKit) lets this run for real, every iteration, on Linux.
@Test func slowChildStaysNotExitedWhileSleepingThenExitsCleanlyOnceItFinishes() throws {
    let launcher = RealProxyProcessLauncher()

    let process = launcher.launch(exePath: "/bin/sh", args: ["-c", "sleep 0.4; exit 0"], environment: [:])

    #expect(process.hasExited == false)

    let deadline = Date().addingTimeInterval(5)
    while !process.hasExited, Date() < deadline {
        Thread.sleep(forTimeInterval: 0.05)
    }

    #expect(process.hasExited == true)
    #expect(process.exitCode == 0)
    #expect(process.terminationSignal == nil)
}

/// Issue #219 AC: "a child that emits a large volume of stderr during startup does not
/// destabilize the supervisor". A real child (standing in for the GLiNER cascade's
/// tqdm-style progress spam) writes well past the 64KB cap in one continuous burst --
/// this must neither hang the launcher (a full, undrained pipe blocking the child's
/// write forever) nor let `standardErrorText` grow unbounded, and the surviving text
/// must be the *tail* of the stream, since `StartupRefusalReason.scrub` only ever needs
/// to recognize a keyword near the end of a traceback, never the full transcript.
@Test func chattyChildStderrStaysCappedAtTheTailAndDoesNotHangTheLauncher() throws {
    let launcher = RealProxyProcessLauncher()

    let process = launcher.launch(
        exePath: "/bin/sh",
        args: ["-c", "yes 'loading model shard progress ...' | head -n 6000 1>&2"],
        environment: [:]
    )

    let deadline = Date().addingTimeInterval(10)
    while !process.hasExited, Date() < deadline {
        Thread.sleep(forTimeInterval: 0.05)
    }
    // `hasExited` (derived from `Process.isRunning`) can flip true a hair before the
    // readability handler drains the pipe's last buffered chunk -- a real race between
    // waitpid and the GCD dispatch source, not a bug this issue is about. A short settle
    // avoids asserting on that inherently-racy last few bytes.
    Thread.sleep(forTimeInterval: 0.1)

    #expect(process.hasExited == true)
    #expect(process.exitCode == 0)
    #expect(process.standardErrorText.utf8.count <= 64 * 1024)
    #expect(process.standardErrorText.contains("loading model shard progress ..."))
}

/// Issue #219: this issue's own reported symptom is an *OS-level* kill mid-slow-start
/// ("no child process, port free") -- not our own `kill()` (that sends SIGTERM via
/// `Process.terminate()`, which this sandbox's test harness process turns out to inherit
/// as ignored across `exec` for every spawned child, making it untestable in-process here;
/// SIGKILL, unlike SIGTERM, can never be blocked or ignored by any process, so it's the
/// faithful way to simulate "something outside this app kills the child" in-sandbox).
/// `terminationSignal` must be read straight from `Process.terminationReason`/
/// `terminationStatus`, never guessed at from stderr, which a signal kill typically
/// leaves empty.
@Test func osLevelSignalKillIsReportedAsTerminationSignalNotAsAnExitCode() throws {
    let launcher = RealProxyProcessLauncher()

    let process = launcher.launch(exePath: "/bin/sleep", args: ["30"], environment: [:])
    guard let real = process as? RealProxyProcess else {
        Issue.record("expected a RealProxyProcess")
        return
    }
    #expect(real.hasExited == false)

    #expect(kill(real.processIdentifier, SIGKILL) == 0)

    let deadline = Date().addingTimeInterval(5)
    while !real.hasExited, Date() < deadline {
        Thread.sleep(forTimeInterval: 0.05)
    }

    #expect(real.hasExited == true)
    #expect(real.terminationSignal == SIGKILL)
}

/// Issue #219 AC "stdout is still never captured or surfaced": a chatty child (standing
/// in for the GLiNER cascade's own uvicorn/tqdm stdout progress spam) writing a large,
/// continuous burst of stdout must exit cleanly -- never an uncaught `BrokenPipeError`
/// from writing into a pipe whose reader went away, the concrete failure mode this
/// issue's stdout-to-null-device fix (`process.standardOutput = FileHandle.nullDevice`)
/// closed. Nothing reads this child's stdout at all; the launcher must not care.
@Test func chattyStdoutChildExitsCleanlyBecauseStdoutIsNeverACapturedPipe() throws {
    let launcher = RealProxyProcessLauncher()

    let process = launcher.launch(
        exePath: "/bin/sh",
        args: ["-c", "yes 'stdout progress' | head -n 6000"],
        environment: [:]
    )

    let deadline = Date().addingTimeInterval(10)
    while !process.hasExited, Date() < deadline {
        Thread.sleep(forTimeInterval: 0.05)
    }

    #expect(process.hasExited == true)
    #expect(process.exitCode == 0)
    #expect(process.terminationSignal == nil)
}
