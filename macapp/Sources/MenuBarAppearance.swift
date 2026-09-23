import AppKit
import Combine

/// Whether the menu bar is currently drawn dark or light.
///
/// Not the same as the system's dark mode: macOS tints the menu bar from the
/// wallpaper behind it, so a light-mode Mac can have a dark bar and the other
/// way round. The pill styles that put text straight on the bar need the bar's
/// own answer, which lives on the status item's window. MenuBarExtra hides the
/// status item, so this finds its window and watches its appearance.
@MainActor
final class MenuBarAppearance: ObservableObject {
    static let shared = MenuBarAppearance()

    @Published private(set) var dark: Bool = false

    private var observation: NSKeyValueObservation?
    private var attempts = 0

    private init() {
        dark = Self.isDark(NSApp?.effectiveAppearance)
    }

    func start() { attach() }

    private func attach() {
        if let win = NSApp.windows.first(where: { $0.className.contains("StatusBarWindow") }) {
            dark = Self.isDark(win.effectiveAppearance)
            observation = win.observe(\.effectiveAppearance, options: [.new]) { [weak self] w, _ in
                let d = Self.isDark(w.effectiveAppearance)
                Task { @MainActor in
                    if self?.dark != d { self?.dark = d }
                }
            }
            return
        }
        // The status item's window appears a moment after launch.
        attempts += 1
        guard attempts < 30 else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in self?.attach() }
    }

    nonisolated static func isDark(_ a: NSAppearance?) -> Bool {
        a?.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
    }
}
