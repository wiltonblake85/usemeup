import AppKit
import Combine
import SwiftUI

/// The menu bar item and its drop-down.
///
/// AppKit rather than SwiftUI's MenuBarExtra, for one reason: placement.
/// macOS puts a new status item at the LEFT end of the status area, and on a
/// notched Mac (and with macOS 27's collapsing of crowded bars) the left end
/// is where items get hidden. A status item's place is remembered under
/// "NSStatusItem Preferred Position <autosaveName>", measured in points from
/// the right edge of the screen. MenuBarExtra gives no way to set the
/// autosave name, so its item could never be placed. This one is named
/// "UseMeUp", and on first launch asks for position 1: the right-most slot
/// macOS gives a third-party item, just left of Control Center and the clock.
///
/// Measured on macOS 27.0 (26A428), 2026-09-25, with a test item: no stored
/// position put it off-screen (collapsed); 1500 did the same; 200 put it at
/// x 1315 of 1728; 1 put it at x 1486, the right-most place available.
///
/// Once the item exists, macOS rewrites that key whenever you Command-drag the
/// item somewhere else, so a place you choose sticks and this never overrides
/// it: the default is written only when the key is missing.
@MainActor
final class StatusItemController: NSObject {
    static let shared = StatusItemController()

    static let autosaveName = "UseMeUp"
    private static var positionKey: String { "NSStatusItem Preferred Position \(autosaveName)" }

    private var item: NSStatusItem?
    private let popover = NSPopover()
    private var watchers = Set<AnyCancellable>()

    func install() {
        guard item == nil else { return }

        // Before the item exists: macOS reads the key when the item is created.
        let d = UserDefaults.standard
        if d.object(forKey: Self.positionKey) == nil {
            d.set(1.0, forKey: Self.positionKey)
        }

        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.autosaveName = Self.autosaveName
        if let b = item.button {
            b.imagePosition = .imageOnly
            b.target = self
            b.action = #selector(toggle(_:))
        }
        self.item = item

        let host = NSHostingController(rootView: PanelView().environmentObject(UsageStore.shared))
        host.sizingOptions = [.preferredContentSize]
        popover.contentViewController = host
        popover.behavior = .transient        // closes on a click anywhere else
        popover.animates = false

        // Redraw the label whenever anything it shows changes. objectWillChange
        // fires before the new value lands, hence the short debounce.
        let prefsChanged = NotificationCenter.default
            .publisher(for: UserDefaults.didChangeNotification).map { _ in () }
        Publishers.MergeMany(
            UsageStore.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
            WindowPrefs.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
            MenuBarAppearance.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
            prefsChanged.eraseToAnyPublisher()
        )
        .debounce(for: .milliseconds(60), scheduler: RunLoop.main)
        .sink { [weak self] in self?.redraw() }
        .store(in: &watchers)

        redraw()
    }

    private func redraw() {
        guard let b = item?.button else { return }
        let store = UsageStore.shared
        let style = PillStyle(rawValue: UserDefaults.standard.string(forKey: PillStyle.key) ?? "")
            ?? .fallback
        b.image = BarLabel.image(store.barSegments, style: style,
                                 dark: MenuBarAppearance.shared.dark,
                                 allClear: store.barAllClear)
        b.setAccessibilityLabel(store.barSpoken)
        b.toolTip = store.barSpoken
    }

    @objc private func toggle(_ sender: Any?) {
        guard let b = item?.button else { return }
        if popover.isShown {
            popover.performClose(sender)
        } else {
            popover.show(relativeTo: b.bounds, of: b, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
        }
    }

    func closePanel() {
        if popover.isShown { popover.performClose(nil) }
    }
}

/// The Settings window, opened from the panel.
///
/// Its own NSWindow rather than SwiftUI's openSettings, which needs a view
/// inside a SwiftUI scene; the panel now lives in an NSPopover.
@MainActor
final class SettingsWindow {
    static let shared = SettingsWindow()
    private var window: NSWindow?

    func show() {
        StatusItemController.shared.closePanel()
        if window == nil {
            let host = NSHostingController(rootView: SettingsView().environmentObject(UsageStore.shared))
            host.sizingOptions = [.preferredContentSize]
            let w = NSWindow(contentViewController: host)
            w.title = "UseMeUp Settings"
            w.styleMask = [.titled, .closable]
            w.isReleasedWhenClosed = false
            w.center()
            window = w
        }
        NSApp.activate()
        window?.makeKeyAndOrderFront(nil)
    }
}
