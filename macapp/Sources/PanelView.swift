import SwiftUI

/// The drop-down under the menu bar item.
///
/// Since 2026-09-25 this is the whole detailed view: every window's figures
/// and its burn-up chart. The app no longer sends anyone to the web page for
/// the chart; the page still exists for whoever runs the server by hand.
struct PanelView: View {
    @EnvironmentObject var store: UsageStore
    @ObservedObject private var prefs = WindowPrefs.shared

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
            } else if ordered.isEmpty && !store.allWindows.isEmpty {
                problem("Every window is turned off. Choose which to show in Settings.")
            } else if ordered.isEmpty {
                problem(firstRunHint(store.fetchError) ?? "Waiting for the first reading…")
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(ordered.enumerated()), id: \.element.id) { i, w in
                        if i > 0 { Divider().padding(.vertical, 14) }
                        WindowRow(window: w)
                    }
                }
                .padding(16)
            }

            Divider()
            footer
        }
        .frame(width: 540)
        .onAppear { Task { await store.refresh() } }
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
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    /// A fresh download reads the Claude Code status line, which stays empty
    /// until the status line is set up and one message is sent in Claude Code.
    /// Someone who never uses Claude Code in a terminal would wait forever, so
    /// when this app started the server, point at the other way in too.
    private func firstRunHint(_ error: String?) -> String? {
        guard let e = error else { return nil }
        guard e.hasPrefix("No status line reading"), store.link == .spawned else { return e }
        return e + "\n\nOr open Settings and set Rate limits from to Usage endpoint. It asks Anthropic directly with the sign-in Claude Code saved in your Keychain, so it sees usage from Cowork and claude.ai too. If you rarely open Claude Code, also turn on the renew option under it."
    }

    private func problem(_ text: String) -> some View {
        Text(text).font(.system(size: 11)).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
            .padding(16)
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !ordered.isEmpty { BurnLegend() }
            if let p = store.payload, let hours = p.hoursLabel, let days = p.sampleDays {
                // The pace model is only as good as the rhythm behind it, so the
                // rhythm is stated rather than hidden in a tooltip.
                Text("Working day: \(hours) · learned from \(days) days")
                    .font(.system(size: 10)).foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 12) {
                Button("Settings…") { SettingsWindow.shared.show() }
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }
            }
            .buttonStyle(.borderless)
            .font(.system(size: 11))
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}

private struct WindowRow: View {
    @EnvironmentObject var store: UsageStore
    let window: UsageWindow

    private var support: String {
        [window.kicker, window.detail].filter { !$0.isEmpty }.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(window.label).font(.system(size: 12, weight: .semibold))
                Spacer()
                Text(window.shortPct)
                    .font(.system(size: 20, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(window.severity == .calm ? Color.primary : window.severity.color)
            }

            // Where it ends up, first and largest. The pace verdict describes
            // the slope; the landing figure is the thing being asked about.
            Text(window.headline.sentenceCased)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(window.severity == .calm ? Color.primary : window.severity.color)
                .fixedSize(horizontal: false, vertical: true)

            if let a = window.advice, !a.isEmpty {
                Text(a)
                    .font(.system(size: 11, weight: .medium))
                    .fixedSize(horizontal: false, vertical: true)
            }

            BurnChart(window: window)
                .padding(.top, 4)

            Text(window.verdict.sentenceCased)
                .font(.system(size: 11)).foregroundStyle(.secondary)

            // Kicker and burn rate are one supporting line, not two.
            Text(support).font(.system(size: 10)).foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)

            // Minutes are the finest unit shown, so a minute tick is enough,
            // and a TimelineView stops ticking while the drop-down is closed.
            TimelineView(.everyMinute) { _ in
                if let left = store.resetsIn(window) {
                    Text("Resets in \(left)").font(.system(size: 10)).foregroundStyle(.tertiary)
                }
            }
        }
    }
}
