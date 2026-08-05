import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

Kirigami.FormLayout {
    id: page

    property alias cfg_pollIntervalMinutes: pollInterval.value
    property alias cfg_showCodex: showCodex.checked
    property alias cfg_showClaude: showClaude.checked
    property alias cfg_showProviderLabels: showProviderLabels.checked
    property alias cfg_showProviderLogos: showProviderLogos.checked
    property alias cfg_helperCommand: helperCommand.text

    QQC2.CheckBox {
        id: showCodex
        Kirigami.FormData.label: i18n("Providers:")
        text: i18n("Codex")
    }

    QQC2.CheckBox {
        id: showClaude
        text: i18n("Claude Code")
    }

    Item {
        Kirigami.FormData.isSection: true
    }

    QQC2.SpinBox {
        id: pollInterval
        Kirigami.FormData.label: i18n("Check every:")
        from: 1
        to: 120
        textFromValue: (value) => i18np("%1 minute", "%1 minutes", value)
        valueFromText: (text) => parseInt(text, 10)
    }

    QQC2.CheckBox {
        id: showProviderLogos
        Kirigami.FormData.label: i18n("Panel:")
        text: i18n("Show provider logos inside the rings")
    }

    QQC2.CheckBox {
        id: showProviderLabels
        text: i18n("Show provider names next to percentages")
    }

    Item {
        Kirigami.FormData.isSection: true
    }

    QQC2.TextField {
        id: helperCommand
        Kirigami.FormData.label: i18n("Helper command:")
        Layout.fillWidth: true
    }

    QQC2.Label {
        Layout.fillWidth: true
        Layout.maximumWidth: Kirigami.Units.gridUnit * 22
        wrapMode: Text.WordWrap
        font: Kirigami.Theme.smallFont
        opacity: 0.7
        text: i18n("Path to the ai-usage helper. It reads limits through the Codex and Claude CLIs and consumes no tokens.")
    }
}
