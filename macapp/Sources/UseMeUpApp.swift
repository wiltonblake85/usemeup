import SwiftUI

@main
struct UseMeUpApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @ObservedObject private var store = UsageStore.shared

    var body: some Scene {
        MenuBarExtra {
            PanelView().environmentObject(store)
        } label: {
            // Every weekly window, side by side, each in its own green,
            // amber or red pill. See UsageStore.barSegments for what is shown and why.
            Image(nsImage: BarLabel.image(store.barSegments))
                .accessibilityLabel(store.barSpoken)
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
