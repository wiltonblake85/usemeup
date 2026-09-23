import SwiftUI

/// How one rate-limit window is shown. Chosen per window in Settings.
///
/// Added 2026-09-23 when Opus 5.5 overtook Fable. A model-scoped window you no
/// longer use is dead weight in a bar that has room for three figures, but
/// which window stops mattering is the user's call, not the app's: someone
/// else may live on the scoped model and never touch the 5-hour window.
enum WindowShow: String, CaseIterable, Identifiable {
    case always, whenNeeded, off

    var id: String { rawValue }

    var title: String {
        switch self {
        case .always:     return "Always in the menu bar"
        case .whenNeeded: return "In the menu bar when amber or red"
        case .off:        return "Off"
        }
    }
}

/// The user's choice for every window, kept in UserDefaults by window key.
///
/// Keyed by the server's window key ("session", "weekly_all",
/// "weekly_scoped"), not by label, so a renamed model keeps its setting. A key
/// with no stored choice falls back to what the app did before this setting
/// existed, so nobody's bar changes until they change it.
///
/// Off means off everywhere in the app: not in the bar, not in the panel, and
/// no notifications. The server still measures it, so turning it back on
/// shows current figures at once.
@MainActor
final class WindowPrefs: ObservableObject {
    static let shared = WindowPrefs()
    private static let storeKey = "windowShow"

    @Published private var modes: [String: String] {
        didSet { UserDefaults.standard.set(modes, forKey: Self.storeKey) }
    }

    private init() {
        modes = (UserDefaults.standard.dictionary(forKey: Self.storeKey) as? [String: String]) ?? [:]
    }

    /// The weekly windows are the ones a week is planned around, so they start
    /// out always in the bar. Everything else starts out earning its place by
    /// being in trouble: the 5-hour window is noise on a normal day, but when
    /// it hits the wall it blocks every model at once.
    static func fallback(_ key: String) -> WindowShow {
        (key == "weekly_all" || key.hasPrefix("weekly_scoped")) ? .always : .whenNeeded
    }

    func mode(_ key: String) -> WindowShow {
        modes[key].flatMap(WindowShow.init(rawValue:)) ?? Self.fallback(key)
    }

    func set(_ key: String, _ m: WindowShow) { modes[key] = m.rawValue }

    func binding(_ key: String) -> Binding<WindowShow> {
        Binding(get: { self.mode(key) }, set: { self.set(key, $0) })
    }

    /// The key an alert belongs to. Alert ids are "key|window|kind"; see
    /// alert_for in panel.py.
    static func key(ofAlert id: String) -> String {
        String(id.split(separator: "|", maxSplits: 1).first ?? "")
    }
}
