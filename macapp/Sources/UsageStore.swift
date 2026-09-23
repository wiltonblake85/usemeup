import SwiftUI

/// Where the numbers are coming from. The app says this out loud in the panel
/// rather than showing a blank meter and letting you guess.
enum ServerLink: Equatable {
    case starting
    case attached          // a usemeup server was already listening; we joined it
    case spawned           // we started the bundled one ourselves
    case failed(String)
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
    private let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 8
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

    private func bringUpServer() async {
        if await probe() { link = .attached; await refresh(); return }

        guard let exe = bundledServer() else {
            link = .failed("No server is running on port \(Self.port), and this build has no bundled server in Contents/Resources/server.")
            return
        }
        let p = Process()
        p.executableURL = exe
        p.arguments = ["serve", "--no-open"]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch {
            link = .failed("Could not start the bundled server: \(error.localizedDescription)")
            return
        }
        server = p

        // First run indexes every transcript on disk, which is thousands of
        // files, so this waits minutes rather than seconds before giving up.
        let deadline = Date().addingTimeInterval(180)
        while Date() < deadline {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            if await probe() { link = .spawned; await refresh(); return }
            if !p.isRunning { break }
        }
        link = .failed("The bundled server started but never answered on port \(Self.port).")
    }

    private func bundledServer() -> URL? {
        guard let res = Bundle.main.resourceURL else { return nil }
        let exe = res.appendingPathComponent("server/usemeup-server")
        return FileManager.default.isExecutableFile(atPath: exe.path) ? exe : nil
    }

    private func probe() async -> Bool {
        guard let url = URL(string: "\(Self.base)/api/menubar") else { return false }
        do {
            let (_, r) = try await session.data(from: url)
            return (r as? HTTPURLResponse)?.statusCode == 200
        } catch { return false }
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
