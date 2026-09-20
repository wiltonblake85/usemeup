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

    /// Which window owns the bar while nothing else is worse. A worse window
    /// takes over the dot and the number together (see `barWindow`), so
    /// pinning a calm one cannot hide a blown one.
    @AppStorage("pinnedWindow") var pinnedWindow: String = "weekly_all"

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

    var windows: [UsageWindow] { payload?.windows ?? [] }

    var pinned: UsageWindow? {
        windows.first { $0.key == pinnedWindow } ?? windows.first
    }

    /// The worst window as the SERVER ranked it. Falling back to a local max
    /// only when `worst` is missing keeps one ranking rule in one place.
    var worst: UsageWindow? {
        if let k = payload?.worst, let w = windows.first(where: { $0.key == k }) { return w }
        return windows.max { $0.severity < $1.severity }
    }

    /// The window the menu bar is describing. The dot and the number must
    /// always come from the same window: a red dot beside a calm window's 55%
    /// reads as "55% and in trouble", which is true of neither window. So the
    /// pinned window owns the bar until another window is strictly worse, and
    /// then that window takes the whole bar, colour and number together.
    var barWindow: UsageWindow? {
        guard let p = pinned else { return worst }
        if let w = worst, w.severity > p.severity { return w }
        return p
    }

    /// True when the bar is showing something other than the pinned window,
    /// which is exactly when it needs a name in front of the number.
    var barIsOverride: Bool {
        guard let b = barWindow, let p = pinned else { return false }
        return b.key != p.key
    }

    var barText: String {
        guard let w = barWindow else { return "--" }
        let value = w.usedPct >= 100 ? "spent" : w.shortPct
        return barIsOverride ? "\(w.barName) \(value)" : value
    }
    var barSeverity: Severity { barWindow?.severity ?? .calm }

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
