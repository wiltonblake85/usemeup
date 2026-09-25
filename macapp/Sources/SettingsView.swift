import ServiceManagement
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var store: UsageStore
    @ObservedObject private var notifier = Notifier.shared
    @ObservedObject private var prefs = WindowPrefs.shared
    @ObservedObject private var serverSettings = ServerSettings.shared
    @AppStorage(PillStyle.key) private var styleRaw = PillStyle.fallback.rawValue
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled
    @State private var loginError: String?

    private var style: PillStyle { PillStyle(rawValue: styleRaw) ?? .fallback }

    var body: some View {
        Form {
            LabeledContent("Menu bar") { Text(store.barSpoken).foregroundStyle(.secondary) }

            Picker("Style", selection: $styleRaw) {
                ForEach(PillStyle.allCases) { Text($0.title).tag($0.rawValue) }
            }

            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    StylePreview(style: style, dark: false)
                    StylePreview(style: style, dark: true)
                }
                Text(style.blurb)
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text("The colour is the forecast, not a fixed threshold: green lands under 100% by reset, amber is close, red is on course to run out.")
                .font(.system(size: 10)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Divider().padding(.vertical, 4)

            // One row per window the server reports, including ones turned
            // off, so anything turned off can be turned back on.
            if settingsWindows.isEmpty {
                LabeledContent("Windows") {
                    Text("appear after the first reading").foregroundStyle(.secondary)
                }
            } else {
                ForEach(settingsWindows) { w in
                    Picker(w.label, selection: prefs.binding(w.key)) {
                        ForEach(WindowShow.allCases) { Text($0.title).tag($0) }
                    }
                }
            }
            Text("Off hides a window from the menu bar and its drop-down, and stops its notifications. It is still measured, so turning it back on shows current figures at once.")
                .font(.system(size: 10)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Divider().padding(.vertical, 4)

            Toggle("Notify me when a window is forecast to run out", isOn: $notifier.enabled)
            Text("Once when the forecast first says a window will hit 100% before it resets (or it passes 95%), and once more if it is spent. Never again for that window. A forecast drawn only from past weeks does not count.")
                .font(.system(size: 10)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            LabeledContent("Notifications") {
                HStack(spacing: 8) {
                    Text(notifier.status).foregroundStyle(.secondary)
                    Button("Send a test") { Task { await notifier.test() } }
                }
            }
            .task { await notifier.refreshStatus() }

            Divider().padding(.vertical, 4)

            Toggle("Open at login", isOn: $launchAtLogin)
                .onChange(of: launchAtLogin) { _, want in setLogin(want) }
            if let e = loginError {
                Text(e).font(.system(size: 10)).foregroundStyle(.red)
            }

            Divider().padding(.vertical, 4)

            LabeledContent("Server") { Text(linkText).foregroundStyle(.secondary) }

            Picker("Rate limits from", selection: $serverSettings.source) {
                ForEach(RateSource.allCases) { Text($0.title).tag($0) }
            }
            .disabled(store.link == .attached)
            .onChange(of: serverSettings.source) { _, _ in
                Task { await store.applyServerSettings() }
            }
            Toggle("Renew an expired sign-in by running claude -p ok", isOn: $serverSettings.autoRefresh)
                .disabled(store.link == .attached || serverSettings.source == .statusline)
                .onChange(of: serverSettings.autoRefresh) { _, _ in
                    Task { await store.applyServerSettings() }
                }
            Text(serverNote)
                .font(.system(size: 10)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .formStyle(.grouped)
        .frame(width: 400)
        .padding(.vertical, 8)
    }

    /// Same order as the panel: weekly windows first, the 5-hour window last.
    private var settingsWindows: [UsageWindow] {
        let rank = ["weekly_all": 0, "weekly_scoped": 1, "session": 2]
        return store.allWindows.sorted { (rank[$0.key] ?? 9) < (rank[$1.key] ?? 9) }
    }

    private var pidText: String {
        store.serverInfo?.pid.map { " (pid \($0))" } ?? ""
    }

    private var linkText: String {
        switch store.link {
        case .starting:        return "starting…"
        case .waitingForAgent: return "waiting for the usemeup LaunchAgent to start"
        case .attached:        return "joined the one already running on 8787" + pidText
        case .spawned:         return "started by this app on 8787" + pidText
        case .failed(let w):   return w
        }
    }

    /// When the app joined a server it did not start, the pickers above are
    /// greyed out, and this says which settings are actually in force.
    private var serverNote: String {
        guard store.link == .attached else { return serverSettings.source.blurb }
        guard let info = store.serverInfo, let raw = info.source else {
            return "UseMeUp joined a server that was already running, so that server's own settings apply. These choices take effect when UseMeUp starts its own server."
        }
        let name = RateSource(rawValue: raw)?.title.lowercased() ?? raw
        let renew = (info.autoRefresh ?? false) ? ", renewing an expired sign-in" : ""
        return "UseMeUp joined a server that was already running, so its own settings apply: \(name)\(renew). These choices take effect when UseMeUp starts its own server."
    }

    private func setLogin(_ want: Bool) {
        do {
            loginError = nil
            if want { try SMAppService.mainApp.register() }
            else    { try SMAppService.mainApp.unregister() }
        } catch {
            loginError = error.localizedDescription
            launchAtLogin = SMAppService.mainApp.status == .enabled
        }
    }
}

/// A style drawn on a light or dark strip, with one window in each colour, so
/// every state can be judged before choosing.
private struct StylePreview: View {
    let style: PillStyle
    let dark: Bool

    private static let sample: [UsageWindow] = [
        sampleWindow("weekly_scoped", "Fable", 94, "alert"),
        sampleWindow("weekly_all", "All models", 71, "watch"),
        sampleWindow("session", "5-hour", 22, "ok"),
    ]

    var body: some View {
        Image(nsImage: BarLabel.image(Self.sample, style: style, dark: dark))
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(RoundedRectangle(cornerRadius: 5)
                .fill(dark ? Color(white: 0.16) : Color(white: 0.93)))
    }

    private static func sampleWindow(_ key: String, _ label: String, _ pct: Double, _ state: String) -> UsageWindow {
        let j = """
        {"key":"\(key)","label":"\(label)","used_pct":\(pct),"headline":"","verdict":"","tone":"",
         "state":"\(state)","kicker":"","detail":"","basis":"","resets_at":null,"over_cap":false,"resets_in":null}
        """
        return try! JSONDecoder().decode(UsageWindow.self, from: Data(j.utf8))
    }
}
