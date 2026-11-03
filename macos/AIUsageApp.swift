import AppKit
import ServiceManagement
import SwiftUI

let providerNames: [(id: String, name: String)] = [("codex", "Codex"), ("claude", "Claude Code"), ("grok", "Grok")]

@MainActor
final class UsageModel: ObservableObject {
    @Published var displayDate = Date()
    @Published var report: UsageReport?
    @Published var busy = false
    @Published var helperError: String?
    @Published var loginError: String?
    @Published var enabled: [String] {
        didSet { defaults.set(enabled, forKey: "providers") }
    }
    @Published var launchAtLogin = SMAppService.mainApp.status == .enabled
    private var timer: Timer?
    private var queuedRefresh = false
    private var queuedForce = false
    private let defaults: UserDefaults
    private let reader: (URL, [String], Bool) throws -> UsageReport

    init(startPolling: Bool = true, defaults: UserDefaults = .standard,
         reader: @escaping (URL, [String], Bool) throws -> UsageReport = {
             try readUsage(resources: $0, providers: $1, refresh: $2)
         }) {
        self.defaults = defaults
        self.reader = reader
        let saved = defaults.stringArray(forKey: "providers")
        enabled = saved.map { ids in providerNames.map(\.id).filter { ids.contains($0) } }
            ?? providerNames.map(\.id)
        syncLogin()
        guard startPolling else { return }
        refresh()
        // Poll at a minute so expired windows disappear promptly; helper TTL is five minutes.
        let timer = Timer(timeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.displayDate = Date()
                self?.refresh()
            }
        }
        timer.tolerance = 5
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { [weak self] _ in Task { @MainActor in self?.refresh() } }
    }

    func toggle(_ id: String) {
        enabled = enabled.contains(id) ? enabled.filter { $0 != id } : enabled + [id]
        if busy { queuedRefresh = true } else { refresh() }
    }

    func refresh(force: Bool = false) {
        guard !busy else {
            if force { queuedRefresh = true; queuedForce = true }
            return
        }
        displayDate = Date()
        guard !enabled.isEmpty else { report = nil; helperError = nil; return }
        busy = true
        let ids = enabled
        guard let resources = Bundle.main.resourceURL else {
            busy = false
            helperError = "App resources are missing. Reinstall AI Usage."
            return
        }
        let reader = self.reader
        DispatchQueue.global(qos: .utility).async {
            let result = Result { try reader(resources, ids, force) }
            DispatchQueue.main.async {
                self.busy = false
                switch result {
                case .success(let report):
                    self.report = report
                    self.helperError = nil
                case .failure(let error): self.helperError = error.localizedDescription
                }
                if self.queuedRefresh {
                    let force = self.queuedForce
                    self.queuedRefresh = false
                    self.queuedForce = false
                    self.refresh(force: force)
                }
            }
        }
    }

    func syncLogin() {
        let status = SMAppService.mainApp.status
        launchAtLogin = status == .enabled
        if status == .requiresApproval {
            loginError = "Approve AI Usage in System Settings > General > Login Items."
        } else if defaults.bool(forKey: "launchAtLogin") && !launchAtLogin {
            loginError = "Launch at login is off in macOS. Enable it again here or in System Settings."
        } else { loginError = nil }
    }

    func setLogin(_ value: Bool) {
        do {
            if value { try SMAppService.mainApp.register() }
            else { try SMAppService.mainApp.unregister() }
            defaults.set(value, forKey: "launchAtLogin")
            loginError = SMAppService.mainApp.status == .requiresApproval
                ? "Approve AI Usage in System Settings > General > Login Items." : nil
        } catch { loginError = error.localizedDescription }
        launchAtLogin = SMAppService.mainApp.status == .enabled
    }

    /// One menu bar slot per enabled provider, in a fixed order so failures never shift the others.
    func menuEntries(at date: Date) -> [MenuEntry] {
        providerNames.filter { enabled.contains($0.id) }.map { id, name in
            let provider = report?.providers.first { $0.id == id }
            let primary = provider?.primary(at: date)
            return MenuEntry(
                id: id, name: name, usedPercent: primary?.usedPercent,
                nearLimit: provider?.nearLimit(at: date) == true,
                // A failed refresh leaves the previous reading on screen; say so.
                stale: primary != nil && (provider?.stale == true || helperError != nil),
                pending: provider == nil && busy
            )
        }
    }

    func accessibilitySummary(at date: Date) -> String {
        let entries = menuEntries(at: date)
        guard !entries.isEmpty else { return "AI Usage. No providers selected." }
        return "AI Usage. " + entries.map(\.spokenSummary).joined(separator: ". ") + "."
    }
}

struct MenuEntry: Equatable {
    let id: String
    let name: String
    let usedPercent: Double?
    let nearLimit: Bool
    let stale: Bool
    let pending: Bool

    var percentText: String {
        if let usedPercent { return "\(Int(usedPercent.rounded()))%" }
        return pending ? "…" : "–"
    }

    var spokenSummary: String {
        guard let usedPercent else { return "\(name) " + (pending ? "loading" : "unavailable") }
        return "\(name) \(Int(usedPercent.rounded())) percent used"
            + (nearLimit ? ", a limit is above 85 percent" : "")
            + (stale ? ", last reading" : "")
    }
}

#if !TESTING
@main
struct AIUsageApp: App {
    @StateObject private var model = UsageModel()

    var body: some Scene {
        MenuBarExtra {
            UsagePanel(model: model)
        } label: {
            Image(nsImage: menuBarImage(for: model.menuEntries(at: model.displayDate)))
                .accessibilityLabel(model.accessibilitySummary(at: model.displayDate))
        }.menuBarExtraStyle(.window)
    }
}

#endif
