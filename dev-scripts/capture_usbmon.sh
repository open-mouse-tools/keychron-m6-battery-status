#!/usr/bin/env bash
# Captures usbmon text-format traffic on bus 1 for the Keychron M6 receiver.
# Requires sudo for the usbmon module and reading /sys/kernel/debug/usb/usbmon.
#
# Usage:
#   sudo ./capture_usbmon.sh [duration_seconds]
#
# Default duration: 30 seconds.

set -euo pipefail

DURATION="${1:-30}"
OUTPUT="script-outputs/usbmon_bus1_$(date +%Y%m%d_%H%M%S).txt"
BUS=1            # Bus number from `lsusb -d 3434:d028`

# Ensure usbmon is loaded
if ! grep -q usbmon /proc/modules; then
    echo "Loading usbmon kernel module…"
    modprobe usbmon
fi

# Find the debugfs path
if [[ ! -d /sys/kernel/debug/usb/usbmon ]]; then
    echo "Mounting debugfs…"
    mount -t debugfs none /sys/kernel/debug
fi

mkdir -p "$(dirname "$OUTPUT")"

echo "─────────────────────────────────────────────────────────────"
echo "Capturing usbmon bus ${BUS} for ${DURATION}s → ${OUTPUT}"
echo "─────────────────────────────────────────────────────────────"
echo ""
echo "DO THIS NOW (you have ${DURATION} seconds):"
echo "  1. Open Chrome → https://launcher.keychron.com"
echo "  2. Click 'Connect' / 'Pair Device' if shown"
echo "  3. Select the Keychron receiver from the picker"
echo "  4. Wait for it to show the battery percentage"
echo "  5. If there's a 'refresh' button or similar, click it once"
echo ""
echo "Capturing now…"

timeout "${DURATION}" cat "/sys/kernel/debug/usb/usbmon/${BUS}u" > "$OUTPUT" || true

# Restore user ownership so we can read it later
chown "${SUDO_USER:-$USER}:${SUDO_USER:-$USER}" "$OUTPUT"

LINES=$(wc -l < "$OUTPUT")
SIZE=$(du -h "$OUTPUT" | cut -f1)
echo ""
echo "Capture complete: ${LINES} lines, ${SIZE}"
echo "Saved to: ${OUTPUT}"
echo ""
echo "Filter preview (lines mentioning the receiver, device 018):"
grep -E ":${BUS}:00[1-9]:018|:${BUS}:00[1-9]:01[0-9]" "$OUTPUT" | head -10 || true
