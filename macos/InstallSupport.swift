// Stop only the installation being replaced. Other copies may be running for development.
import AppKit

let destination = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
let applications = NSRunningApplication.runningApplications(
    withBundleIdentifier: "com.nicl9923.aiusage.macos"
).filter { $0.bundleURL?.standardizedFileURL == destination }
for application in applications { application.terminate() }
let deadline = Date().addingTimeInterval(10)
while applications.contains(where: { !$0.isTerminated }) && Date() < deadline {
    RunLoop.current.run(until: Date().addingTimeInterval(0.1))
}
if applications.contains(where: { !$0.isTerminated }) {
    fputs("AI Usage did not quit. Quit it and rerun the installer.\n", stderr)
    exit(1)
}
