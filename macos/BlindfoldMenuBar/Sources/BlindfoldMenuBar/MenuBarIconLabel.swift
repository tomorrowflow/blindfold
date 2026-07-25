import SwiftUI
import BlindfoldCore

/// Renders `MenuBarIconState` as an SF Symbol template image (legible in both light and
/// dark menu bars by construction -- macOS renders `Image(systemName:)` in a status-item
/// label as a template image automatically) plus the ADR-0038 alarm badge dot. Purely an
/// asset-mapping choice, never a state decision: the bucket and the badge boolean both
/// come straight from `MenuBarPresentation` (ADR-0040's "re-derives nothing" rule) -- this
/// view only picks a symbol name for a bucket that already exists.
struct MenuBarIconLabel: View {
    let iconState: MenuBarIconState
    let showsAlarmBadge: Bool

    private var symbolName: String {
        switch iconState {
        case .protected: return "checkmark.shield.fill"
        case .degraded: return "exclamationmark.triangle.fill"
        case .stoppedOrRefused: return "shield.slash.fill"
        }
    }

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            Image(systemName: symbolName)
            if showsAlarmBadge {
                Circle()
                    .fill(Color.red)
                    .frame(width: 6, height: 6)
            }
        }
    }
}
