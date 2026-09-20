import SwiftUI

/// The bar glyph. Drawn rather than an SF Symbol because the menu bar renders
/// symbols as templates, which throws the colour away, and the colour is the
/// whole signal.
enum BarDot {
    static func image(_ sev: Severity) -> NSImage {
        let d: CGFloat = 8
        let img = NSImage(size: NSSize(width: d, height: d), flipped: false) { rect in
            NSColor(sev.color).setFill()
            NSBezierPath(ovalIn: rect).fill()
            return true
        }
        img.isTemplate = false
        return img
    }
}

@main
struct UseMeUpApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @ObservedObject private var store = UsageStore.shared

    var body: some Scene {
        MenuBarExtra {
            PanelView().environmentObject(store)
        } label: {
            // One window owns the dot and the number together: the pinned one,
            // unless another is worse, in which case that one, named.
            HStack(spacing: 3) {
                Image(nsImage: BarDot.image(store.barSeverity))
                Text(store.barText)
            }
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView().environmentObject(store)
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ n: Notification) {
        UsageStore.shared.start()
    }

    func applicationWillTerminate(_ n: Notification) {
        UsageStore.shared.stop()
    }
}
