import Foundation
import Testing
@testable import BlindfoldCore

/// The single-instance guard (issue #213, ADR-0039/0041 ported to Swift): Windows uses a
/// session-scoped named mutex so two tray supervisors can't both spawn a proxy and collide
/// on the port; macOS has no direct analog, so a non-blocking exclusive `flock` on a fixed
/// lock file plays the same role -- held for the process's lifetime, released automatically
/// if the process dies. Each test uses its own temp-file path so tests never collide with
/// each other or a real running instance.
@Test func acquiringAFreshLockSucceeds() {
    let path = NSTemporaryDirectory() + "blindfold-single-instance-\(UUID().uuidString).lock"
    let guard1 = SingleInstanceGuard()

    #expect(guard1.acquire(lockFilePath: path) == true)

    guard1.release()
    try? FileManager.default.removeItem(atPath: path)
}

@Test func aSecondGuardCannotAcquireTheSameLockWhileTheFirstHoldsIt() {
    let path = NSTemporaryDirectory() + "blindfold-single-instance-\(UUID().uuidString).lock"
    let guard1 = SingleInstanceGuard()
    let guard2 = SingleInstanceGuard()
    #expect(guard1.acquire(lockFilePath: path) == true)

    #expect(guard2.acquire(lockFilePath: path) == false)

    guard1.release()
    try? FileManager.default.removeItem(atPath: path)
}

@Test func releasingLetsAFreshGuardAcquireTheSameLock() {
    let path = NSTemporaryDirectory() + "blindfold-single-instance-\(UUID().uuidString).lock"
    let guard1 = SingleInstanceGuard()
    #expect(guard1.acquire(lockFilePath: path) == true)
    guard1.release()

    let guard2 = SingleInstanceGuard()
    #expect(guard2.acquire(lockFilePath: path) == true)

    guard2.release()
    try? FileManager.default.removeItem(atPath: path)
}
