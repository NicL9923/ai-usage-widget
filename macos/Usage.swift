import Foundation

struct UsageWindow: Decodable {
    let label: String
    let usedPercent: Double
    let resetsAt: String?

    func isCurrent(at date: Date) -> Bool {
        guard let resetsAt, let reset = parseTimestamp(resetsAt) else { return true }
        return reset > date
    }
}

/// Matches the Plasma widget: elevated from 70%, critical from 85%.
enum UsageLevel: Equatable {
    case normal, elevated, critical

    init(_ usedPercent: Double) {
        self = usedPercent >= 85 ? .critical : usedPercent >= 70 ? .elevated : .normal
    }
}

struct BankedResets: Decodable {
    struct Credit: Decodable {
        let title: String
        let expiresAt: String?
    }
    let availableCount: Int
    let credits: [Credit]
}

struct ProviderUsage: Decodable, Identifiable {
    let id: String
    let displayName: String
    let checkedAt: String
    let stale: Bool
    let error: String?
    let primaryLabel: String?
    let windows: [UsageWindow]
    let bankedResets: BankedResets?

    func currentWindows(at date: Date) -> [UsageWindow] {
        windows.filter { $0.isCurrent(at: date) }
    }

    func primary(at date: Date) -> UsageWindow? {
        let current = currentWindows(at: date)
        return current.first { $0.label == primaryLabel } ?? current.first
    }

    /// Any current limit at the critical level, including ones other than the primary.
    func nearLimit(at date: Date) -> Bool {
        currentWindows(at: date).contains { UsageLevel($0.usedPercent) == .critical }
    }
}

struct UsageReport: Decodable {
    let version: Int
    let providers: [ProviderUsage]

    static func decode(_ data: Data) throws -> UsageReport {
        let report = try JSONDecoder().decode(UsageReport.self, from: data)
        guard report.version == 1,
              Set(report.providers.map(\.id)).count == report.providers.count,
              report.providers.allSatisfy({ provider in
                  provider.windows.allSatisfy {
                      $0.usedPercent.isFinite && (0...100).contains($0.usedPercent)
                  }
              }) else { throw ReportError.invalidContract }
        return report
    }
}

enum ReportError: LocalizedError {
    case invalidContract
    case helper(String)

    var errorDescription: String? {
        switch self {
        case .invalidContract: return "The helper returned an unsupported usage report."
        case .helper(let message): return message
        }
    }
}

func parseTimestamp(_ value: String) -> Date? {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: value) { return date }
    formatter.formatOptions = [.withInternetDateTime]
    return formatter.date(from: value)
}

struct HelperConfiguration: Codable {
    let python: String
    let path: String
    var environment: [String: String]? = nil
}

/// Runs off the UI thread. Exit 1 is a valid report with no available providers.
func readUsage(resources: URL, providers: [String], refresh: Bool) throws -> UsageReport {
    let config = try JSONDecoder().decode(
        HelperConfiguration.self,
        from: Data(contentsOf: resources.appendingPathComponent("helper.json"))
    )
    guard FileManager.default.isExecutableFile(atPath: config.python) else {
        throw ReportError.helper("Python at \(config.python) is missing. Rerun scripts/install.sh.")
    }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: config.python)
    process.arguments = ["-m", "ai_usage", "--timeout", "20"]
        + providers.flatMap { ["--provider", $0] }
        + (refresh ? ["--refresh"] : [])
    var environment = ProcessInfo.processInfo.environment
    // Only saved configuration paths, never a captured shell environment or API keys.
    for key in ["CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"] {
        if let value = config.environment?[key] { environment[key] = value }
    }
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PATH"] = config.path
    environment["PYTHONPATH"] = resources.appendingPathComponent("helper").path
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONSAFEPATH"] = "1"
    process.environment = environment
    // Finder/login launches have a different cwd and a minimal PATH.
    process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
    process.standardInput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    let output = Pipe()
    process.standardOutput = output
    try process.run()
    let watchdog = DispatchWorkItem { if process.isRunning { process.terminate() } }
    DispatchQueue.global().asyncAfter(deadline: .now() + 60, execute: watchdog)
    let data = output.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    watchdog.cancel()
    guard process.terminationReason == .exit, (0...1).contains(process.terminationStatus) else {
        throw ReportError.helper("The usage helper stopped. Check the CLI sign-ins and reinstall if Python moved.")
    }
    return try UsageReport.decode(data)
}
