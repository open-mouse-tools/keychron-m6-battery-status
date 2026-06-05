#!/usr/bin/env bash
# Install / upgrade the Keychron M6 Battery plasmoid package, the udev rule,
# and (optionally) the wireless listener systemd user service.
#
# Usage:
#   ./install.sh                   # plasmoid + udev rule, prompt for listener
#   ./install.sh --no-listener     # skip the listener service
#   ./install.sh --listener        # install + enable the listener service
#   ./install.sh --plasmoid-only   # skip udev + listener (assume already done)
#
# Exit codes:
#   0  everything requested was installed
#   1  a prerequisite is missing (kpackagetool6, python3, etc.)
#   2  installation step failed
#
# Re-running this is safe: kpackagetool6 will upgrade an existing install in
# place, and copying the udev rule / systemd unit is idempotent.

set -euo pipefail

PLUGIN_ID="com.github.open-mouse-tools.keychronm6battery"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UDEV_SRC="$REPO_DIR/packaging/udev-rules/99-keychron-m6-8k.rules"
UDEV_DST="/etc/udev/rules.d/99-keychron-m6-8k.rules"
SYSTEMD_SRC="$REPO_DIR/packaging/systemd-user/keychron-m6-battery-listener.service"
SYSTEMD_DST="$HOME/.config/systemd/user/keychron-m6-battery-listener.service"

INSTALL_LISTENER=ask
INSTALL_UDEV=true

for arg in "$@"; do
    case "$arg" in
        --listener)        INSTALL_LISTENER=yes ;;
        --no-listener)     INSTALL_LISTENER=no  ;;
        --plasmoid-only)   INSTALL_LISTENER=no; INSTALL_UDEV=false ;;
        -h|--help)
            sed -n '2,15p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)
            echo "unknown argument: $arg" >&2
            exit 1 ;;
    esac
done

# ── Sanity checks ────────────────────────────────────────────────────────────

need() {
    command -v "$1" >/dev/null 2>&1 \
        || { echo "missing prerequisite: $1" >&2; exit 1; }
}
need kpackagetool6
need python3

# ── 1. Plasmoid package ─────────────────────────────────────────────────────

echo "→ Installing plasmoid …"
if kpackagetool6 --type=Plasma/Applet --list 2>/dev/null \
        | grep -q "^$PLUGIN_ID\b"; then
    kpackagetool6 --type=Plasma/Applet --upgrade "$REPO_DIR/plasmoid" \
        || { echo "plasmoid upgrade failed" >&2; exit 2; }
    echo "  upgraded existing plasmoid"
else
    kpackagetool6 --type=Plasma/Applet --install "$REPO_DIR/plasmoid" \
        || { echo "plasmoid install failed" >&2; exit 2; }
    echo "  fresh install complete"
fi

# ── 2. Udev rule ────────────────────────────────────────────────────────────

if [[ "$INSTALL_UDEV" == "true" ]]; then
    if cmp -s "$UDEV_SRC" "$UDEV_DST" 2>/dev/null; then
        echo "→ Udev rule already in place"
    elif [[ -e "$UDEV_DST" ]]; then
        echo "→ A different file already exists at $UDEV_DST"
        echo "  Refusing to overwrite. Inspect it with:"
        echo "    diff $UDEV_DST $UDEV_SRC"
        echo "  Then either remove the destination or merge the rules manually."
        exit 2
    else
        echo "→ Installing udev rule (requires sudo) …"
        sudo cp "$UDEV_SRC" "$UDEV_DST"
        sudo udevadm control --reload-rules
        sudo udevadm trigger
        echo "  rule installed — unplug + replug the receiver / cable for it to apply"
    fi
fi

# ── 3. Systemd user service (optional, for wireless support) ────────────────

prompt_listener() {
    cat <<EOF

The wireless listener is a small Python daemon that watches the receiver's
HID interface for battery status frames and caches them. Without it, the
plasmoid will only show readings when the M6 is connected via USB cable.

EOF
    read -r -p "Enable the wireless listener now? [Y/n] " reply
    case "${reply,,}" in
        ""|y|yes) INSTALL_LISTENER=yes ;;
        *)        INSTALL_LISTENER=no  ;;
    esac
}

if [[ "$INSTALL_LISTENER" == "ask" ]]; then
    prompt_listener
fi

if [[ "$INSTALL_LISTENER" == "yes" ]]; then
    echo "→ Installing systemd user service …"
    mkdir -p "$(dirname "$SYSTEMD_DST")"
    cp "$SYSTEMD_SRC" "$SYSTEMD_DST"
    systemctl --user daemon-reload
    systemctl --user enable --now keychron-m6-battery-listener.service
    sleep 0.5
    if systemctl --user is-active --quiet keychron-m6-battery-listener.service; then
        echo "  listener running"
    else
        echo "  listener installed but failed to start; check"
        echo "    systemctl --user status keychron-m6-battery-listener.service"
    fi
fi

# ── Final pointer ───────────────────────────────────────────────────────────

cat <<EOF

Done.

Add the widget: right-click your panel / desktop → "Add Widgets" → search
"Keychron M6 Battery" → drag it onto the panel.

Verify the reader from a terminal:
  python3 $REPO_DIR/dev-scripts/keychron_m6_battery.py

EOF
