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

/// Issue #414's own root cause: a spawned child left in the launcher's own process group
/// means `kill(-pgid, ...)` would hit the launcher (this app) too, so nothing before this
/// issue could ever signal "the child and everything it forked" without also signalling
/// itself. Placing the child at the head of its *own* new group (pgid == its own pid) is
/// what `kill()` (below) needs in order to terminate the whole process tree the child may
/// have spawned (e.g. an ASGI worker) without touching the launcher.
@Test func spawnedChildIsTheLeaderOfItsOwnNewProcessGroup() throws {
    let launcher = RealProxyProcessLauncher()

    let process = launcher.launch(exePath: "/bin/sleep", args: ["1"], environment: [:])
    guard let real = process as? RealProxyProcess else {
        Issue.record("expected a RealProxyProcess")
        return
    }

    #expect(getpgid(real.processIdentifier) == real.processIdentifier)
    #expect(real.processIdentifier != getpgrp())

    real.kill()
}

/// Whether `port` can be bound right now -- the AC's own check ("no listener remains on
/// the port afterwards"), done directly at the socket layer rather than shelling out to a
/// second tool, and cleaned up immediately so it never itself leaves anything listening.
private func canBindLoopback(port: UInt16) -> Bool {
    let fd = socket(AF_INET, Int32(SOCK_STREAM.rawValue), 0)
    guard fd >= 0 else { return false }
    defer { close(fd) }

    var reuse: Int32 = 1
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))

    var address = sockaddr_in()
    address.sin_family = sa_family_t(AF_INET)
    address.sin_port = port.bigEndian
    address.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))

    let result = withUnsafePointer(to: &address) { pointer -> Int32 in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockaddrPointer in
            bind(fd, sockaddrPointer, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    return result == 0
}

/// Issue #414's own AC: "the whole spawned process group is terminated, not only the
/// direct child -- covered by a test that asserts no listener remains on the port
/// afterwards". The direct child here is a shell that backgrounds a grandchild Python
/// listener and then `wait`s -- standing in for `blindfold-proxy`'s parent-plus-ASGI-worker
/// shape the issue itself names. `kill()` must reach the grandchild too: killing only the
/// direct child (the pre-fix behaviour) leaves the grandchild bound to the port forever,
/// exactly the "two proxy processes survived and kept holding the port" symptom reported.
///
@Test func killTerminatesTheWholeProcessGroupSoNoGrandchildIsLeftHoldingThePort() throws {
    let port: UInt16 = 25998
    #expect(canBindLoopback(port: port), "test port must be free before the test starts")

    let launcher = RealProxyProcessLauncher()
    let process = launcher.launch(
        exePath: "/bin/sh",
        args: [
            "-c",
            """
            python3 -c "import socket,time
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('127.0.0.1', \(port)))
            s.listen(1)
            time.sleep(30)" &
            wait
            """,
        ],
        environment: [:]
    )

    // Give the grandchild time to actually bind before asserting the port is held.
    let bindDeadline = Date().addingTimeInterval(5)
    while canBindLoopback(port: port), Date() < bindDeadline {
        Thread.sleep(forTimeInterval: 0.05)
    }
    #expect(canBindLoopback(port: port) == false, "the grandchild must be holding the port before kill() is exercised")

    process.kill()

    let releaseDeadline = Date().addingTimeInterval(5)
    while !canBindLoopback(port: port), Date() < releaseDeadline {
        Thread.sleep(forTimeInterval: 0.05)
    }
    #expect(canBindLoopback(port: port), "the grandchild must release the port once the whole process group is killed")
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
