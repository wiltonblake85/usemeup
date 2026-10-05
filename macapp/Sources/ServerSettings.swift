import SwiftUI

/// Where the server this app starts reads the rate limits from.
enum RateSource: String, CaseIterable, Identifiable {
    case statusline, endpoint, auto

    var id: String { rawValue }

    var title: String {
        switch self {
        case .statusline: return "Claude Code status line"
        case .endpoint:   return "Usage endpoint"
        case .auto:       return "Usage endpoint, then status line"
        }
    }

    var blurb: String {
        switch self {
        case .statusline:
            return "No credential and no network. Updates only when you use Claude Code in a terminal, and has no model-scoped window."
        case .endpoint:
            return "Reads your Claude Code sign-in from the Keychain and asks Anthropic at most every five minutes. Includes the model-scoped window."
        case .auto:
            return "The usage endpoint, falling back to the status line when the endpoint fails."
        }
    }
}

/// The settings handed to the server this app starts itself.
///
/// Added 2026-09-25. Before this, the bundled server inherited whatever
/// environment the app happened to be launched with: started by its
/// LaunchAgent it read the usage endpoint, started from Finder or "Open at
/// login" it silently fell back to the status line, and token auto-refresh
/// was never on. The choice now lives in the app's own defaults and is passed
/// to the server explicitly, so it no longer depends on how the app was opened.
///
/// These apply only to a server this app starts. A server that was already
/// running (the `usemeup agent` LaunchAgent) keeps its own settings, and the
/// Settings window says so.
@MainActor
final class ServerSettings: ObservableObject {
    static let shared = ServerSettings()

    private static let sourceKey = "serverSource"
    private static let refreshKey = "serverAutoRefresh"

    @Published var source: RateSource {
        didSet { UserDefaults.standard.set(source.rawValue, forKey: Self.sourceKey) }
    }

    /// Let the server run `claude -p ok` to renew an expired token. Spends a
    /// few tokens about once a day, so it starts off unless the environment
    /// this app was first launched with already asked for it.
    @Published var autoRefresh: Bool {
        didSet { UserDefaults.standard.set(autoRefresh, forKey: Self.refreshKey) }
    }

    private init() {
        let d = UserDefaults.standard
        let env = ProcessInfo.processInfo.environment

        // First launch after this change: adopt what the environment said, so
        // a Mac that was deliberately set to the endpoint stays on it, then
        // save it so later launches no longer depend on the environment.
        let s: RateSource
        if let raw = d.string(forKey: Self.sourceKey), let saved = RateSource(rawValue: raw) {
            s = saved
        } else {
            s = RateSource(rawValue: (env["USEMEUP_SOURCE"] ?? "").lowercased()) ?? .statusline
        }
        let r: Bool
        if d.object(forKey: Self.refreshKey) != nil {
            r = d.bool(forKey: Self.refreshKey)
        } else {
            r = !["", "0", "false", "False"].contains(env["USEMEUP_AUTO_REFRESH"] ?? "")
        }
        source = s
        autoRefresh = r
        // didSet does not run in init, so save explicitly: from here on the
        // choice is the app's, not the environment's.
        d.set(s.rawValue, forKey: Self.sourceKey)
        d.set(r, forKey: Self.refreshKey)
    }

    /// The environment for a spawned server: this app's own, with the two
    /// choices above written over whatever it carried.
    ///
    /// `standingInFor` is the usemeup LaunchAgent's own EnvironmentVariables,
    /// passed when the app starts its server only because that agent did not
    /// answer in time. The server then stands in for the agent, so it takes
    /// the agent's choices, not this app's. On 2026-09-30 a stand-in ran with
    /// the app's renewal setting (off) while the agent's was on, and the bar
    /// went grey for 15 hours when the access token ran out at 4:02 PM.
    ///
    /// PATH gets Claude Code's folder either way: an app opened from Finder or
    /// at login has /usr/bin:/bin:/usr/sbin:/sbin, which has no `claude`, so
    /// the server could not have renewed the token even with renewal on. The
    /// server also searches those folders itself since 0.1.5; this is the
    /// second line, for any older server binary.
    func environment(standingInFor agent: [String: String]? = nil) -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        if let agent {
            // Every choice `usemeup agent install` can write (agent.build_plist),
            // so the stand-in records into the agent's database and splits days
            // in its time zone. Until 2026-10-05 only the source was copied, and a
            // stand-in for an agent with USEMEUP_DB set sampled into the default
            // database, leaving a hole in the agent's history. Absent from the
            // agent means the server's default there, so here too.
            // USEMEUP_PORT is left out on purpose: this app only ever talks to
            // UsageStore.port, so a stand-in anywhere else could not be reached.
            for k in ["USEMEUP_SOURCE", "USEMEUP_TZ", "USEMEUP_DB", "USEMEUP_OFFLINE", "USEMEUP_DEMO"] {
                if let v = agent[k] { env[k] = v } else { env.removeValue(forKey: k) }
            }
            if let r = agent["USEMEUP_AUTO_REFRESH"], !["", "0", "false", "False"].contains(r) {
                env["USEMEUP_AUTO_REFRESH"] = "1"
            } else {
                env.removeValue(forKey: "USEMEUP_AUTO_REFRESH")
            }
        } else {
            env["USEMEUP_SOURCE"] = source.rawValue
            if autoRefresh { env["USEMEUP_AUTO_REFRESH"] = "1" }
            else { env.removeValue(forKey: "USEMEUP_AUTO_REFRESH") }
        }
        var path: [String] = []
        if let exe = ClaudeSignIn.claudePath() {
            path.append(URL(fileURLWithPath: exe).deletingLastPathComponent().path)
        }
        path += (env["PATH"] ?? "").split(separator: ":").map(String.init)
        path += ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        var seen = Set<String>()
        env["PATH"] = path.filter { !$0.isEmpty && seen.insert($0).inserted }.joined(separator: ":")
        return env
    }
}
