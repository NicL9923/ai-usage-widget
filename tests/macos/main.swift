import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

let now = parseTimestamp("2026-11-02T12:00:00Z")!
let payload = """
{"version":1,"providers":[{"id":"codex","displayName":"Codex","checkedAt":"2026-11-02T11:59:00.123456+00:00","stale":false,"primaryLabel":"Session","windows":[{"label":"Session","usedPercent":25,"resetsAt":"2026-11-02T13:00:00Z"},{"label":"Weekly","usedPercent":90,"resetsAt":"2026-11-09T12:00:00Z"}],"bankedResets":{"availableCount":1,"credits":[{"title":"Weekly reset","expiresAt":null}]}}]}
"""
require(parseTimestamp("not a timestamp") == nil, "Reject invalid timestamps")
let report = try UsageReport.decode(Data(payload.utf8))
let provider = report.providers[0]
require(provider.summary(at: now) == "25%!", "Flag high secondary limits without changing the primary")
require(provider.bankedResets?.availableCount == 1, "Keep banked reset details")
require(parseTimestamp(provider.checkedAt) != nil, "Decode Python microsecond timestamps")
require(provider.currentWindows(at: parseTimestamp("2026-11-02T13:00:00Z")!).count == 1,
        "Drop expired windows at the exact reset time")
require(provider.summary(at: parseTimestamp("2026-11-02T13:00:00Z")!) == "90%!",
        "Use a current window when the primary period ends")
require(provider.summary(at: parseTimestamp("2026-11-10T12:00:00Z")!) == "unavailable",
        "Never display a previous period's usage")
let stalePayload = payload.replacingOccurrences(of: "\"stale\":false", with: "\"stale\":true")
let stale = try UsageReport.decode(Data(stalePayload.utf8))
require(stale.providers[0].summary(at: now) == "25%!*",
        "Stale readings must be marked")
let emptyPayload = """
{"version":1,"providers":[{"id":"grok","displayName":"Grok","checkedAt":"","stale":false,"error":"No usage yet","windows":[]}]}
"""
let empty = try UsageReport.decode(Data(emptyPayload.utf8))
require(empty.providers[0].summary(at: now) == "unavailable", "Absent never means zero")
for bad in [
    payload.replacingOccurrences(of: "\"version\":1", with: "\"version\":2"),
    payload.replacingOccurrences(of: "\"usedPercent\":25", with: "\"usedPercent\":101"),
    payload.replacingOccurrences(of: "\"usedPercent\":25", with: "\"usedPercent\":-1"),
    payload.replacingOccurrences(of: "\"usedPercent\":25", with: "\"usedPercent\":null")
] {
    do {
        _ = try UsageReport.decode(Data(bad.utf8))
        fatalError("Accepted an invalid contract")
    } catch {}
}
print("macOS contract tests passed")

// Exercise the actual Process bridge, including a valid unavailable report (exit 1)
// and the environment used when launched from Finder with no terminal PATH.
let scratch = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
    .appendingPathComponent("helper-test-" + UUID().uuidString)
try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: scratch) }
let helper = scratch.appendingPathComponent("fake-python")
let script = "#!/bin/sh\n[ \"$PYTHONSAFEPATH\" = 1 ] || exit 12\n[ \"$PYTHONDONTWRITEBYTECODE\" = 1 ] || exit 9\n[ \"$CODEX_HOME\" = /custom/codex ] || exit 10\n[ \"$ANTHROPIC_API_KEY\" != not-an-api-key ] || exit 11\n[ \"$PATH\" = /usr/bin:/bin ] || exit 7\n[ \"$1\" = -m ] || exit 8\nprintf '%s' '" + emptyPayload + "'\nexit 1\n"
try script.write(to: helper, atomically: true, encoding: .utf8)
try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: helper.path)
let config = HelperConfiguration(python: helper.path, path: "/usr/bin:/bin",
                                 environment: ["CODEX_HOME": "/custom/codex", "ANTHROPIC_API_KEY": "not-an-api-key"])
try JSONEncoder().encode(config).write(to: scratch.appendingPathComponent("helper.json"))
let unavailable = try readUsage(resources: scratch, providers: ["grok"], refresh: true)
require(unavailable.providers[0].windows.isEmpty, "Accept unavailable reports with exit 1")
try "#!/bin/sh\nexit 2\n".write(to: helper, atomically: true, encoding: .utf8)
do {
    _ = try readUsage(resources: scratch, providers: ["grok"], refresh: false)
    fatalError("Accepted a broken helper")
} catch {}
print("macOS helper integration tests passed")

try FileManager.default.removeItem(at: helper)
do {
    _ = try readUsage(resources: scratch, providers: ["grok"], refresh: false)
    fatalError("Accepted a missing Python executable")
} catch {
    require(error.localizedDescription.contains("Rerun scripts/install.sh"), "Explain missing Python recovery")
}
