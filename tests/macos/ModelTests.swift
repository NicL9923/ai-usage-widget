import Foundation

@main
struct ModelTests {
    @MainActor static func main() throws {
        let name = "ai-usage-model-test-" + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let gate = DispatchSemaphore(value: 0)
        let lock = NSLock()
        var calls: [([String], Bool)] = []
        let report = try UsageReport.decode(Data("{\"version\":1,\"providers\":[]}".utf8))
        let model = UsageModel(startPolling: false, defaults: defaults) { _, ids, force in
            lock.lock()
            calls.append((ids, force))
            let first = calls.count == 1
            lock.unlock()
            if first { _ = gate.wait(timeout: .now() + 3) }
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
        lock.lock()
        let captured = calls
        lock.unlock()
        precondition(captured.count == 2, "Coalesce repeated forced refreshes into one follow-up")
        precondition(!captured[0].1 && captured[1].1, "Retain the forced refresh while busy")
        precondition(!captured[1].0.contains("claude"), "Use the latest provider selection")
        precondition(model.title(at: Date()).contains("Cx ?"), "Keep unavailable provider markers")
        model.toggle("codex")
        model.toggle("grok")
        let end = Date().addingTimeInterval(5)
        while model.busy && Date() < end {
            RunLoop.main.run(until: Date().addingTimeInterval(0.01))
        }
        precondition(model.enabled.isEmpty && model.title(at: Date()) == "AI Usage")
        let reading = try UsageReport.decode(Data("""
        {"version":1,"providers":[{"id":"codex","displayName":"Codex","checkedAt":"","stale":false,"windows":[{"label":"Weekly","usedPercent":25}]}]}
        """.utf8))
        let retryGate = DispatchSemaphore(value: 0)
        var attempts = 0
        let retryModel = UsageModel(startPolling: false, defaults: defaults) { _, _, _ in
            attempts += 1
            if attempts == 1 { throw ReportError.helper("Failed to refresh") }
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
        precondition(retryModel.helperError != nil && retryModel.title(at: Date()).contains("*"))
        retryModel.refresh()
        precondition(retryModel.busy && retryModel.title(at: Date()).contains("*"),
                     "Keep the stale marker while retrying a failed refresh")
        retryGate.signal()
        let retryDeadline = Date().addingTimeInterval(5)
        while retryModel.busy && Date() < retryDeadline {
            RunLoop.main.run(until: Date().addingTimeInterval(0.01))
        }
        precondition(!retryModel.busy && retryModel.helperError == nil)
        precondition(!retryModel.title(at: Date()).contains("*"), "Clear stale only after success")
        print("macOS refresh scheduling tests passed")
    }
}
