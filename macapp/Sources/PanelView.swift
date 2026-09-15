import SwiftUI

struct PanelView: View {
    @EnvironmentObject var store: UsageStore
    @Environment(\.openSettings) private var openSettings
    @State private var tick = Date()

    /// Weekly windows first, the 5-hour window under them. The long windows are
    /// the ones you plan against; the short one is a footnote until it bites.
    private var ordered: [UsageWindow] {
        let rank = ["weekly_all": 0, "weekly_scoped": 1, "session": 2]
        return store.windows.sorted { (rank[$0.key] ?? 9) < (rank[$1.key] ?? 9) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()

            if case .failed(let why) = store.link {
                problem(why)
            } else if ordered.isEmpty {
                problem(store.fetchError ?? "Waiting for the first reading…")
            } else {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(ordered) { w in WindowRow(window: w, tick: tick) }
                }
                .padding(14)
            }

            Divider()
            footer
        }
        .frame(width: 340)
        .onAppear { Task { await store.refresh() } }
        .onReceive(Timer.publish(every: 1, on: .main, in: .common).autoconnect()) { tick = $0 }
    }

    private var header: some View {
        HStack {
            Text("UseMeUp").font(.system(size: 13, weight: .semibold))
            Spacer()
            Button { Task { await store.refresh() } } label: {
                Image(systemName: "arrow.clockwise").font(.system(size: 11))
            }
            .buttonStyle(.borderless)
            .help("Refresh now")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private func problem(_ text: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(text).font(.system(size: 11)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let p = store.payload, let hours = p.hoursLabel, let days = p.sampleDays {
                // The pace model is only as good as the rhythm behind it, so the
                // rhythm is stated rather than hidden in a tooltip.
                Text("Working day: \(hours) · learned from \(days) days")
                    .font(.system(size: 10)).foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 12) {
                Button("Dashboard") { store.openDashboard() }
                Button("Settings…") { NSApp.activate(); openSettings() }
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }
            }
            .buttonStyle(.borderless)
            .font(.system(size: 11))
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }
}

private struct WindowRow: View {
    @EnvironmentObject var store: UsageStore
    let window: UsageWindow
    let tick: Date

    private var isPinned: Bool { store.pinnedWindow == window.key }

    private var support: String {
        [window.kicker, window.detail].filter { !$0.isEmpty }.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(window.label).font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
                if isPinned {
                    Image(systemName: "pin.fill").font(.system(size: 8))
                        .foregroundStyle(.tertiary).help("Shown in the menu bar")
                }
                Spacer()
                Text(window.shortPct)
                    .font(.system(size: 17, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(window.severity == .calm ? Color.primary : window.severity.color)
            }

            meter

            // Where it ends up, first and largest. The pace verdict describes
            // the slope; the landing figure is the thing being asked about, and
            // making the reader derive it from "a little under pace" is making
            // them do the last step themselves.
            Text(window.headline.sentenceCased)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(window.severity == .calm ? Color.primary : window.severity.color)
                .fixedSize(horizontal: false, vertical: true)

            Text(window.verdict.sentenceCased)
                .font(.system(size: 11)).foregroundStyle(.secondary)

            // Kicker and burn rate are one supporting line, not two.
            Text(support).font(.system(size: 10)).foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)

            if let left = store.resetsIn(window) {
                Text("Resets in \(left)").font(.system(size: 10)).foregroundStyle(.tertiary)
            }
        }
    }

    private var meter: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.10))
                Capsule().fill(window.severity.color)
                    .frame(width: max(2, geo.size.width * min(window.usedPct, 100) / 100))
            }
        }
        .frame(height: 4)
    }
}
