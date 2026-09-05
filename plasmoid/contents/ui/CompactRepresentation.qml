import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid

MouseArea {
    id: compact

    readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical
    // Keep every enabled provider in the layout. A failed probe must not make the other
    // entries jump sideways, and an absent reading is never drawn as a 0% ring.
    readonly property var entries: root.providers
    readonly property bool showLogos: Plasmoid.configuration.showProviderLogos
    readonly property int fallbackSize: Kirigami.Units.iconSizes.small
    // Logos need interior room, so the ring grows a little when they are shown.
    readonly property int ringSize: Math.round(Math.max(fallbackSize,
        (vertical ? compact.width : compact.height) * (showLogos ? 0.84 : 0.72)))

    Layout.minimumWidth: vertical ? fallbackSize : Math.max(fallbackSize, layout.implicitWidth)
    Layout.minimumHeight: vertical ? Math.max(fallbackSize, layout.implicitHeight) : fallbackSize
    Layout.preferredWidth: Layout.minimumWidth
    Layout.preferredHeight: Layout.minimumHeight

    activeFocusOnTab: true
    acceptedButtons: Qt.LeftButton | Qt.MiddleButton
    Accessible.role: Accessible.Button
    Accessible.name: i18n("AI Usage")
    Accessible.description: root.toolTipSubText
    Accessible.focusable: true
    Accessible.onPressAction: function() { root.expanded = !root.expanded; }

    onClicked: function (mouse) {
        if (mouse.button === Qt.MiddleButton)
            root.refresh();
        else
            root.expanded = !root.expanded;
    }
    Keys.onReturnPressed: root.expanded = !root.expanded
    Keys.onEnterPressed: root.expanded = !root.expanded
    Keys.onSpacePressed: root.expanded = !root.expanded

    Rectangle {
        anchors.fill: parent
        anchors.margins: Kirigami.Units.smallSpacing
        visible: compact.activeFocus
        color: "transparent"
        border.color: Kirigami.Theme.highlightColor
        border.width: Kirigami.Units.smallSpacing / 4
        radius: Kirigami.Units.smallSpacing
    }

    GridLayout {
        id: layout

        anchors.centerIn: parent
        rows: compact.vertical ? entries.length : 1
        columns: compact.vertical ? 1 : entries.length
        rowSpacing: Kirigami.Units.largeSpacing
        columnSpacing: Kirigami.Units.largeSpacing

        Repeater {
            model: compact.entries

            Item {
                id: providerEntry

                required property var modelData
                readonly property bool hasReading: modelData.hasReading === true
                readonly property string warning: root.providerWarning(modelData)
                readonly property string statusText: hasReading
                    ? i18n("%1%", Math.round(modelData.primaryUsedPercent))
                    : "—"
                readonly property string labelText: (Plasmoid.configuration.showProviderLabels && !compact.vertical
                    ? modelData.displayName + " " : "") + statusText

                Layout.preferredWidth: compact.vertical
                    ? compact.ringSize
                    : compact.ringSize + Kirigami.Units.smallSpacing + status.implicitWidth
                Layout.preferredHeight: compact.vertical
                    ? compact.ringSize + Kirigami.Units.smallSpacing + status.implicitHeight
                        + (providerName.visible ? providerName.implicitHeight : 0)
                    : Math.max(compact.ringSize, status.implicitHeight)
                implicitWidth: Layout.preferredWidth
                implicitHeight: Layout.preferredHeight
                opacity: modelData.stale ? 0.6 : 1

                Item {
                    id: visual

                    width: compact.ringSize
                    height: width
                    anchors.top: parent.top
                    anchors.horizontalCenter: compact.vertical ? parent.horizontalCenter : undefined
                    anchors.left: compact.vertical ? undefined : parent.left

                    UsageRing {
                        anchors.fill: parent
                        visible: providerEntry.hasReading
                        value: providerEntry.hasReading ? providerEntry.modelData.primaryUsedPercent : 0
                        ringColor: providerEntry.hasReading
                            ? root.usageColor(providerEntry.modelData.primaryUsedPercent)
                            : Kirigami.Theme.disabledTextColor
                        trackColor: Kirigami.Theme.textColor
                        markSource: compact.showLogos ? root.providerMark(providerEntry.modelData.id) : ""
                        markColor: Kirigami.Theme.textColor
                    }

                    Kirigami.Icon {
                        anchors.centerIn: parent
                        width: Math.max(Kirigami.Units.iconSizes.small, parent.width * 0.62)
                        height: width
                        visible: !providerEntry.hasReading && root.loaded
                        source: "dialog-warning"
                        color: Kirigami.Theme.negativeTextColor
                    }

                    Kirigami.Icon {
                        anchors.centerIn: parent
                        width: Math.max(Kirigami.Units.iconSizes.small, parent.width * 0.62)
                        height: width
                        visible: !providerEntry.hasReading && !root.loaded
                        source: compact.showLogos
                            ? root.providerMark(providerEntry.modelData.id)
                            : "speedometer"
                        color: Kirigami.Theme.disabledTextColor
                        isMask: true
                    }

                    Kirigami.Icon {
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        width: Kirigami.Units.iconSizes.small
                        height: width
                        visible: providerEntry.warning !== ""
                        source: "dialog-warning"
                        color: Kirigami.Theme.negativeTextColor

                        MouseArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.NoButton
                            hoverEnabled: true

                            PlasmaComponents.ToolTip {
                                text: providerEntry.warning
                                visible: parent.containsMouse
                                delay: Kirigami.Units.toolTipDelay
                            }
                        }
                    }
                }

                PlasmaComponents.Label {
                    id: status

                    anchors.top: compact.vertical ? visual.bottom : undefined
                    anchors.left: compact.vertical ? undefined : visual.right
                    anchors.leftMargin: compact.vertical ? 0 : Kirigami.Units.smallSpacing
                    anchors.verticalCenter: compact.vertical ? undefined : visual.verticalCenter
                    anchors.horizontalCenter: compact.vertical ? parent.horizontalCenter : undefined
                    anchors.topMargin: compact.vertical ? Kirigami.Units.smallSpacing : 0
                    width: compact.vertical ? parent.width : implicitWidth
                    horizontalAlignment: compact.vertical ? Text.AlignHCenter : Text.AlignLeft
                    elide: compact.vertical ? Text.ElideNone : Text.ElideRight
                    fontSizeMode: compact.vertical ? Text.HorizontalFit : Text.FixedSize
                    text: providerEntry.labelText
                    font.pixelSize: Math.max(Kirigami.Theme.defaultFont.pixelSize,
                        Math.round((compact.vertical ? compact.width : compact.height) * 0.45))
                    font.features: { "tnum": 1 }
                    color: providerEntry.hasReading ? Kirigami.Theme.textColor : Kirigami.Theme.disabledTextColor
                }

                PlasmaComponents.Label {
                    id: providerName

                    visible: compact.vertical && Plasmoid.configuration.showProviderLabels
                    anchors.top: status.bottom
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                    font: Kirigami.Theme.smallFont
                    text: providerEntry.modelData.displayName
                }

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.NoButton
                    hoverEnabled: true

                    PlasmaComponents.ToolTip {
                        text: root.providerSummary(providerEntry.modelData)
                        visible: parent.containsMouse
                        delay: Kirigami.Units.toolTipDelay
                    }
                }
            }
        }
    }

    // Keep the applet discoverable before the first result, with no providers selected,
    // or after a helper-level failure.
    Kirigami.Icon {
        anchors.centerIn: parent
        width: compact.fallbackSize
        height: width
        source: root.helperError !== "" ? "dialog-warning" : "speedometer"
        visible: compact.entries.length === 0
    }

    PlasmaComponents.BusyIndicator {
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        width: Kirigami.Units.iconSizes.small
        height: width
        visible: root.busy
        running: visible
        Accessible.name: i18n("Refreshing usage")
    }
}
