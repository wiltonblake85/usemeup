import SwiftUI

/// Signs Claude Code in on this Mac, from a button, without a terminal.
///
/// Added 2026-09-29. UseMeUp reads the rate limits with the sign-in Claude
/// Code keeps in the Keychain, and that sign-in ends about a month after it
/// was made. Renewing the access token is automatic; renewing the sign-in
/// itself needs a person to click Authorize in a browser. Before this button
/// the only way was `claude auth login` in a terminal, and a Claude Code
/// session in the Claude app or the cloud does not count, because it uses the
/// app's own sign-in, not this Mac's. That cost a day of stale figures.
///
/// This runs exactly what the terminal would: `claude auth login --claudeai`.
/// Claude Code opens the browser, waits for the Authorize click on a local
/// callback, and writes the Keychain itself. UseMeUp never sees a token.
@MainActor
final class ClaudeSignIn: ObservableObject {
    static let shared = ClaudeSignIn()

    enum Phase: Equatable {
        case idle
        case waiting            // browser open, waiting for Authorize
        case done               // Claude Code said it signed in
        case failed(String)
    }

    @Published private(set) var phase: Phase = .idle
    private var process: Process?
    /// Held open for the life of the process. Claude Code offers to read a
    /// pasted code from stdin; an input that ends at once could read as "no
    /// code" and end the sign-in before the browser answers.
    private var input: Pipe?
    private var giveUp: Task<Void, Never>?
    /// Counts Sign in attempts, so a process that ends after its attempt was
    /// cancelled or replaced is recognised as old. A number, not the Process's
    /// identity: a freed Process's address can be reused by the next one.
    private var attempt = 0

    /// Ten minutes to find the tab and click. Past that the attempt is
    /// abandoned so a forgotten tab does not hold a process forever.
    private static let patience: UInt64 = 600

    /// Where the Claude Code installer and Homebrew put the command. A menu bar
    /// app gets launchd's short PATH, not a shell's, so it looks for itself.
    static func claudePath() -> String? {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return ["\(home)/.local/bin/claude", "/opt/homebrew/bin/claude",
                "/usr/local/bin/claude", "\(home)/.claude/local/claude"]
            .first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    func start() {
        if phase == .waiting { return }
        guard let exe = Self.claudePath() else {
            phase = .failed("Claude Code is not installed where UseMeUp looks (~/.local/bin, /opt/homebrew/bin or /usr/local/bin). Install it from claude.com/code, then try again.")
            return
        }
        attempt += 1
        let mine = attempt
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = ["auth", "login", "--claudeai"]
        p.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = [URL(fileURLWithPath: exe).deletingLastPathComponent().path,
                       "/usr/bin", "/bin", "/usr/sbin", "/sbin", env["PATH"] ?? ""]
            .filter { !$0.isEmpty }.joined(separator: ":")
        p.environment = env
        let input = Pipe(), output = Pipe()
        p.standardInput = input
        p.standardOutput = output
        p.standardError = output
        p.terminationHandler = { proc in
            let data = output.fileHandleForReading.readDataToEndOfFile()
            let text = String(decoding: data, as: UTF8.self)
            let code = proc.terminationStatus
            Task { @MainActor in ClaudeSignIn.shared.finished(mine, code, text) }
        }
        do { try p.run() } catch {
            phase = .failed("Could not start Claude Code: \(error.localizedDescription)")
            return
        }
        process = p
        self.input = input
        phase = .waiting
        giveUp?.cancel()
        giveUp = Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.patience * 1_000_000_000)
            guard !Task.isCancelled else { return }
            self?.cancel(reason: "No Authorize click within ten minutes, so the sign-in was stopped. Click Sign in to try again.")
        }
    }

    /// Lets go of the process as well as stopping it, so a Sign in clicked
    /// before it has exited starts clean. Until 2026-10-05 the old process stayed
    /// in `process`, and when it exited its `finished` closed the new
    /// attempt's stdin, cancelled its timeout and marked it failed while the
    /// browser was still waiting on it.
    func cancel(reason: String = "Sign-in cancelled.") {
        guard let p = process, p.isRunning else { return }
        p.terminate()
        process = nil
        input = nil
        giveUp?.cancel()
        phase = .failed(reason)
    }

    /// `mine` is the attempt whose process ended. One this app has already let
    /// go of (cancelled, or replaced by a new Sign in) is ignored.
    private func finished(_ mine: Int, _ code: Int32, _ text: String) {
        guard mine == attempt, process != nil else { return }
        giveUp?.cancel()
        process = nil
        input = nil
        if code == 0 {
            phase = .done
            // The server reads the Keychain again within a minute; ask it
            // now and once more after that, so the figures come back without
            // waiting for the next poll.
            Task {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                await UsageStore.shared.refresh()
                try? await Task.sleep(nanoseconds: 62_000_000_000)
                await UsageStore.shared.refresh()
                if ClaudeSignIn.shared.phase == .done { ClaudeSignIn.shared.phase = .idle }
            }
        } else if case .failed = phase {
            // Already explained (cancelled, or timed out).
        } else {
            let last = text.split(whereSeparator: \.isNewline)
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .last { !$0.isEmpty && !$0.hasPrefix("If the browser") && !$0.contains("oauth/authorize") }
            phase = .failed("Claude Code did not finish signing in" + (last.map { ": \($0)" } ?? "."))
        }
    }
}

/// The sign-in notice and its button, used by the drop-down and Settings.
struct SignInNotice: View {
    let info: SignInInfo
    /// The drop-down shows the sentence; Settings has its own label and only
    /// needs the button and its progress.
    var showMessage = true
    @ObservedObject private var signIn = ClaudeSignIn.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if showMessage, let m = info.message {
                Text(m)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(info.severity == .calm ? Color.primary : info.severity.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 10) {
                switch signIn.phase {
                case .waiting:
                    ProgressView().controlSize(.small)
                    Text("Waiting for you to click Authorize in your browser…")
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                    Spacer()
                    Button("Cancel") { signIn.cancel() }
                        .buttonStyle(.borderless).font(.system(size: 11))
                case .done:
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                    Text("Signed in. The figures update within a minute.")
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                default:
                    Button(info.action ?? (info.signedOut ? "Sign in" : "Sign in again")) {
                        signIn.start()
                    }
                    .controlSize(.small)
                    Text("Opens your browser. Click Authorize there.")
                        .font(.system(size: 10)).foregroundStyle(.tertiary)
                }
            }
            if case .failed(let why) = signIn.phase {
                Text(why).font(.system(size: 10)).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
