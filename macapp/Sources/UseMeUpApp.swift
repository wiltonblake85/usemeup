import SwiftUI
import UserNotifications

@main
struct UseMeUpApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    // The menu bar item is AppKit (StatusItemController), not MenuBarExtra,
    // so the app can name it and ask for a place at the right end of the bar.
    // The one SwiftUI scene left is Settings, which SettingsWindow opens.
    var body: some Scene {
        Settings {
            SettingsView().environmentObject(UsageStore.shared)
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let presenter = NotificationPresenter()

    func applicationDidFinishLaunching(_ n: Notification) {
        UNUserNotificationCenter.current().delegate = presenter
        StatusItemController.shared.install()
        UsageStore.shared.start()
        MenuBarAppearance.shared.start()
    }

    func applicationWillTerminate(_ n: Notification) {
        UsageStore.shared.stop()
    }
}
