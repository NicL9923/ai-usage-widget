import AppKit
import SwiftUI

extension UsageLevel {
    var color: Color {
        switch self {
        case .normal: return .accentColor
        case .elevated: return .orange
        case .critical: return .red
        }
    }
}

private let resetStyle = Date.RelativeFormatStyle(presentation: .named, unitsStyle: .wide)

private func percent(_ value: Double) -> String { "\(Int(value.rounded()))%" }

struct ProviderLogo: View {
    let id: String

    var body: some View {
        if let mark = ProviderMark.image(id) {
            Image(nsImage: mark).resizable().renderingMode(.template).scaledToFit()
                .accessibilityHidden(true)
        } else {
            Image(systemName: "circle.fill").resizable().scaledToFit().padding(4)
                .accessibilityHidden(true)
        }
    }
}

struct UsageRing: View {
    let id: String
    let usedPercent: Double?
    var size: CGFloat = 40
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let line = size * 0.11
        ZStack {
            Circle().stroke(Color.primary.opacity(0.12), lineWidth: line)
            Circle()
                .trim(from: 0, to: (usedPercent ?? 0) / 100)
                .stroke(UsageLevel(usedPercent ?? 0).color,
                        style: StrokeStyle(lineWidth: line, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .animation(reduceMotion ? nil : .easeOut(duration: 0.4), value: usedPercent)
            ProviderLogo(id: id)
                .frame(width: size * 0.44, height: size * 0.44)
                .foregroundStyle(usedPercent == nil ? .tertiary : .primary)
        }
        .padding(line / 2)
        .frame(width: size, height: size)
    }
}

struct UsageBar: View {
    let usedPercent: Double

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.1))
                Capsule().fill(UsageLevel(usedPercent).color)
                    .frame(width: max(geometry.size.height, geometry.size.width * usedPercent / 100))
                    .opacity(usedPercent > 0 ? 1 : 0)
            }
        }
        .frame(height: 5)
    }
}

/// One secondary limit, e.g. the weekly window under a session primary.
struct WindowRow: View {
    let window: UsageWindow

    var body: some View {
        let level = UsageLevel(window.usedPercent)
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 4) {
                Text(window.label).lineLimit(1).truncationMode(.middle)
                Spacer(minLength: 8)
                if level == .critical {
                    Image(systemName: "exclamationmark.triangle.fill").imageScale(.small)
                        .accessibilityLabel("Near usage limit")
                }
                Text(percent(window.usedPercent)).monospacedDigit().fontWeight(.medium)
            }
            .font(.callout)
            .foregroundStyle(level == .critical ? Color.red : .primary)
            UsageBar(usedPercent: window.usedPercent)
            if let reset = window.resetsAt.flatMap(parseTimestamp) {
                Text("Resets \(reset.formatted(resetStyle))")
                    .font(.caption).foregroundStyle(.secondary)
                    .help(reset.formatted(date: .complete, time: .shortened))
            }
        }
        .accessibilityElement(children: .combine)
    }
}

struct ProviderCard: View {
    let name: String
    let id: String
    let provider: ProviderUsage?
    let date: Date
    let busy: Bool
    let helperFailed: Bool

    private var primary: UsageWindow? { provider?.primary(at: date) }

    private var subtitle: String {
        guard provider != nil else {
            return busy ? "Checking usage…" : "No reading yet. Check that the CLI is signed in."
        }
        guard let primary else { return "Usage unavailable" }
        guard let reset = primary.resetsAt.flatMap(parseTimestamp) else { return primary.label }
        return "\(primary.label) · resets \(reset.formatted(resetStyle))"
    }

    private var notice: String? {
        if let error = provider?.error { return error }
        if helperFailed && primary != nil { return "Showing the last reading." }
        return nil
    }

    var body: some View {
        let stale = primary != nil && (provider?.stale == true || helperFailed)
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                UsageRing(id: id, usedPercent: primary?.usedPercent).opacity(stale ? 0.55 : 1)
                VStack(alignment: .leading, spacing: 2) {
                    Text(name).font(.headline)
                    Text(subtitle).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                        .help(primary?.resetsAt.flatMap(parseTimestamp)?
                            .formatted(date: .complete, time: .shortened) ?? "")
                }
                Spacer(minLength: 8)
                if let primary {
                    Text(percent(primary.usedPercent))
                        .font(.system(.title2, design: .rounded).weight(.semibold))
                        .monospacedDigit()
                        .foregroundStyle(UsageLevel(primary.usedPercent).color)
                        .opacity(stale ? 0.55 : 1)
                } else if provider != nil {
                    Text("–").font(.system(.title2, design: .rounded)).foregroundStyle(.tertiary)
                        .accessibilityLabel("Unavailable")
                }
            }
            .accessibilityElement(children: .combine)

            let others = (provider?.currentWindows(at: date) ?? []).filter {
                $0.label != primary?.label
            }
            ForEach(Array(others.enumerated()), id: \.offset) { _, window in
                WindowRow(window: window)
            }

            if let resets = provider?.bankedResets {
                VStack(alignment: .leading, spacing: 2) {
                    Label(resets.availableCount == 1 ? "1 banked reset"
                          : "\(resets.availableCount) banked resets",
                          systemImage: "arrow.counterclockwise.circle")
                        .font(.callout)
                    ForEach(Array(resets.credits.enumerated()), id: \.offset) { _, credit in
                        Text(credit.title + (credit.expiresAt.flatMap(parseTimestamp).map {
                            ", expires " + $0.formatted(date: .abbreviated, time: .shortened)
                        } ?? ""))
                        .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }

            if let notice {
                Label(notice, systemImage: stale ? "clock.arrow.circlepath" : "exclamationmark.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.primary.opacity(0.05))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.06))
        )
    }
}

struct UsagePanel: View {
    @ObservedObject var model: UsageModel

    private var lastChecked: Date? {
        let enabled = Set(model.enabled)
        return model.report?.providers.filter { enabled.contains($0.id) && !$0.windows.isEmpty }
            .compactMap { parseTimestamp($0.checkedAt) }.min()
    }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            VStack(alignment: .leading, spacing: 12) {
                header
                if let error = model.helperError {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .textSelection(.enabled)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.orange.opacity(0.14),
                                    in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
                if model.enabled.isEmpty {
                    VStack(spacing: 6) {
                        Image(systemName: "gauge.with.dots.needle.33percent")
                            .font(.largeTitle).foregroundStyle(.secondary)
                        Text("No providers selected").font(.headline)
                        Text("Choose one from the settings menu.")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 24)
                } else {
                    ScrollView {
                        VStack(spacing: 8) {
                            ForEach(providerNames.filter { model.enabled.contains($0.id) }, id: \.id) {
                                id, name in
                                ProviderCard(
                                    name: name, id: id,
                                    provider: model.report?.providers.first { $0.id == id },
                                    date: context.date, busy: model.busy,
                                    helperFailed: model.helperError != nil
                                )
                            }
                        }
                    }
                    .frame(maxHeight: 460)
                    .fixedSize(horizontal: false, vertical: true)
                }
                if let error = model.loginError {
                    Label(error, systemImage: "person.badge.clock").font(.caption)
                        .foregroundStyle(.secondary).textSelection(.enabled)
                }
                footer
            }
            .padding(14)
            .frame(width: 340)
            .onAppear { model.syncLogin() }
        }
    }

    private var header: some View {
        HStack(spacing: 6) {
            Text("AI Usage").font(.title3.weight(.semibold))
            Spacer()
            Button { model.refresh(force: true) } label: {
                ZStack {
                    // Keep the button the same size while the spinner shows.
                    Image(systemName: "arrow.clockwise").opacity(model.busy ? 0 : 1)
                    if model.busy { ProgressView().controlSize(.small) }
                }
                .frame(width: 22, height: 22)
                .contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .disabled(model.busy || model.enabled.isEmpty)
            .keyboardShortcut("r")
            .help("Refresh now")
            .accessibilityLabel(model.busy ? "Refreshing usage" : "Refresh now")

            Menu {
                Section("Show in menu bar") {
                    ForEach(providerNames, id: \.id) { id, name in
                        Toggle(name, isOn: Binding(
                            get: { model.enabled.contains(id) }, set: { _ in model.toggle(id) }
                        ))
                    }
                }
                Divider()
                Toggle("Launch at login", isOn: Binding(
                    get: { model.launchAtLogin }, set: { model.setLogin($0) }
                ))
            } label: {
                Image(systemName: "gearshape").frame(width: 22, height: 22)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("Settings")
            .accessibilityLabel("Settings")
        }
    }

    private var footer: some View {
        HStack {
            Group {
                if let checked = lastChecked {
                    Text("Updated \(checked.formatted(.relative(presentation: .named)))")
                        .help(checked.formatted(date: .complete, time: .shortened))
                } else {
                    Text("Uses your signed-in CLIs. No model calls.")
                }
            }
            .font(.caption).foregroundStyle(.secondary)
            Spacer()
            Button("Quit") { NSApplication.shared.terminate(nil) }
                .buttonStyle(.borderless)
                .font(.caption)
                .foregroundStyle(.secondary)
                .keyboardShortcut("q")
        }
    }
}
