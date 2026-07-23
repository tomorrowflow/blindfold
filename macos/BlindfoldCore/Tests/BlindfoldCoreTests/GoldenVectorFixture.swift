import Foundation
@testable import BlindfoldCore

/// Loads `fixtures/supervisor-golden-vectors.json` — the language-neutral golden-vector
/// fixture (issue #193 / ADR-0041, extends ADR-0040) shared with the future C#
/// `Blindfold.Core` reader (#194). Resolved relative to this test source file via
/// `#filePath` rather than SwiftPM `Bundle.module`, so the same fixture file (not a
/// package-bundled copy) is what both cores assert against.
enum GoldenVectorFixture {
    /// A `{"type": ..., "reason": ...}` tag shared by `liveness`/`app_state` fields —
    /// `reason` is only ever populated alongside `type == "refused"`.
    struct TaggedState: Decodable, Sendable {
        let type: String
        let reason: String?

        func toLiveness() -> ProxyLiveness {
            switch type {
            case "notStarted": return .notStarted
            case "starting": return .starting
            case "running": return .running
            case "refused": return .refused(reason: reason!)
            default: fatalError("golden vector fixture: unknown liveness type '\(type)'")
            }
        }

        func toAppState() -> AppState {
            switch type {
            case "stopped": return .stopped
            case "starting": return .starting
            case "protected": return .protected
            case "degraded": return .degraded
            case "refused": return .refused(reason: reason!)
            default: fatalError("golden vector fixture: unknown app state type '\(type)'")
            }
        }
    }

    struct FixtureUnprotectedMode: Decodable, Sendable {
        let active: Bool
        let bound: String?
        let remaining_seconds: Double?
    }

    struct FixtureStatus: Decodable, Sendable {
        let state: String
        let unprotected_mode: FixtureUnprotectedMode?

        func toStatusPayload() -> StatusPayload {
            StatusPayload(
                state: state,
                unprotectedMode: unprotected_mode.map {
                    StatusPayload.UnprotectedMode(active: $0.active, bound: $0.bound, remainingSeconds: $0.remaining_seconds)
                }
            )
        }
    }

    struct FixtureAlarm: Decodable, Sendable {
        let bound: String
        let remaining_seconds: Double?

        func toAlarm() -> UnprotectedAlarm {
            UnprotectedAlarm(bound: bound, remainingSeconds: remaining_seconds)
        }
    }

    struct ReducerCase: Decodable, Sendable {
        let name: String
        let liveness: TaggedState
        let status: FixtureStatus?
        let expected_state: TaggedState
    }

    struct UnprotectedAlarmCase: Decodable, Sendable {
        let name: String
        let status: FixtureStatus
        let expected_alarm: FixtureAlarm?
    }

    struct IconStateCase: Decodable, Sendable {
        let name: String
        let app_state: TaggedState
        let expected_icon: String
    }

    struct HeaderTextCase: Decodable, Sendable {
        let name: String
        let app_state: TaggedState
        let proxy_port: Int
        let dependencies_down: Int
        let alarm: FixtureAlarm?
        let expected_header: String
    }

    struct AlarmBadgeCase: Decodable, Sendable {
        let name: String
        let alarm: FixtureAlarm?
        let expected_badge: Bool
    }

    struct LoopbackGuardCase: Decodable, Sendable {
        let name: String
        let url: String
        let expected_accept: Bool
    }

    struct GoldenVectors: Decodable, Sendable {
        let reducer_truth_table: [ReducerCase]
        let unprotected_alarm_cases: [UnprotectedAlarmCase]
        let icon_state_cases: [IconStateCase]
        let header_text_cases: [HeaderTextCase]
        let alarm_badge_cases: [AlarmBadgeCase]
        let loopback_guard_cases: [LoopbackGuardCase]
    }

    /// Walks up from this test source file to the repo root, then into `fixtures/` —
    /// keeps the fixture a single file both this core and the future C# core (#194)
    /// read directly, rather than a SwiftPM-bundled copy only Swift can see.
    static func load(file: String = #filePath) -> GoldenVectors {
        var url = URL(fileURLWithPath: file)
        // <repo root>/macos/BlindfoldCore/Tests/BlindfoldCoreTests/GoldenVectorFixture.swift
        for _ in 0..<5 {
            url.deleteLastPathComponent()
        }
        url.appendPathComponent("fixtures/supervisor-golden-vectors.json")
        let data = try! Data(contentsOf: url)
        return try! JSONDecoder().decode(GoldenVectors.self, from: data)
    }
}
