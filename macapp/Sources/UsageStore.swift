import SwiftUI

/// Where the numbers are coming from. The app says this out loud in the panel
/// rather than showing a blank meter and letting you guess.
enum ServerLink: Equatable {
    case starting
    case waitingForAgent   // the usemeup LaunchAgent is installed; giving it time to claim the port
    case attached          // a usemeup server was already listening; we joined it
    case spawned           // we started the bundled one ourselves
    case failed(String)
}

/// What `/api/ping` says about the server on the port.
struct ServerInfo: Codable, Equatable {
    let app: String
    let version: String?
    let pid: Int?
    let source: String?
    let autoRefresh: Bool?

    enum CodingKeys: String, CodingKey {
        case app, version, pid, source
        case autoRefresh = "auto_refresh"
    }
}

/// Who answers on the port.
private enum Occupant {
    case usemeup(ServerInfo?)   // nil info: a server older than /api/ping
    case foreign                // something answered, and it is not usemeup
    case nobody
}

@MainActor
final class UsageStore: ObservableObject {
    /// One store, referenced by the scene and the app delegate alike.
    static let shared = UsageStore()

    @Published private(set) var payload: MenuBarPayload?
    @Published private(set) var link: ServerLink = .starting
    @Published private(set) var lastFetch: Date?
    @Published private(set) var fetchError: String?

    static let port = 8787
    private static var base: String { "http://127.0.0.1:\(port)" }

    private var timer: Timer?
    private var server: Process?
    /// One bring-up at a time: the timer, a failed refresh and a settings
    /// change can all ask for one.
    private var bringingUp = false
    /// The server on the port, as it described itself. Settings shows it.
    @Published private(set) var serverInfo: ServerInfo?

    /// The `usemeup agent install` LaunchAgent. When it exists it owns the
    /// port, and this app waits for it rather than racing it at login.
    static let agentPlist: URL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/LaunchAgents/com.wiltonblake.usemeup.plist")

    private let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        // Long enough for a cold rebuild on a busy Mac. A timeout is a slow
        // answer, not a dead server, and refresh() treats it that way.
        c.timeoutIntervalForRequest = 30
        c.waitsForConnectivity = false
        return URLSession(configuration: c)
    }()

    // ---------------------------------------------------------------- windows

    /// Every window the server reports, including ones turned off. Settings
    /// lists these so a window can be turned back on.
    var allWindows: [UsageWindow] { payload?.windows ?? [] }

    /// The windows the user has not turned off. Everything the app shows or
    /// says reads from this, never from `allWindows`.
    var windows: [UsageWindow] {
        allWindows.filter { WindowPrefs.shared.mode($0.key) != .off }
    }

    /// The worst window as the SERVER ranked it, unless the user turned that
    /// one off. Falling back to a local max only then keeps one ranking rule
    /// in one place.
    var worst: UsageWindow? {
        if let k = payload?.worst, let w = windows.first(where: { $0.key == k }) { return w }
        return windows.max { $0.severity < $1.severity }
    }

    /// What the menu bar shows, left to right.
    ///
    /// Each window's place is the user's choice in Settings (WindowPrefs):
    /// always, only while amber or red, or off. Out of the box that is every
    /// weekly window always and the 5-hour window only when it is in trouble.
    ///
    /// Nothing takes the bar over. An earlier build let the single worst window
    /// do that, which hid exactly the wrong thing: once a scoped window is
    /// nearly spent you stop using that model, the question is closed, and the
    /// all-models figure is the one you now need.
    var barSegments: [UsageWindow] {
        let rank: (UsageWindow) -> Int = { w in
            if w.key.hasPrefix("weekly_scoped") { return 0 }
            if w.key == "weekly_all" { return 1 }
            return 2
        }
        let prefs = WindowPrefs.shared
        return windows
            .filter { prefs.mode($0.key) == .always || $0.severity > .calm }
            .sorted { rank($0) < rank($1) }
    }

    /// Readings exist but nothing is set to show right now: every window left
    /// on is "only when amber or red" and all of them are green. The bar then
    /// shows a check rather than the "--" that means no reading at all.
    var barAllClear: Bool { barSegments.isEmpty && !allWindows.isEmpty }

    /// The same thing in words, for VoiceOver and the tooltip.
    var barSpoken: String {
        if barAllClear { return "Nothing needs attention" }
        return barSegments.isEmpty ? "No reading yet"
            : barSegments.map { "\($0.label) \($0.barValue)" }
                .joined(separator: ", ")
    }

    // ---------------------------------------------------------------- lifecycle

    func start() {
        Task { await bringUpServer() }
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.refresh() }
        }
        // Nothing here needs the minute on the dot; let the system batch wakeups.
        timer?.tolerance = 10
    }

    func stop() {
        timer?.invalidate()
        timer = nil
        // Only ever kill a server this app started. An attached LaunchAgent is
        // someone else's process and outlives us.
        if let p = server, p.isRunning { p.terminate() }
        server = nil
    }

    // ---------------------------------------------------------------- server

    /// Find or start a server, in this order:
    ///
    /// 1. A usemeup server already answers: join it.
    /// 2. Something else holds the port: say so and stop. Starting ours would
    ///    only have it wait in standby behind a program that is not ours.
    /// 3. The usemeup LaunchAgent is installed: wait for it. Both start at
    ///    login, and before 2026-09-25 this app won that race, spawned its
    ///    bundled copy, and left the agent failing to bind and restarting
    ///    every ten seconds, 3,737 times in 14.5 hours.
    /// 4. Otherwise, or if the agent never answers: start the bundled server
    ///    with the settings chosen in this app.
    private func bringUpServer() async {
        guard !bringingUp else { return }
        bringingUp = true
        defer { bringingUp = false }

        switch await occupant() {
        case .usemeup(let info):
            serverInfo = info
            link = .attached
            await refresh()
            return
        case .foreign:
            link = .failed("Port \(Self.port) is in use by another program, so UseMeUp cannot run its server there. Quit that program and UseMeUp will pick up on its next check.")
            return
        case .nobody:
            break
        }

        if FileManager.default.fileExists(atPath: Self.agentPlist.path) {
            link = .waitingForAgent
            let deadline = Date().addingTimeInterval(90)
            while Date() < deadline {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                if case .usemeup(let info) = await occupant() {
                    serverInfo = info
                    link = .attached
                    await refresh()
                    return
                }
            }
            NSLog("UseMeUp: the usemeup LaunchAgent is installed but nothing answered on %d in 90 s; starting the bundled server", Self.port)
        }
        await spawn()
    }

    private func spawn() async {
        guard let exe = bundledServer() else {
            link = .failed("No server is running on port \(Self.port), and this build has no bundled server in Contents/Resources/server.")
            return
        }
        let p = Process()
        p.executableURL = exe
        p.arguments = ["serve", "--no-open"]
        // Explicit, not inherited: see ServerSettings.
        p.environment = ServerSettings.shared.environment()
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch {
            link = .failed("Could not start the bundled server: \(error.localizedDescription)")
            return
        }
        server = p

        // The server claims the port and answers /api/ping at once, then
        // scans. A first run indexes every transcript on disk, which is
        // thousands of files, so data can take minutes; the port cannot.
        let deadline = Date().addingTimeInterval(180)
        while Date() < deadline {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            if case .usemeup(let info) = await occupant() {
                serverInfo = info
                link = .spawned
                await refresh()
                return
            }
            if !p.isRunning { break }
        }
        link = .failed("The bundled server started but never answered on port \(Self.port).")
    }

    /// Restart a server this app started, so a changed setting takes effect.
    /// A server the app joined keeps its own settings and is left alone.
    func applyServerSettings() async {
        guard link == .spawned, let p = server else { return }
        p.terminate()
        let deadline = Date().addingTimeInterval(10)
        while p.isRunning && Date() < deadline {
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        server = nil
        link = .starting
        await bringUpServer()
    }

    private func bundledServer() -> URL? {
        guard let res = Bundle.main.resourceURL else { return nil }
        let exe = res.appendingPathComponent("server/usemeup-server")
        return FileManager.default.isExecutableFile(atPath: exe.path) ? exe : nil
    }

    /// Who answers on the port. `/api/ping` costs the server nothing; a server
    /// older than it gets one more question on /api/menubar before being
    /// called foreign.
    private func occupant() async -> Occupant {
        guard let ping = URL(string: "\(Self.base)/api/ping"),
              let menubar = URL(string: "\(Self.base)/api/menubar") else { return .nobody }
        do {
            let (data, r) = try await session.data(from: ping)
            let code = (r as? HTTPURLResponse)?.statusCode ?? 0
            if code == 200, let info = try? JSONDecoder().decode(ServerInfo.self, from: data),
               info.app == "usemeup" {
                return .usemeup(info)
            }
            let (_, r2) = try await session.data(from: menubar)
            return (r2 as? HTTPURLResponse)?.statusCode == 200 ? .usemeup(nil) : .foreign
        } catch {
            return .nobody
        }
    }

    // ---------------------------------------------------------------- polling

    func refresh() async {
        guard let url = URL(string: "\(Self.base)/api/menubar") else { return }
        do {
            let (data, _) = try await session.data(from: url)
            let decoded = try JSONDecoder().decode(MenuBarPayload.self, from: data)
            payload = decoded
            lastFetch = Date()
            fetchError = decoded.ok ? nil : (decoded.error ?? "the server has no rate-limit data")
            if case .failed = link { link = .attached }
            // A window turned off raises nothing. Filtered here, not on the
            // server, because the choice lives in this app's settings.
            let prefs = WindowPrefs.shared
            await Notifier.shared.deliver((decoded.alerts ?? []).filter {
                prefs.mode(WindowPrefs.key(ofAlert: $0.id)) != .off
            })
        } catch {
            fetchError = error.localizedDescription
            // The server went away: ours exited, or nothing listens any more.
            // Find or start one again rather than polling a dead port forever.
            //
            // Only on "nobody is there". A timeout means the server is busy,
            // and asking again at once is how, in the first build of this,
            // every slow answer turned into another request: each rebuild
            // slowed the next until a poll took 44 seconds at 160% CPU.
            let ours = server.map { !$0.isRunning } ?? false
            let gone = [URLError.cannotConnectToHost, .networkConnectionLost, .cannotFindHost]
                .contains((error as? URLError)?.code ?? .unknown)
            let failed: Bool = { if case .failed = link { return true } else { return false } }()
            if ours || (gone && (link == .attached || failed)) {
                if ours { server = nil }
                Task { await bringUpServer() }
            }
        }
    }

    /// Countdown that stays honest between polls: the server's figure minus the
    /// time since we asked, rather than a number frozen at fetch.
    func resetsIn(_ w: UsageWindow) -> String? {
        guard let secs = w.resetsIn, let at = lastFetch else { return nil }
        let left = secs - Int(Date().timeIntervalSince(at))
        return left <= 0 ? "resetting now" : countdown(left)
    }

    func openDashboard() {
        if let u = URL(string: "\(Self.base)/") { NSWorkspace.shared.open(u) }
    }
}
