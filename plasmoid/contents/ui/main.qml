/*
 * Keychron M6 Battery Plasmoid
 *
 * Polls a bundled Python script (contents/scripts/keychron_m6_battery.py) that
 * reads the M6's battery percentage either live from the wired HID feature
 * report (0x2A) or from a wireless cache file populated by an optional systemd
 * user service. See the project README for the full protocol reference.
 */

import QtQuick
import QtQuick.Layouts
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    // ── State ──────────────────────────────────────────────────────────────
    property int batteryPercent: -1        // -1 = unknown / no data yet
    property bool deviceAvailable: false
    property string transport: ""          // "usb_direct" | "wireless" | ""
    property int wirelessAgeSeconds: -1    // -1 when not applicable
    property string lastUpdate: ""
    property string lastError: ""
    property string lastErrorCode: ""

    // True when the cable is in: USB-direct mode implies the mouse is being
    // powered / charged over the wire. The widget surfaces this as
    // "Charging" rather than a percentage in the compact view.
    readonly property bool isCharging: deviceAvailable && transport === "usb_direct"

    // ── Configuration (see contents/config/main.xml) ──────────────────────
    readonly property int pollIntervalMs: Plasmoid.configuration.pollIntervalSeconds * 1000
    readonly property int lowBatteryThreshold: Plasmoid.configuration.lowBatteryThreshold
    readonly property bool hideWhenUnavailable: Plasmoid.configuration.hideWhenUnavailable

    // ── Bundled-script path ────────────────────────────────────────────────
    // Strip the file:// scheme to get a usable local-filesystem path for
    // the executable DataSource. We use Qt.resolvedUrl rather than
    // Plasmoid.file() because the latter requires a package-structure
    // declaration we don't ship.
    readonly property string scriptPath: {
        const url = Qt.resolvedUrl("../scripts/keychron_m6_battery.py").toString()
        return url.replace(/^file:\/+/, "/")
    }
    readonly property string command: "python3 '" + scriptPath + "' --json"

    // ── Visibility ─────────────────────────────────────────────────────────
    Plasmoid.status: {
        if (deviceAvailable) return PlasmaCore.Types.ActiveStatus
        if (hideWhenUnavailable) return PlasmaCore.Types.HiddenStatus
        return PlasmaCore.Types.PassiveStatus
    }

    // ── Icon + tooltip in the panel ────────────────────────────────────────
    Plasmoid.icon: {
        if (!deviceAvailable) return "input-mouse-symbolic"
        // Charging icons mirror the discharge tier so the icon still hints
        // at the current level, but with the lightning-bolt overlay.
        if (isCharging) {
            if (batteryPercent < lowBatteryThreshold) return "battery-caution-charging-symbolic"
            if (batteryPercent < 30) return "battery-low-charging-symbolic"
            if (batteryPercent < 90) return "battery-good-charging-symbolic"
            return "battery-full-charging-symbolic"
        }
        if (batteryPercent < lowBatteryThreshold) return "battery-caution-symbolic"
        if (batteryPercent < 30) return "battery-low-symbolic"
        if (batteryPercent < 90) return "battery-good-symbolic"
        return "battery-full-symbolic"
    }

    toolTipMainText: "Keychron M6 8K"
    toolTipSubText: {
        if (!deviceAvailable) {
            if (lastError !== "") return lastError
            return i18n("Not connected (plug in via USB cable, or run the wireless listener)")
        }
        const ageLabel = formatAgeLabel(transport, wirelessAgeSeconds, lastUpdate)
        if (isCharging) {
            // Keep the percentage available on hover even though the panel
            // says "Charging" — useful to know how close to full it is.
            return i18n("Charging · %1%%2", batteryPercent, ageLabel)
        }
        const transportLabel = transport === "wireless" ? i18n("wireless") : ""
        return i18n("Battery: %1% (%2)%3", batteryPercent, transportLabel, ageLabel)
    }

    // ── Helpers ────────────────────────────────────────────────────────────
    function formatAgeLabel(t, ageSec, fallbackTime) {
        if (t === "wireless" && ageSec >= 0) {
            const mins = Math.floor(ageSec / 60)
            if (mins < 1) return " · " + i18n("just updated")
            if (mins < 60) return " · " + i18np("%1m ago", "%1m ago", mins)
            const hours = Math.floor(mins / 60)
            const remMins = mins % 60
            return " · " + i18n("%1h %2m ago", hours, remMins)
        }
        if (fallbackTime) return " · " + fallbackTime
        return ""
    }

    function statusLine() {
        if (lastError !== "") return lastError
        if (!deviceAvailable) return i18n("Plug in the M6 via USB cable to see live readings")
        if (isCharging) return i18n("Charging · %1% · live", batteryPercent)
        if (transport === "wireless") {
            if (wirelessAgeSeconds < 0) return i18n("Wireless · cached")
            const mins = Math.floor(wirelessAgeSeconds / 60)
            if (mins < 1) return i18n("Wireless · just updated")
            if (mins < 60) return i18np("Wireless · %1m ago", "Wireless · %1m ago", mins)
            return i18n("Wireless · %1h %2m ago",
                        Math.floor(mins/60), mins % 60)
        }
        return i18n("Updated %1", lastUpdate)
    }

    function helpHint() {
        if (lastErrorCode === "no_cache") {
            return i18n("Enable the wireless listener: systemctl --user enable --now "
                      + "keychron-m6-battery-listener.service")
        }
        if (lastErrorCode === "permission_denied") {
            return i18n("Install the udev rule from packaging/udev-rules/ and replug the device")
        }
        return ""
    }

    // ── Polling ────────────────────────────────────────────────────────────
    Plasma5Support.DataSource {
        id: probe
        engine: "executable"
        connectedSources: []

        onNewData: function(source, data) {
            const stdout = data["stdout"] || ""
            const stderr = data["stderr"] || ""
            try {
                const payload = JSON.parse(stdout)
                if (payload.ok === true && typeof payload.battery_percent === "number") {
                    root.batteryPercent = payload.battery_percent
                    root.deviceAvailable = true
                    root.transport = payload.transport || ""
                    root.wirelessAgeSeconds =
                        (typeof payload.age_seconds === "number") ? payload.age_seconds : -1
                    root.lastError = ""
                    root.lastErrorCode = ""
                    root.lastUpdate = Qt.formatDateTime(new Date(), "hh:mm")
                } else {
                    root.deviceAvailable = false
                    root.transport = ""
                    root.wirelessAgeSeconds = -1
                    root.lastError = (payload && payload.message) ? payload.message
                                                                  : i18n("device unavailable")
                    root.lastErrorCode = (payload && payload.error) ? payload.error : ""
                }
            } catch (e) {
                root.deviceAvailable = false
                root.transport = ""
                root.wirelessAgeSeconds = -1
                root.lastError = stderr || (i18n("output parse error: ") + e.message)
                root.lastErrorCode = "parse_error"
            }
            disconnectSource(source)
        }

        function poll() { connectSource(root.command) }
    }

    Timer {
        id: pollTimer
        interval: root.pollIntervalMs
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: probe.poll()
    }

    // ── Compact representation (panel) ────────────────────────────────────
    compactRepresentation: MouseArea {
        Layout.minimumWidth: contentRow.implicitWidth + 2 * Kirigami.Units.smallSpacing
        Layout.minimumHeight: contentRow.implicitHeight
        onClicked: root.expanded = !root.expanded

        RowLayout {
            id: contentRow
            anchors.centerIn: parent
            spacing: Kirigami.Units.smallSpacing

            Kirigami.Icon {
                Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                source: root.Plasmoid.icon
                opacity: root.deviceAvailable ? 1.0 : 0.5
            }
            PlasmaComponents.Label {
                text: {
                    if (!root.deviceAvailable) return "—"
                    if (root.isCharging) return i18n("Charging")
                    return root.batteryPercent + "%"
                }
                opacity: root.deviceAvailable ? 1.0 : 0.5
                // Bold the percentage when the battery is low — but don't bold
                // "Charging" since the user already knows the cable is in.
                font.bold: root.deviceAvailable && !root.isCharging
                          && root.batteryPercent < root.lowBatteryThreshold
            }
        }
    }

    // ── Full representation (expanded view) ────────────────────────────────
    fullRepresentation: ColumnLayout {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 16
        Layout.minimumHeight: Kirigami.Units.gridUnit * 10
        spacing: Kirigami.Units.smallSpacing

        Kirigami.Heading {
            Layout.alignment: Qt.AlignHCenter
            level: 3
            text: "Keychron M6 8K"
        }

        Kirigami.Icon {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: Kirigami.Units.iconSizes.huge
            Layout.preferredHeight: Kirigami.Units.iconSizes.huge
            source: root.Plasmoid.icon
            opacity: root.deviceAvailable ? 1.0 : 0.4
        }

        PlasmaComponents.Label {
            Layout.alignment: Qt.AlignHCenter
            font.pointSize: Kirigami.Theme.defaultFont.pointSize * 1.6
            font.bold: true
            text: {
                if (!root.deviceAvailable) return i18n("Not connected")
                if (root.isCharging) return i18n("Charging")
                return root.batteryPercent + "%"
            }
            color: {
                if (!root.deviceAvailable) return Kirigami.Theme.disabledTextColor
                // Don't show charging in the warning colour even if the value
                // is below the threshold — it's actively being charged.
                if (!root.isCharging && root.batteryPercent < root.lowBatteryThreshold)
                    return Kirigami.Theme.negativeTextColor
                return Kirigami.Theme.textColor
            }
        }

        // Smaller line under the big "Charging" / "N%" — shows the
        // percentage in tiny text while charging so the user can still see
        // how full the battery is.
        PlasmaComponents.Label {
            Layout.alignment: Qt.AlignHCenter
            visible: root.isCharging
            text: root.batteryPercent + "%"
            opacity: 0.7
            font.pointSize: Kirigami.Theme.defaultFont.pointSize * 1.1
        }

        PlasmaComponents.Label {
            Layout.alignment: Qt.AlignHCenter
            Layout.maximumWidth: parent.width - Kirigami.Units.largeSpacing * 2
            horizontalAlignment: Text.AlignHCenter
            text: root.statusLine()
            opacity: 0.7
            wrapMode: Text.WordWrap
        }

        PlasmaComponents.Label {
            Layout.alignment: Qt.AlignHCenter
            Layout.maximumWidth: parent.width - Kirigami.Units.largeSpacing * 2
            horizontalAlignment: Text.AlignHCenter
            visible: root.helpHint() !== ""
            text: root.helpHint()
            opacity: 0.6
            font.pointSize: Kirigami.Theme.smallFont.pointSize
            wrapMode: Text.WordWrap
        }

        Item { Layout.fillHeight: true }

        PlasmaComponents.Button {
            Layout.alignment: Qt.AlignHCenter
            text: i18n("Refresh now")
            icon.name: "view-refresh"
            onClicked: probe.poll()
        }
    }
}
