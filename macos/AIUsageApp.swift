import AppKit
import ServiceManagement
import SwiftUI

let providerNames = [("codex", "Codex", "Cx"), ("claude", "Claude Code", "Cl"), ("grok", "Grok", "G")]

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
        enabled = saved.map { ids in providerNames.map(\.0).filter { ids.contains($0) } }
            ?? providerNames.map(\.0)
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

    func title(at date: Date) -> String {
        guard !enabled.isEmpty else { return "AI Usage" }
        return providerNames.filter { enabled.contains($0.0) }.map { id, _, short in
            let provider = report?.providers.first { $0.id == id }
            let value = provider?.summary(at: date) ?? (busy ? "…" : "unavailable")
            // Short unavailable marker keeps three providers clear of a laptop's notch.
            return "\(short) \(value == "unavailable" ? "?" : value)\(helperError != nil && provider?.stale != true && provider?.primary(at: date) != nil ? "*" : "")"
        }.joined(separator: "  ")
    }
}

struct ProviderView: View {
    let name: String
    let provider: ProviderUsage?
    let date: Date
    let helperFailed: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(name).font(.headline)
                Spacer()
                if provider?.stale == true || helperFailed {
                    Label("Stale", systemImage: "exclamationmark.triangle")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if let provider {
                let windows = provider.currentWindows(at: date)
                if windows.isEmpty { Text("Usage unavailable").foregroundStyle(.secondary) }
                ForEach(Array(windows.enumerated()), id: \.offset) { _, window in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(window.label)
                            Spacer()
                            if window.usedPercent >= 85 {
                                Image(systemName: "exclamationmark.triangle.fill")
                                    .foregroundStyle(.red).accessibilityLabel("Near usage limit")
                            }
                            Text("\(Int(window.usedPercent.rounded()))% used")
                                .monospacedDigit()
                                .foregroundStyle(window.usedPercent >= 85 ? Color.red : .primary)
                        }
                        ProgressView(value: window.usedPercent, total: 100)
                            .tint(window.usedPercent >= 85 ? .red : window.usedPercent >= 70 ? .orange : .accentColor)
                            .accessibilityLabel(window.label)
                            .accessibilityValue("\(Int(window.usedPercent.rounded())) percent used")
                        if let raw = window.resetsAt, let reset = parseTimestamp(raw) {
                            Text("Resets \(reset, style: .relative)")
                                .font(.caption).foregroundStyle(.secondary)
                                .help(reset.formatted(date: .complete, time: .shortened))
                        }
                    }
                }
                if let resets = provider.bankedResets {
                    Text("Banked resets: \(resets.availableCount)").font(.subheadline)
                    ForEach(Array(resets.credits.enumerated()), id: \.offset) { _, credit in
                        Text(credit.title + (credit.expiresAt.flatMap(parseTimestamp).map {
                            " · expires " + $0.formatted(date: .abbreviated, time: .shortened)
                        } ?? "")).font(.caption).foregroundStyle(.secondary)
                    }
                }
                if let error = provider.error {
                    Text(error).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                }
                if let checked = parseTimestamp(provider.checkedAt) {
                    Text("Checked \(checked, style: .relative) ago")
                        .font(.caption).foregroundStyle(.secondary)
                        .help(checked.formatted(date: .complete, time: .shortened))
                }
            } else {
                Text("No reading yet. Check that the CLI is installed and signed in.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 8)
    }
}

struct UsagePanel: View {
    @ObservedObject var model: UsageModel

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("AI Usage").font(.title2.bold())
                    Spacer()
                    if model.busy { ProgressView().controlSize(.small).accessibilityLabel("Refreshing usage") }
                    Button("Refresh now") { model.refresh(force: true) }
                        .disabled(model.busy || model.enabled.isEmpty)
                        .keyboardShortcut("r")
                }
                Text("Subscription limits · * stale · ! limit at 85% or higher")
                    .font(.caption).foregroundStyle(.secondary)
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        if model.enabled.isEmpty {
                            Text("Select a provider below to show its usage.").padding(.vertical)
                        }
                        ForEach(providerNames.filter { model.enabled.contains($0.0) }, id: \.0) { id, name, _ in
                            ProviderView(name: name, provider: model.report?.providers.first { $0.id == id },
                                         date: context.date, helperFailed: model.helperError != nil)
                            Divider()
                        }
                    }
                }.frame(maxHeight: 440)
                if let error = model.helperError {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.callout).textSelection(.enabled)
                }
                HStack {
                    ForEach(providerNames, id: \.0) { id, name, _ in
                        Toggle(name, isOn: Binding(
                            get: { model.enabled.contains(id) }, set: { _ in model.toggle(id) }
                        ))
                    }
                }.toggleStyle(.checkbox)
                Toggle("Launch at login", isOn: Binding(
                    get: { model.launchAtLogin }, set: { model.setLogin($0) }
                )).toggleStyle(.checkbox)
                if let error = model.loginError { Text(error).font(.caption).textSelection(.enabled) }
                HStack {
                    Text("Uses your signed-in CLIs. No model calls.")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Quit") { NSApplication.shared.terminate(nil) }.keyboardShortcut("q")
                }
            }.padding(16).frame(width: 400).onAppear { model.syncLogin() }
        }
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
            Text(model.title(at: model.displayDate)).monospacedDigit()
                .accessibilityLabel("AI Usage. " + model.title(at: model.displayDate))
        }.menuBarExtraStyle(.window)
    }
}

#endif
