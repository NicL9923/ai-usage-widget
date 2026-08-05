import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid

MouseArea {
    id: compact

    readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical
    readonly property var entries: root.providers.filter(provider => provider.ok)
    readonly property int ringSize: Math.round(Math.max(Kirigami.Units.iconSizes.small, (vertical ? compact.width : compact.height) * 0.72))

    Layout.minimumWidth: vertical ? 0 : layout.implicitWidth
    Layout.minimumHeight: vertical ? layout.implicitHeight : 0
    Layout.preferredWidth: Layout.minimumWidth
    Layout.preferredHeight: Layout.minimumHeight

    acceptedButtons: Qt.LeftButton | Qt.MiddleButton
    onClicked: function (mouse) {
        if (mouse.button === Qt.MiddleButton)
            root.refresh();
        else
            root.expanded = !root.expanded;
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

            RowLayout {
                required property var modelData

                spacing: Kirigami.Units.smallSpacing
                opacity: modelData.stale ? 0.6 : 1

                UsageRing {
                    Layout.preferredWidth: compact.ringSize
                    Layout.preferredHeight: compact.ringSize
                    Layout.alignment: Qt.AlignVCenter
                    value: modelData.primaryUsedPercent || 0
                    ringColor: root.usageColor(modelData.primaryUsedPercent || 0)
                    trackColor: Kirigami.Theme.textColor
                }

                PlasmaComponents.Label {
                    Layout.alignment: Qt.AlignVCenter
                    text: (Plasmoid.configuration.showProviderLabels ? modelData.displayName + " " : "") + Math.round(modelData.primaryUsedPercent || 0) + "%"
                    font.pixelSize: Math.round(compact.ringSize * 0.62)
                    font.features: { "tnum": 1 }
                }
            }
        }
    }

    // Nothing to show yet, or nothing worked: fall back to a plain icon so the widget is
    // still findable and clickable in the panel.
    Kirigami.Icon {
        anchors.centerIn: parent
        width: compact.ringSize
        height: compact.ringSize
        source: root.helperError !== "" ? "dialog-warning" : "speedometer"
        visible: compact.entries.length === 0
    }
}
