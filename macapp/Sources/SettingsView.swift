import ServiceManagement
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var store: UsageStore
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled
    @State private var loginError: String?

    var body: some View {
        Form {
            LabeledContent("Menu bar") { Text(store.barSpoken).foregroundStyle(.secondary) }

            Text("Every weekly window is always shown, the model-scoped one first, so a nearly spent model cannot hide what is left for the others. The 5-hour window joins them only while it is amber or red. Each sits on its own green, amber or red background, so it reads the same over any wallpaper.")
                .font(.system(size: 10)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Divider().padding(.vertical, 4)

            Toggle("Open at login", isOn: $launchAtLogin)
                .onChange(of: launchAtLogin) { _, want in setLogin(want) }
            if let e = loginError {
                Text(e).font(.system(size: 10)).foregroundStyle(.red)
            }

            Divider().padding(.vertical, 4)

            LabeledContent("Server") { Text(linkText).foregroundStyle(.secondary) }
        }
        .formStyle(.grouped)
        .frame(width: 380)
        .padding(.vertical, 8)
    }

    private var linkText: String {
        switch store.link {
        case .starting:       return "starting…"
        case .attached:       return "joined the one already running on 8787"
        case .spawned:        return "started by this app on 8787"
        case .failed(let w):  return w
        }
    }

    private func setLogin(_ want: Bool) {
        do {
            loginError = nil
            if want { try SMAppService.mainApp.register() }
            else    { try SMAppService.mainApp.unregister() }
        } catch {
            loginError = error.localizedDescription
            launchAtLogin = SMAppService.mainApp.status == .enabled
        }
    }
}
