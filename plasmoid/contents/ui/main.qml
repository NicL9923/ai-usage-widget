import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.plasma.plasmoid
import "UsageState.js" as UsageState

PlasmoidItem {
    id: root

    readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

    property var readings: []
    property var requests: UsageState.createRequests()
    property int generation: 0
    property bool ready: false
    property bool busy: false
    property double clockNow: Date.now()

    readonly property var enabledProviders: {
        let enabled = [];
        if (Plasmoid.configuration.showCodex)
            enabled.push({id: "codex", displayName: "Codex"});
        if (Plasmoid.configuration.showClaude)
            enabled.push({id: "claude", displayName: "Claude Code"});
        if (Plasmoid.configuration.showGrok)
            enabled.push({id: "grok", displayName: "Grok"});
        return enabled;
    }
    readonly property var providers: enabledProviders.map(function(provider) {
        const reading = root.readings.find(reading => reading.id === provider.id);
        if (reading)
            return reading;
        return Object.assign({}, provider, {hasReading: false, ok: false, stale: false,
            windows: [], bankedResets: null, primaryUsedPercent: null,
            error: root.loaded ? i18n("No usage reading available") : ""});
    })
    property string helperError: ""
    property bool loaded: false

    readonly property string helper: Plasmoid.configuration.helperCommand
    readonly property int pollMinutes: Math.max(1, Plasmoid.configuration.pollIntervalMinutes)

    readonly property bool anyProviderEnabled: Plasmoid.configuration.showCodex || Plasmoid.configuration.showClaude || Plasmoid.configuration.showGrok

    readonly property string providerFlags: {
        let flags = "";
        if (Plasmoid.configuration.showCodex)
            flags += " --provider codex";
        if (Plasmoid.configuration.showClaude)
            flags += " --provider claude";
        if (Plasmoid.configuration.showGrok)
            flags += " --provider grok";
        return flags;
    }

    // Keep the TTL just below the poll interval so each scheduled check refreshes
    // expired readings while sharing recent results with other helper invocations.
    readonly property string pollCommand: helper + providerFlags + " --ttl " + Math.max(30, pollMinutes * 60 - 10)
    readonly property string refreshCommand: helper + providerFlags + " --refresh"

    function usageColor(percent) {
        if (percent >= 85)
            return Kirigami.Theme.negativeTextColor;
        if (percent >= 70)
            return Kirigami.Theme.neutralTextColor;
        return Kirigami.Theme.highlightColor;
    }

    // Provider marks are bundled per id; anything unrecognized simply gets no mark.
    function providerMark(providerId) {
        if (providerId === "codex" || providerId === "claude" || providerId === "grok")
            return Qt.resolvedUrl("../icons/" + providerId + ".svg");
        return "";
    }

    function formatReset(iso) {
        if (!iso)
            return "";
        const when = new Date(iso);
        if (isNaN(when.getTime()))
            return "";
        return Qt.formatDateTime(when, Qt.locale().dateTimeFormat(Locale.ShortFormat));
    }

    function durationText(minutes) {
        if (minutes < 60)
            return i18n("%1m", minutes);
        if (minutes < 1440)
            return i18n("%1h %2m", Math.floor(minutes / 60), minutes % 60);
        return i18n("%1d %2h", Math.floor(minutes / 1440), Math.floor(minutes % 1440 / 60));
    }

    function updatedText(provider) {
        const minutes = UsageState.elapsedMinutes(provider.checkedAt, root.clockNow);
        if (minutes === null)
            return "";
        return provider.stale ? i18n("Last updated %1 ago", durationText(minutes))
                              : i18n("Updated %1 ago", durationText(minutes));
    }

    function resetText(iso) {
        const minutes = UsageState.resetMinutes(iso, root.clockNow);
        if (minutes === null)
            return "";
        if (minutes <= 0)
            return i18n("Reset time passed; awaiting refresh");
        return i18n("Resets in %1", durationText(minutes));
    }

    function providerWarning(provider) {
        if (!provider.hasReading)
            return "";
        return provider.windows.filter(window => window.usedPercent >= 85).map(function(window) {
            return i18n("%1: %2% used", window.label, Math.round(window.usedPercent));
        }).join("\n");
    }

    function providerSummary(provider) {
        if (!provider.hasReading)
            return !root.loaded ? i18n("%1: loading", provider.displayName)
                : i18n("%1: unavailable. %2", provider.displayName, provider.error || "");
        let summary = provider.windows.map(function(window) {
            return i18n("%1: %2% used", window.label, Math.round(window.usedPercent));
        }).join("\n");
        return provider.displayName + "\n" + summary + "\n" + updatedText(provider)
            + (provider.error ? "\n" + provider.error : "");
    }

    function fail(message) {
        root.helperError = message;
        root.readings = root.readings.map(function(provider) {
            return Object.assign({}, provider, {stale: provider.hasReading, error: message});
        });
        root.loaded = true;
    }

    function apply(stdout) {
        try {
            root.readings = UsageState.parsePayload(stdout);
            root.clockNow = Date.now();
            root.helperError = "";
            root.loaded = true;
        } catch (error) {
            fail(i18n("Could not read helper output"));
        }
    }

    function start(request) {
        root.busy = !!root.requests.active;
        if (request)
            executable.connectSource(request.command);
    }

    function requestRefresh(force) {
        if (!root.ready)
            return;
        start(root.requests.request(root.anyProviderEnabled
            ? (force ? root.refreshCommand : root.pollCommand) : null, root.generation));
    }

    Plasma5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []

        onNewData: function(source, data) {
            disconnectSource(source);
            const active = root.requests.active;
            if (!active || active.command !== source)
                return;
            if (active.generation === root.generation && root.anyProviderEnabled) {
                const stdout = (data["stdout"] || "").trim();
                if (stdout.length > 0)
                    root.apply(stdout);
                else {
                    const stderr = (data["stderr"] || "").trim();
                    root.fail(stderr || i18n("Helper produced no output"));
                }
            }
            // Let the executable engine finish disconnecting before reconnecting a
            // queued request that may use the same command string.
            const next = root.requests.complete();
            root.busy = !!root.requests.active;
            if (next)
                Qt.callLater(function() { root.start(next); });
        }
    }

    Timer {
        interval: root.pollMinutes * 60000
        running: root.ready && root.anyProviderEnabled
        repeat: true
        onTriggered: root.requestRefresh(false)
    }

    Timer {
        interval: 30000
        running: root.visible
        repeat: true
        onTriggered: root.clockNow = Date.now()
    }

    onExpandedChanged: root.clockNow = Date.now()
    onPollCommandChanged: {
        root.generation += 1;
        root.helperError = "";
        root.requestRefresh(false);
    }
    onHelperChanged: {
        root.readings = [];
        root.loaded = false;
    }
    Component.onCompleted: {
        root.ready = true;
        root.requestRefresh(false);
    }

    function refresh() {
        requestRefresh(true);
    }

    // Left unset on purpose: Plasma picks compact in a panel and full on the desktop.
    compactRepresentation: CompactRepresentation {}
    fullRepresentation: FullRepresentation {}

    toolTipMainText: i18n("AI Usage")
    toolTipSubText: {
        if (root.helperError !== "")
            return root.helperError;
        if (!root.anyProviderEnabled)
            return i18n("No providers selected");
        if (!root.loaded)
            return i18n("Loading…");
        return root.providers.map(provider => root.providerSummary(provider)).join("\n\n");
    }

    Plasmoid.status: {
        for (const provider of root.providers) {
            if (provider.hasReading && !provider.stale && provider.maxUsedPercent >= 85)
                return PlasmaCore.Types.NeedsAttentionStatus;
        }
        return PlasmaCore.Types.ActiveStatus;
    }

    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: i18n("Refresh now")
            icon.name: "view-refresh"
            enabled: !root.busy && root.anyProviderEnabled
            onTriggered: root.refresh()
        }
    ]
}
