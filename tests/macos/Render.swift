// Optional GUI smoke check. Run scripts/test-macos.sh --render in a logged-in session.
import AppKit
import SwiftUI

@main
struct RenderCheck {
    @MainActor static func main() throws {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let date = Date()
        let formatter = ISO8601DateFormatter()
        let checked = formatter.string(from: date.addingTimeInterval(-120))
        let reset = formatter.string(from: date.addingTimeInterval(7200))
        let payload = """
        {"version":1,"providers":[
        {"id":"codex","displayName":"Codex","checkedAt":"\(checked)","stale":false,"primaryLabel":"Session","windows":[{"label":"Session","usedPercent":25,"resetsAt":"\(reset)"},{"label":"Weekly (Codex Spark)","usedPercent":90,"resetsAt":"\(reset)"}],"bankedResets":{"availableCount":1,"credits":[{"title":"Weekly reset","expiresAt":"\(reset)"}]}},
        {"id":"claude","displayName":"Claude Code","checkedAt":"\(checked)","stale":true,"error":"Refresh timed out. Showing the last reading.","primaryLabel":"Session","windows":[{"label":"Session","usedPercent":72,"resetsAt":"\(reset)"}]},
        {"id":"grok","displayName":"Grok","checkedAt":"\(checked)","stale":false,"error":"Signed out. Run grok login in a terminal.","windows":[]}
        ]}
        """
        let model = UsageModel(startPolling: false)
        model.report = try UsageReport.decode(Data(payload.utf8))
        let destination = URL(fileURLWithPath: CommandLine.arguments[1])
        for (name, appearance) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
            app.appearance = NSAppearance(named: appearance)
            let view = NSHostingView(rootView: UsagePanel(model: model))
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 340, height: 640),
                                  styleMask: [.borderless], backing: .buffered, defer: false)
            window.appearance = NSAppearance(named: appearance)
            window.contentView = view
            window.orderFront(nil)
            RunLoop.main.run(until: Date().addingTimeInterval(0.5))
            view.layoutSubtreeIfNeeded()
            let capture = Process()
            capture.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
            capture.arguments = ["-x", "-o", "-l", String(window.windowNumber),
                                 destination.appendingPathComponent("macos-\(name).png").path]
            try capture.run()
            capture.waitUntilExit()
            guard capture.terminationStatus == 0 else {
                throw ReportError.helper("Panel capture needs macOS Screen Recording permission.")
            }
            window.orderOut(nil)
        }
        try renderMenuBar(model.menuEntries(at: date), to: destination)
        print("Rendered macOS light and dark panels and menu bars")
    }
}

/// Draws the status item image on menu bar colored strips at 2x, resolving colors per appearance.
@MainActor func renderMenuBar(_ entries: [MenuEntry], to destination: URL) throws {
    let image = menuBarImage(for: entries)
    for (name, appearance, background) in [
        ("light", NSAppearance.Name.aqua, NSColor(white: 0.93, alpha: 1)),
        ("dark", .darkAqua, NSColor(white: 0.16, alpha: 1)),
    ] {
        let size = NSSize(width: image.size.width + 24, height: 24)
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width * 2),
                                      pixelsHigh: Int(size.height * 2), bitsPerSample: 8,
                                      samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                      colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        bitmap.size = size
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        NSAppearance(named: appearance)!.performAsCurrentDrawingAppearance {
            background.setFill()
            NSRect(origin: .zero, size: size).fill()
            image.draw(in: NSRect(x: 12, y: (size.height - image.size.height) / 2,
                                  width: image.size.width, height: image.size.height))
        }
        NSGraphicsContext.restoreGraphicsState()
        try bitmap.representation(using: .png, properties: [:])!
            .write(to: destination.appendingPathComponent("macos-menubar-\(name).png"))
    }
}
