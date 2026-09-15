import ServiceManagement
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var store: UsageStore
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled
    @State private var loginError: String?

    var body: some View {
        Form {
            Picker("Show in the menu bar:", selection: $store.pinnedWindow) {
                ForEach(store.windows) { w in Text(w.label).tag(w.key) }
            }
            .pickerStyle(.radioGroup)

            Text("The icon still turns amber or red for whichever window is worst, so pinning a quiet one cannot hide a busy one.")
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
