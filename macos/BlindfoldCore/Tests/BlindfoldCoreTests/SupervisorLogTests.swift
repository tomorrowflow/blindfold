import Foundation
import Testing
@testable import BlindfoldCore

/// The durable, scrubbed supervisor log (issue #239): "Open Logs" previously opened an empty
/// directory because no structured file-logging existed -- these tests build the seam
/// (`SupervisorLogSink`) and its real, size-bounded file-backed implementation, both
/// Linux-testable exactly like `SingleInstanceGuard`'s real `flock` seam (ADR-0040): the real
/// path is an injected parameter, never resolved by this module itself.
@Test func nullSupervisorLogSinkDiscardsEveryLine() {
    // No observable effect to assert beyond "doesn't crash" -- this is the back-compat default
    // for every existing ProxySupervisor construction site that predates this issue.
    let sink = NullSupervisorLogSink()
    sink.append("spawn: exe=blindfold-proxy args=serve")
}

private func tempLogPath() -> String {
    NSTemporaryDirectory() + "blindfold-supervisor-log-\(UUID().uuidString).log"
}

/// A refused start must leave a diagnosable record on disk (issue #239's own AC) -- the
/// whole point of this file existing, not just an in-memory sink.
@Test func fileSupervisorLogSinkAppendsALineToARealFile() {
    let path = tempLogPath()
    defer { try? FileManager.default.removeItem(atPath: path) }
    let sink = FileSupervisorLogSink(path: path)

    sink.append("refused: root Transit token outside dev mode")

    let contents = (try? String(contentsOfFile: path, encoding: .utf8)) ?? ""
    #expect(contents.contains("refused: root Transit token outside dev mode"))
}

/// The log must not grow without limit (issue #239's AC: "asserted by a test, not by
/// inspection") -- a tiny `maxBytes` plus many appends proves the file is truncated rather
/// than left to grow unbounded, and that the oldest lines are what gets dropped.
@Test func fileSupervisorLogSinkTruncatesOldestLinesOnceOverTheSizeBound() {
    let path = tempLogPath()
    defer { try? FileManager.default.removeItem(atPath: path) }
    let sink = FileSupervisorLogSink(path: path, maxBytes: 200)

    for index in 0..<50 {
        sink.append("event number \(index) padded to make the line longer than it looks")
    }

    let data = FileManager.default.contents(atPath: path) ?? Data()
    #expect(data.count <= 200)
    let contents = String(data: data, encoding: .utf8) ?? ""
    #expect(!contents.contains("event number 0 "))
    #expect(contents.contains("event number 49"))
}
