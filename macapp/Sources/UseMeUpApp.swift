import SwiftUI
import UserNotifications

@main
struct UseMeUpApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @ObservedObject private var store = UsageStore.shared
    @ObservedObject private var bar = MenuBarAppearance.shared
    @AppStorage(PillStyle.key) private var styleRaw = PillStyle.fallback.rawValue

    var body: some Scene {
        MenuBarExtra {
            PanelView().environmentObject(store)
        } label: {
            // Every weekly window, side by side, each coloured green, amber or
            // red in the style chosen in Settings. See UsageStore.barSegments
            // for what is shown and why.
            Image(nsImage: BarLabel.image(store.barSegments,
                                          style: PillStyle(rawValue: styleRaw) ?? .fallback,
                                          dark: bar.dark))
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
    private let presenter = NotificationPresenter()

    func applicationDidFinishLaunching(_ n: Notification) {
        UNUserNotificationCenter.current().delegate = presenter
        UsageStore.shared.start()
        MenuBarAppearance.shared.start()
    }

    func applicationWillTerminate(_ n: Notification) {
        UsageStore.shared.stop()
    }
}
