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
                onClicked: root.refresh()

                PlasmaComponents.ToolTip.text: text
                PlasmaComponents.ToolTip.visible: hovered
                PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
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

            Repeater {
                model: root.providers

                ColumnLayout {
                    required property var modelData

                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
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
                            level: 4
                            text: modelData.displayName
                        }

                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignRight
                            font: Kirigami.Theme.smallFont
                            opacity: 0.7
                            elide: Text.ElideRight
                            text: {
                                if (!modelData.ok)
                                    return "";
                                const checked = root.formatReset(modelData.checkedAt);
                                return modelData.stale ? i18n("stale · %1", checked) : checked;
                            }
                        }
                    }

                    PlasmaComponents.Label {
                        Layout.fillWidth: true
                        visible: !modelData.ok
                        wrapMode: Text.WordWrap
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.negativeTextColor
                        text: modelData.error || i18n("Unavailable")
                    }

                    Repeater {
                        model: modelData.windows

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
                                font: Kirigami.Theme.smallFont
                                opacity: 0.7
                                text: {
                                    const reset = root.formatReset(modelData.resetsAt);
                                    return reset === "" ? "" : i18n("resets %1", reset);
                                }
                            }
                        }
                    }
                }
            }

            PlasmaExtras.PlaceholderMessage {
                Layout.fillWidth: true
                Layout.topMargin: Kirigami.Units.gridUnit * 2
                visible: root.providers.length === 0
                iconName: root.helperError !== "" ? "dialog-warning" : "speedometer"
                text: root.helperError !== "" ? i18n("Helper unavailable") : i18n("No usage data")
                explanation: root.helperError !== "" ? root.helperError : i18n("Check that the ai-usage helper is installed and that you are signed in to a subscription plan.")
            }
        }
    }
}
