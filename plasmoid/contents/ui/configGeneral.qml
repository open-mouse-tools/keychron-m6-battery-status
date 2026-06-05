import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    property alias cfg_pollIntervalSeconds: pollInterval.value
    property alias cfg_hideWhenUnavailable: hideWhenUnavailable.checked
    property alias cfg_lowBatteryThreshold: lowBatteryThreshold.value

    Kirigami.FormLayout {
        SpinBox {
            id: pollInterval
            Kirigami.FormData.label: i18n("Poll interval (seconds):")
            from: 10
            to: 3600
            stepSize: 10
            value: 300
            // Battery doesn't change quickly. Default 300s = 5 min is plenty.
        }

        Label {
            Kirigami.FormData.label: ""
            Layout.maximumWidth: Kirigami.Units.gridUnit * 22
            text: i18n("How often the plasmoid invokes the reader script. "
                     + "Battery readings change slowly so a low interval is wasteful.")
            wrapMode: Text.WordWrap
            opacity: 0.7
            font.pointSize: Kirigami.Theme.smallFont.pointSize
        }

        Item { Kirigami.FormData.isSection: true }

        SpinBox {
            id: lowBatteryThreshold
            Kirigami.FormData.label: i18n("Low battery threshold (%):")
            from: 1
            to: 50
            value: 20
        }

        CheckBox {
            id: hideWhenUnavailable
            Kirigami.FormData.label: i18n("Hide when unavailable:")
            text: i18n("Hide the panel icon when the mouse is not connected")
        }
    }
}
