import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.plasma.plasmoid

PlasmoidItem {
    id: root

    readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

    property var providers: []
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

    // The helper caches, so polling costs nothing until the TTL lapses. Keeping the TTL
    // just under the poll interval means every tick gets a genuinely fresh reading.
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

    function apply(stdout) {
        let payload;
        try {
            payload = JSON.parse(stdout);
        } catch (error) {
            // Keep the last good reading on screen rather than blanking the panel.
            root.helperError = i18n("Could not read helper output");
            root.loaded = true;
            return;
        }
        root.providers = payload.providers || [];
        root.helperError = "";
        root.loaded = true;
    }

    Plasma5Support.DataSource {
        id: executable

        engine: "executable"
        connectedSources: []

        onNewData: function (source, data) {
            disconnectSource(source);
            const stdout = (data["stdout"] || "").trim();
            if (stdout.length > 0) {
                root.apply(stdout);
                return;
            }
            const stderr = (data["stderr"] || "").trim();
            root.helperError = stderr.length > 0 ? stderr : i18n("Helper produced no output");
            root.loaded = true;
        }

        function run(command) {
            // With no providers selected the helper would fall back to querying all of
            // them, so refuse to run rather than showing what was just turned off.
            if (!root.anyProviderEnabled) {
                root.providers = [];
                root.helperError = "";
                root.loaded = true;
                return;
            }
            if (connectedSources.indexOf(command) === -1)
                connectSource(command);
        }
    }

    Timer {
        interval: root.pollMinutes * 60000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: executable.run(root.pollCommand)
    }

    // Reflect config changes (provider toggles, helper path) without waiting a full tick.
    onPollCommandChanged: executable.run(root.pollCommand)

    function refresh() {
        executable.run(root.refreshCommand);
    }

    // Left unset on purpose: Plasma picks compact in a panel and full on the desktop.
    compactRepresentation: CompactRepresentation {}
    fullRepresentation: FullRepresentation {}

    toolTipMainText: i18n("AI Usage")
    toolTipSubText: {
        if (root.helperError !== "")
            return root.helperError;
        if (!root.loaded)
            return i18n("Loading…");
        if (root.providers.length === 0)
            return i18n("No providers configured");
        return root.providers.map(function (provider) {
            if (!provider.ok)
                return i18n("%1: unavailable", provider.displayName);
            return provider.windows.map(function (window) {
                return i18n("%1 — %2: %3% used", provider.displayName, window.label, Math.round(window.usedPercent));
            }).join("\n");
        }).join("\n");
    }

    Plasmoid.status: {
        for (const provider of root.providers) {
            if (provider.ok && provider.maxUsedPercent >= 85)
                return PlasmaCore.Types.NeedsAttentionStatus;
        }
        return PlasmaCore.Types.ActiveStatus;
    }

    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: i18n("Refresh now")
            icon.name: "view-refresh"
            onTriggered: root.refresh()
        }
    ]
}
