import SwiftUI
import UserNotifications

/// One alert the server says currently exists. The server owns the rule and
/// the id (see `alert_for` in panel.py); this side owns only "have I shown it".
struct UsageAlert: Codable, Identifiable {
    let id: String
    let kind: String
    let title: String
    let body: String
}

/// Shows each alert once.
///
/// "Once" is kept here, in UserDefaults, and not on the server, so an alert
/// raised while the app was closed still appears when it opens, and nothing is
/// marked as shown until the system has accepted it. An id is key + window +
/// kind, so a forecast that wobbles across 100 all afternoon is one event.
@MainActor
final class Notifier: ObservableObject {
    static let shared = Notifier()

    @AppStorage("notifyEnabled") var enabled: Bool = true
    @Published private(set) var status: String = "not asked yet"

    private let shownKey = "shownAlertIDs"
    private let keep = 60          // a week holds ~34 five-hour windows; this is plenty

    private var shown: [String] {
        get { UserDefaults.standard.stringArray(forKey: shownKey) ?? [] }
        set { UserDefaults.standard.set(Array(newValue.suffix(keep)), forKey: shownKey) }
    }

    func refreshStatus() async {
        let s = await UNUserNotificationCenter.current().notificationSettings()
        switch s.authorizationStatus {
        case .authorized, .provisional, .ephemeral: status = "allowed"
        case .denied:        status = "turned off for UseMeUp in System Settings → Notifications"
        case .notDetermined: status = "not asked yet"
        @unknown default:    status = "unknown"
        }
    }

    /// Ask only when there is something to say, so the permission prompt
    /// arrives with its reason attached and not on first launch.
    private func allowed() async -> Bool {
        let center = UNUserNotificationCenter.current()
        let s = await center.notificationSettings()
        if s.authorizationStatus == .notDetermined {
            _ = try? await center.requestAuthorization(options: [.alert, .sound])
        }
        await refreshStatus()
        return status == "allowed"
    }

    func deliver(_ alerts: [UsageAlert]) async {
        guard enabled else { return }
        let fresh = alerts.filter { !shown.contains($0.id) }
        guard !fresh.isEmpty, await allowed() else { return }
        for a in fresh {
            let c = UNMutableNotificationContent()
            c.title = a.title.prefix(1).uppercased() + a.title.dropFirst()
            c.body = a.body
            c.sound = a.kind == "spent" ? .default : nil
            let req = UNNotificationRequest(identifier: a.id, content: c, trigger: nil)
            do {
                try await UNUserNotificationCenter.current().add(req)
                shown.append(a.id)          // only once the system has taken it
            } catch {
                status = "could not post: \(error.localizedDescription)"
            }
        }
    }

    /// For the Settings button: proves the path end to end without waiting
    /// for a window to get into trouble.
    func test() async {
        guard await allowed() else { return }
        let c = UNMutableNotificationContent()
        c.title = "UseMeUp notifications are on"
        c.body = "You will hear from this once when a window is forecast to run out, and once if it does."
        try? await UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: "test-\(Date().timeIntervalSince1970)", content: c, trigger: nil))
    }
}

/// Without a delegate macOS suppresses banners from the frontmost app, and a
/// menu bar app counts as frontmost while its panel is open.
final class NotificationPresenter: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(_ c: UNUserNotificationCenter, willPresent n: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .list, .sound])
    }
}
