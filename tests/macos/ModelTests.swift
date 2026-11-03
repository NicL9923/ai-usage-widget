import Foundation

/// Reader calls run on a background queue; record them behind a lock.
final class Recorder<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [Value] = []

    /// Appends and returns the new count.
    func append(_ value: Value) -> Int {
        lock.lock(); defer { lock.unlock() }
        values.append(value)
        return values.count
    }

    var all: [Value] { lock.lock(); defer { lock.unlock() }; return values }
}

@main
struct ModelTests {
    @MainActor static func main() throws {
        let name = "ai-usage-model-test-" + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let gate = DispatchSemaphore(value: 0)
        let calls = Recorder<([String], Bool)>()
        let report = try UsageReport.decode(Data("{\"version\":1,\"providers\":[]}".utf8))
        let model = UsageModel(startPolling: false, defaults: defaults) { _, ids, force in
            if calls.append((ids, force)) == 1 { _ = gate.wait(timeout: .now() + 3) }
            return report
        }
        model.refresh()
        model.refresh(force: true)
        model.refresh(force: true)
        model.toggle("claude")
        gate.signal()
        let deadline = Date().addingTimeInterval(5)
        while model.busy && Date() < deadline {
            RunLoop.main.run(until: Date().addingTimeInterval(0.01))
        }
        precondition(!model.busy, "Refreshes did not complete")
        let captured = calls.all
        precondition(captured.count == 2, "Coalesce repeated forced refreshes into one follow-up")
        precondition(!captured[0].1 && captured[1].1, "Retain the forced refresh while busy")
        precondition(!captured[1].0.contains("claude"), "Use the latest provider selection")
        let entries = model.menuEntries(at: Date())
        precondition(entries.map(\.id) == ["codex", "grok"] && entries.allSatisfy { $0.percentText == "–" },
                     "Keep unavailable providers in place without a number")
        precondition(model.accessibilitySummary(at: Date()).contains("Codex unavailable"))
        for (id, _) in providerNames {
            precondition(ProviderMark.image(id) != nil, "Bundle a mark for \(id)")
        }
        model.toggle("codex")
        model.toggle("grok")
        let end = Date().addingTimeInterval(5)
        while model.busy && Date() < end {
            RunLoop.main.run(until: Date().addingTimeInterval(0.01))
        }
        precondition(model.enabled.isEmpty && model.menuEntries(at: Date()).isEmpty)
        let reading = try UsageReport.decode(Data("""
        {"version":1,"providers":[{"id":"codex","displayName":"Codex","checkedAt":"","stale":false,"windows":[{"label":"Weekly","usedPercent":25}]}]}
        """.utf8))
        let retryGate = DispatchSemaphore(value: 0)
        let attempts = Recorder<Void>()
        let retryModel = UsageModel(startPolling: false, defaults: defaults) { _, _, _ in
            if attempts.append(()) == 1 { throw ReportError.helper("Failed to refresh") }
            _ = retryGate.wait(timeout: .now() + 3)
            return reading
        }
        // Enable a provider explicitly; the previous model saved an empty selection.
        retryModel.enabled = ["codex"]
        retryModel.report = reading
        retryModel.refresh()
        let failedDeadline = Date().addingTimeInterval(5)
        while retryModel.busy && Date() < failedDeadline {
            RunLoop.main.run(until: Date().addingTimeInterval(0.01))
        }
        precondition(retryModel.helperError != nil && retryModel.menuEntries(at: Date())[0].stale)
        retryModel.refresh()
        precondition(retryModel.busy && retryModel.menuEntries(at: Date())[0].stale,
                     "Keep the stale marker while retrying a failed refresh")
        retryGate.signal()
        let retryDeadline = Date().addingTimeInterval(5)
        while retryModel.busy && Date() < retryDeadline {
            RunLoop.main.run(until: Date().addingTimeInterval(0.01))
        }
        precondition(!retryModel.busy && retryModel.helperError == nil)
        let fresh = retryModel.menuEntries(at: Date())[0]
        precondition(!fresh.stale && fresh.percentText == "25%", "Clear stale only after success")
        print("macOS refresh scheduling tests passed")
    }
}
