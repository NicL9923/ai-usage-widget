import AppKit

/// Provider marks shared with the Plasma applet (Simple Icons and SVG Logos, both CC0).
@MainActor
enum ProviderMark {
    private static var cache: [String: NSImage] = [:]

    /// A template image, so callers tint it. Nil when the bundled SVG is missing or unreadable.
    static func image(_ id: String) -> NSImage? {
        if let cached = cache[id] { return cached }
        guard let url = Bundle.main.url(forResource: id, withExtension: "svg", subdirectory: "icons"),
              let image = NSImage(contentsOf: url), image.isValid else { return nil }
        image.isTemplate = true
        cache[id] = image
        return image
    }

    /// Draws the mark filled with `color`, isolated so the tint never touches the destination.
    nonisolated static func draw(_ mark: NSImage?, in rect: NSRect, color: NSColor) {
        guard let mark else {
            // Keep the slot readable if the SVG is missing: a plain dot in the ring.
            color.setFill()
            NSBezierPath(ovalIn: rect.insetBy(dx: rect.width * 0.3, dy: rect.height * 0.3)).fill()
            return
        }
        let tinted = NSImage(size: rect.size, flipped: false) { bounds in
            mark.draw(in: bounds)
            color.setFill()
            bounds.fill(using: .sourceAtop)
            return true
        }
        tinted.draw(in: rect)
    }
}

private enum Metrics {
    static let height: CGFloat = 18
    static let ring: CGFloat = 16
    static let stroke: CGFloat = 1.6
    static let ringToText: CGFloat = 3
    static let gap: CGFloat = 9
    static let fontSize: CGFloat = 12
}

/// Menu bar colors per usage level. Normal usage stays monochrome like other menu bar items.
func menuBarColor(_ level: UsageLevel) -> NSColor {
    switch level {
    case .normal: return .labelColor
    case .elevated: return .systemOrange
    case .critical: return .systemRed
    }
}

/// Rings with provider marks, mirroring the Plasma panel. The drawing handler runs at draw
/// time, so dynamic colors resolve against the menu bar's current appearance. Everything the
/// handler reads is resolved here on the main actor and captured, never shared.
@MainActor
func menuBarImage(for entries: [MenuEntry]) -> NSImage {
    guard !entries.isEmpty else {
        let image = NSImage(systemSymbolName: "gauge.with.dots.needle.33percent",
                            accessibilityDescription: "AI Usage") ?? NSImage()
        image.isTemplate = true
        return image
    }
    let font = NSFont.monospacedDigitSystemFont(ofSize: Metrics.fontSize, weight: .medium)
    let marks = entries.map { ProviderMark.image($0.id) }
    let texts = entries.map { entry in
        NSAttributedString(string: entry.percentText, attributes: [.font: font])
    }
    let widths = texts.map { Metrics.ring + Metrics.ringToText + ceil($0.size().width) }
    let width = widths.reduce(0, +) + Metrics.gap * CGFloat(entries.count - 1)
    let image = NSImage(size: NSSize(width: width, height: Metrics.height), flipped: false) { _ in
        var x: CGFloat = 0
        for (index, entry) in entries.enumerated() {
            let alpha: CGFloat = entry.usedPercent == nil || entry.stale ? 0.6 : 1
            let label = NSColor.labelColor.withAlphaComponent(alpha)
            let ringRect = NSRect(x: x, y: (Metrics.height - Metrics.ring) / 2,
                                  width: Metrics.ring, height: Metrics.ring)
            let center = NSPoint(x: ringRect.midX, y: ringRect.midY)
            let radius = (Metrics.ring - Metrics.stroke) / 2

            let track = NSBezierPath(ovalIn: ringRect.insetBy(dx: Metrics.stroke / 2, dy: Metrics.stroke / 2))
            track.lineWidth = Metrics.stroke
            NSColor.labelColor.withAlphaComponent(0.22 * alpha).setStroke()
            track.stroke()

            if let used = entry.usedPercent, used > 0 {
                let arc = NSBezierPath()
                arc.appendArc(withCenter: center, radius: radius, startAngle: 90,
                              endAngle: 90 - 360 * CGFloat(min(used, 100)) / 100, clockwise: true)
                arc.lineWidth = Metrics.stroke
                arc.lineCapStyle = .round
                menuBarColor(UsageLevel(used)).withAlphaComponent(alpha).setStroke()
                arc.stroke()
            }

            let markSize = (Metrics.ring - Metrics.stroke * 2) * 0.8
            ProviderMark.draw(marks[index], in: NSRect(x: center.x - markSize / 2, y: center.y - markSize / 2,
                                                   width: markSize, height: markSize), color: label)

            if entry.nearLimit, UsageLevel(entry.usedPercent ?? 0) != .critical {
                // Another limit is critical even though the primary ring may look calm.
                let dot: CGFloat = 5
                NSColor.systemRed.setFill()
                NSBezierPath(ovalIn: NSRect(x: ringRect.maxX - dot + 1, y: ringRect.maxY - dot + 1,
                                            width: dot, height: dot)).fill()
            }

            let text = NSAttributedString(string: entry.percentText, attributes: [
                .font: font,
                .foregroundColor: entry.usedPercent.map { menuBarColor(UsageLevel($0)) }?
                    .withAlphaComponent(alpha) ?? label,
            ])
            let size = text.size()
            text.draw(at: NSPoint(x: ringRect.maxX + Metrics.ringToText,
                                  y: (Metrics.height - size.height) / 2))
            x += widths[index] + Metrics.gap
        }
        return true
    }
    image.accessibilityDescription = "AI Usage"
    return image
}
