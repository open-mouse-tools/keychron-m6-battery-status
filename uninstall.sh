#!/usr/bin/env bash
# Remove the Keychron M6 Battery plasmoid, the wireless listener service,
# and (optionally) the udev rule.
#
# Usage:
#   ./uninstall.sh                   # plasmoid + listener; prompt for udev removal
#   ./uninstall.sh --keep-udev       # leave the udev rule in place
#   ./uninstall.sh --remove-udev     # remove the udev rule unconditionally
#
# Removing the udev rule requires sudo. The wireless cache file is
# automatically wiped from $XDG_RUNTIME_DIR (which is itself ephemeral).

set -euo pipefail

PLUGIN_ID="com.github.open-mouse-tools.keychronm6battery"
UDEV_DST="/etc/udev/rules.d/99-keychron-m6-8k.rules"
SYSTEMD_DST="$HOME/.config/systemd/user/keychron-m6-battery-listener.service"
CACHE_FILE="${XDG_RUNTIME_DIR:-/tmp}/keychron_m6_state.json"

UDEV_ACTION=ask

for arg in "$@"; do
    case "$arg" in
        --keep-udev)    UDEV_ACTION=keep   ;;
        --remove-udev)  UDEV_ACTION=remove ;;
        -h|--help)
            sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)
            echo "unknown argument: $arg" >&2
            exit 1 ;;
    esac
done

# ── 1. Stop and disable the listener (if installed) ─────────────────────────

if systemctl --user list-unit-files --quiet \
        keychron-m6-battery-listener.service >/dev/null 2>&1 \
   || [[ -f "$SYSTEMD_DST" ]]; then
    echo "→ Stopping and disabling listener service …"
    systemctl --user disable --now keychron-m6-battery-listener.service \
        2>/dev/null || true
    rm -f "$SYSTEMD_DST"
    systemctl --user daemon-reload
    echo "  removed"
fi

# ── 2. Remove the plasmoid ──────────────────────────────────────────────────

if kpackagetool6 --type=Plasma/Applet --list 2>/dev/null \
        | grep -q "^$PLUGIN_ID\b"; then
    echo "→ Removing plasmoid …"
    kpackagetool6 --type=Plasma/Applet --remove "$PLUGIN_ID" || true
fi

# ── 3. Cache file ───────────────────────────────────────────────────────────

if [[ -f "$CACHE_FILE" ]]; then
    rm -f "$CACHE_FILE"
    echo "→ Removed cache file $CACHE_FILE"
fi

# ── 4. Udev rule (system-level; requires sudo) ──────────────────────────────

if [[ -f "$UDEV_DST" ]]; then
    if [[ "$UDEV_ACTION" == "ask" ]]; then
        read -r -p "Also remove the system udev rule ($UDEV_DST)? [y/N] " reply
        case "${reply,,}" in
            y|yes) UDEV_ACTION=remove ;;
            *)     UDEV_ACTION=keep   ;;
        esac
    fi
    if [[ "$UDEV_ACTION" == "remove" ]]; then
        echo "→ Removing udev rule (requires sudo) …"
        sudo rm -f "$UDEV_DST"
        sudo udevadm control --reload-rules
        sudo udevadm trigger
        echo "  removed"
    else
        echo "→ Leaving udev rule in place ($UDEV_DST)"
    fi
fi

echo
echo "Uninstall complete."
