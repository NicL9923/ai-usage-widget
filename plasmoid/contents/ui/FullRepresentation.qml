import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras
import org.kde.plasma.plasmoid

PlasmaExtras.Representation {
    id: full

    Layout.minimumWidth: Kirigami.Units.gridUnit * 20
    Layout.minimumHeight: Kirigami.Units.gridUnit * 12
    Layout.preferredWidth: Kirigami.Units.gridUnit * 24
    // Size the popup to its content, then start scrolling instead of growing further.
    Layout.preferredHeight: Math.min(Kirigami.Units.gridUnit * 30,
                                     content.implicitHeight + full.header.height
                                         + Kirigami.Units.gridUnit * 2)

    header: PlasmaExtras.PlasmoidHeading {
        RowLayout {
            anchors.fill: parent

            Kirigami.Heading {
                Layout.fillWidth: true
                level: 2
                text: i18n("AI Usage")
                elide: Text.ElideRight
            }

            PlasmaComponents.ToolButton {
                icon.name: "view-refresh"
                display: PlasmaComponents.AbstractButton.IconOnly
                text: i18n("Refresh now")
                enabled: !root.busy && root.anyProviderEnabled
                onClicked: root.refresh()

                PlasmaComponents.ToolTip.text: text
                PlasmaComponents.ToolTip.visible: hovered
                PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
            }

            PlasmaComponents.BusyIndicator {
                Layout.preferredWidth: Kirigami.Units.iconSizes.small
                Layout.preferredHeight: Kirigami.Units.iconSizes.small
                visible: root.busy
                running: visible
                Accessible.name: i18n("Refreshing usage")
            }
        }
    }

    contentItem: PlasmaComponents.ScrollView {
        id: scroll

        contentWidth: availableWidth
        // Size hints come from the Layout properties above; deriving them from the content
        // here as well would loop, since the content is sized to the available space.
        implicitWidth: 0
        implicitHeight: 0

        leftPadding: Kirigami.Units.largeSpacing
        rightPadding: Kirigami.Units.largeSpacing
        topPadding: Kirigami.Units.smallSpacing
        bottomPadding: Kirigami.Units.largeSpacing

        ColumnLayout {
            id: content

            width: scroll.contentWidth
            spacing: Kirigami.Units.largeSpacing

            RowLayout {
                Layout.fillWidth: true
                visible: root.helperError !== ""
                spacing: Kirigami.Units.smallSpacing

                Kirigami.Icon {
                    Layout.preferredWidth: Kirigami.Units.iconSizes.small
                    Layout.preferredHeight: Kirigami.Units.iconSizes.small
                    source: "dialog-warning"
                    color: Kirigami.Theme.negativeTextColor
                }

                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    color: Kirigami.Theme.negativeTextColor
                    text: root.helperError
                }
            }

            Repeater {
                model: root.providers

                ColumnLayout {
                    id: providerCard

                    required property var modelData
                    readonly property bool hasReading: modelData.hasReading === true
                    readonly property string warning: root.providerWarning(modelData)

                    Layout.fillWidth: true
                    visible: root.loaded
                    spacing: Kirigami.Units.smallSpacing
                    opacity: modelData.stale ? 0.6 : 1

                    RowLayout {
                        Layout.fillWidth: true

                        Kirigami.Icon {
                            Layout.preferredWidth: Kirigami.Units.iconSizes.small
                            Layout.preferredHeight: Kirigami.Units.iconSizes.small
                            visible: source.toString() !== ""
                            source: root.providerMark(modelData.id)
                            color: Kirigami.Theme.textColor
                            isMask: true
                        }

                        Kirigami.Heading {
                            Layout.fillWidth: true
                            level: 4
                            text: modelData.displayName
                            Accessible.description: root.providerSummary(modelData)
                        }

                        Kirigami.Icon {
                            Layout.preferredWidth: Kirigami.Units.iconSizes.small
                            Layout.preferredHeight: Kirigami.Units.iconSizes.small
                            visible: providerCard.warning !== ""
                            source: "dialog-warning"
                            color: Kirigami.Theme.negativeTextColor

                            MouseArea {
                                anchors.fill: parent
                                acceptedButtons: Qt.NoButton
                                hoverEnabled: true

                                PlasmaComponents.ToolTip {
                                    text: providerCard.warning
                                    visible: parent.containsMouse
                                    delay: Kirigami.Units.toolTipDelay
                                }
                            }
                        }
                    }

                    PlasmaComponents.Label {
                        Layout.fillWidth: true
                        visible: providerCard.hasReading
                        horizontalAlignment: Text.AlignRight
                        font: Kirigami.Theme.smallFont
                        opacity: 0.7
                        text: root.updatedText(modelData)

                        MouseArea {
                            id: updatedHover

                            anchors.fill: parent
                            acceptedButtons: Qt.NoButton
                            hoverEnabled: true

                            PlasmaComponents.ToolTip {
                                text: root.formatReset(modelData.checkedAt)
                                visible: updatedHover.containsMouse && text !== ""
                                delay: Kirigami.Units.toolTipDelay
                            }
                        }
                    }

                    PlasmaComponents.Label {
                        Layout.fillWidth: true
                        visible: !providerCard.hasReading || modelData.error
                        wrapMode: Text.WordWrap
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.negativeTextColor
                        text: modelData.error || i18n("Unavailable")
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: Kirigami.Units.largeSpacing
                        visible: providerCard.hasReading && modelData.bankedResets !== null
                                 && modelData.bankedResets !== undefined
                        spacing: 0

                        RowLayout {
                            Layout.fillWidth: true

                            PlasmaComponents.Label {
                                Layout.fillWidth: true
                                text: i18n("Banked resets")
                            }

                            PlasmaComponents.Label {
                                text: modelData.bankedResets
                                    ? i18n("%1 available", modelData.bankedResets.availableCount)
                                    : ""
                                color: Kirigami.Theme.highlightColor
                            }
                        }

                        Repeater {
                            model: modelData.bankedResets ? modelData.bankedResets.credits : []

                            PlasmaComponents.Label {
                                required property var modelData

                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignRight
                                font: Kirigami.Theme.smallFont
                                opacity: 0.7
                                text: {
                                    const expiry = root.formatReset(modelData.expiresAt);
                                    return expiry === ""
                                        ? modelData.title
                                        : i18n("%1 · expires %2", modelData.title, expiry);
                                }

                                MouseArea {
                                    id: expiryHover

                                    anchors.fill: parent
                                    acceptedButtons: Qt.NoButton
                                    hoverEnabled: true

                                    PlasmaComponents.ToolTip {
                                        text: root.formatReset(modelData.expiresAt)
                                        visible: expiryHover.containsMouse && text !== ""
                                        delay: Kirigami.Units.toolTipDelay
                                    }
                                }
                            }
                        }
                    }

                    Repeater {
                        model: providerCard.hasReading ? modelData.windows : []

                        ColumnLayout {
                            required property var modelData

                            Layout.fillWidth: true
                            Layout.topMargin: Kirigami.Units.largeSpacing
                            spacing: 0

                            RowLayout {
                                Layout.fillWidth: true

                                PlasmaComponents.Label {
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                    text: modelData.label
                                }

                                PlasmaComponents.Label {
                                    text: i18n("%1% used", Math.round(modelData.usedPercent))
                                    color: root.usageColor(modelData.usedPercent)
                                }
                            }

                            PlasmaComponents.ProgressBar {
                                Layout.fillWidth: true
                                from: 0
                                to: 100
                                value: modelData.usedPercent

                                Accessible.role: Accessible.ProgressBar
                                Accessible.name: modelData.label
                                Accessible.description: i18n("%1% used", Math.round(modelData.usedPercent))
                            }

                            PlasmaComponents.Label {
                                Layout.fillWidth: true
                                visible: text !== ""
                                horizontalAlignment: Text.AlignRight
                                wrapMode: Text.WordWrap
                                font: Kirigami.Theme.smallFont
                                opacity: 0.7
                                text: {
                                    const reset = root.resetText(modelData.resetsAt);
                                    return reset;
                                }

                                MouseArea {
                                    id: resetHover

                                    anchors.fill: parent
                                    acceptedButtons: Qt.NoButton
                                    hoverEnabled: true

                                    PlasmaComponents.ToolTip {
                                        text: root.formatReset(modelData.resetsAt)
                                        visible: resetHover.containsMouse && text !== ""
                                        delay: Kirigami.Units.toolTipDelay
                                    }
                                }
                            }
                        }
                    }
                }
            }

            PlasmaExtras.PlaceholderMessage {
                Layout.fillWidth: true
                Layout.topMargin: Kirigami.Units.gridUnit * 2
                visible: root.anyProviderEnabled && !root.loaded
                iconName: "speedometer"
                text: i18n("Loading usage")
                explanation: i18n("Checking your configured providers…")
            }

            PlasmaExtras.PlaceholderMessage {
                Layout.fillWidth: true
                Layout.topMargin: Kirigami.Units.gridUnit * 2
                visible: !root.anyProviderEnabled
                iconName: "speedometer"
                text: i18n("No providers selected")
                explanation: i18n("Choose at least one provider in the widget settings.")
            }

            PlasmaExtras.PlaceholderMessage {
                Layout.fillWidth: true
                Layout.topMargin: Kirigami.Units.gridUnit * 2
                visible: root.loaded && root.anyProviderEnabled && root.providers.length === 0
                iconName: root.helperError !== "" ? "dialog-warning" : "speedometer"
                text: root.helperError !== "" ? i18n("Helper unavailable") : i18n("No usage data")
                explanation: root.helperError !== "" ? root.helperError : i18n("Check that the ai-usage helper is installed and that you are signed in to a subscription plan.")
            }
        }
    }
}
