import Testing
@testable import BlindfoldCore

/// The menu bar icon's coarse-state reduction (issue #185 / ADR-0039): the icon
/// encodes only three buckets -- protected / degraded / stopped-or-refused -- so
/// status reads at a glance without opening the menu. Pure reduction from the
/// five-state `AppState`, no UI.
///
/// Cases are the shared golden-vector fixture (issue #193 / ADR-0041) — presentation
/// strings are asserted verbatim as the shared truth both this core and the future
/// C# `Blindfold.Core` (#194) render.
@Test(arguments: GoldenVectorFixture.load().icon_state_cases)
func iconStateMatchesGoldenVector(_ vector: GoldenVectorFixture.IconStateCase) {
    let icon = MenuBarPresentation.iconState(for: vector.app_state.toAppState())
    let expected: MenuBarIconState = switch vector.expected_icon {
    case "protected": .protected
    case "degraded": .degraded
    case "stoppedOrRefused": .stoppedOrRefused
    default: fatalError("golden vector fixture: unknown icon state '\(vector.expected_icon)'")
    }
    #expect(icon == expected, "\(vector.name)")
}

/// The header line (issue #185 / ADR-0039): renders all five states verbatim as
/// the menu's status-at-a-glance header text.
@Test(arguments: GoldenVectorFixture.load().header_text_cases)
func headerTextMatchesGoldenVector(_ vector: GoldenVectorFixture.HeaderTextCase) {
    let text = MenuBarPresentation.headerText(
        for: vector.app_state.toAppState(),
        proxyPort: vector.proxy_port,
        dependenciesDown: vector.dependencies_down,
        alarm: vector.alarm?.toAlarm()
    )
    #expect(text == vector.expected_header, "\(vector.name)")
}

/// The icon must also flag the ADR-0038 alarm (AC #1) -- the single source of
/// truth the view reads instead of re-deriving `alarm != nil` itself, keeping the
/// view logic-free (AC #3).
@Test(arguments: GoldenVectorFixture.load().alarm_badge_cases)
func alarmBadgeMatchesGoldenVector(_ vector: GoldenVectorFixture.AlarmBadgeCase) {
    let shows = MenuBarPresentation.showsUnprotectedAlarmBadge(alarm: vector.alarm?.toAlarm())
    #expect(shows == vector.expected_badge, "\(vector.name)")
}
