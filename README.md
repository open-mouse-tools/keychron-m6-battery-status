# Keychron M6 Battery

A small KDE Plasma 6 panel widget that shows the battery level of a **Keychron M6 8K** wireless mouse on Linux.

The official Keychron Launcher is a Chrome-only web app, and Linux has no built-in driver for the M6's battery reporting. This widget fills that gap: it sits in your panel as a battery icon and percentage, refreshes automatically, and tells you when the mouse is getting low.

> [!NOTE]
> **This is for the Keychron M6 8K specifically.** Other Keychron M-series mice (M3, M4, M7) use different USB protocols and won't work with this widget.

---

## What you get

- A **panel icon + percentage** that updates every few minutes.
- An **expanded view** when you click the icon — bigger reading, last-update time, manual refresh button.
- **Works whether the mouse is wired or wireless**, with a small caveat about wireless (explained below).
- **Configurable** — right-click the widget to set poll interval, low-battery warning threshold, and whether to hide when the mouse is unplugged.

### Wired vs. wireless — the short version

- **Plugged in by cable** (charging): you get a fresh reading every poll, on demand.
- **Wireless (through the receiver)**: the receiver only reports battery at certain moments (when the mouse wakes from sleep, when the percentage changes). A small background service catches those moments and remembers the latest reading. In normal use you'll see the value update every few minutes, sometimes longer.

---

## Install

You'll need:

- **KDE Plasma 6.0 or newer** (Plasma 6.5 / LTS is fine).
- **Python 3.8 or newer** (already installed on virtually every modern Linux distro).
- A **terminal** for the install command.

### One-command install

Open a terminal and run:

```bash
git clone https://github.com/open-mouse-tools/keychron-m6-battery-status.git
cd keychron-m6-battery-status
./install.sh
```

The installer will:

1. Add the widget to KDE (no password required).
2. Install a system-level permission rule so the widget can read the mouse (asks for your password once).
3. Ask whether you want to enable wireless support. **Say yes** unless you only ever use the mouse plugged in.

After it finishes:

1. **Unplug and replug the mouse / receiver.** This is the only way for the permission rule to apply to the device that's already connected.
2. **Right-click your panel** (the bar at the bottom or top of the screen) → **"Add or Manage Widgets"** → search for **"Keychron M6 Battery"** → drag it onto the panel.

That's it. The icon should appear and show your battery within a few seconds.

### What if I'm only ever going to charge the mouse via cable?

You can skip the wireless service:

```bash
./install.sh --no-listener
```

The widget will then only show a battery value while the cable is connected.

---

## Using the widget

### Panel icon

A small mouse-or-battery icon plus a percentage. The icon turns red and the number goes bold when the battery is below 20% (configurable).

### Click for details

Clicking the panel icon opens a bigger view with:

- The current percentage in large text.
- Whether the reading is **Live · wired** (cable is in right now) or **Wireless · 3m ago** (the most recent wireless reading and how old it is).
- A **Refresh now** button — useful right after you plug the cable in.

### Right-click → Configure

- **Poll interval** (default: 5 minutes). How often the widget checks. Battery doesn't change quickly, so a low number here just wastes electricity.
- **Low battery threshold** (default: 20%). Below this, the icon turns red.
- **Hide when unavailable**. If checked, the widget completely hides itself when the mouse isn't reachable. Off by default so you know it's still there.

---

## Troubleshooting

### "Not connected" — but the mouse is in my hand, working fine

A few things to check, in order:

1. **Did you unplug and replug after running the installer?** The permission rule only applies to *newly-connected* devices. Pull the cable / receiver out for two seconds and plug it back in.

2. **Is the mouse really wireless right now?** The M6 has a **slider on the bottom** that switches between wired and wireless modes. If the slider is on "wireless" and you plugged in the cable just to charge, the mouse is still talking to the computer over the receiver, not the cable.

3. **Wireless service running?** If you skipped the listener during install, the widget can't read battery wirelessly. Re-run the installer with `./install.sh --listener` to add it.

4. **Wireless service hasn't seen any battery frames yet?** The receiver only sends battery status at certain moments. Try this:
   - Use the mouse for a few seconds.
   - Stop touching it for about 30 seconds so it falls asleep.
   - Click the mouse to wake it.
   - Wait a couple of minutes, then click "Refresh now" in the widget.

### The widget doesn't appear in the "Add Widgets" picker

Restart Plasma's shell:

```bash
kquitapp6 plasmashell && kstart plasmashell
```

Or simply log out and back in.

### "command not found: kpackagetool6" when running install.sh

You're either not on Plasma 6 yet, or the KDE CLI tools aren't installed.

- **Arch / Manjaro**: `sudo pacman -S kde-cli-tools`
- **Ubuntu / Debian** (Plasma 6): `sudo apt install kde-cli-tools`
- **Fedora KDE**: usually pre-installed; otherwise `sudo dnf install kde-cli-tools`

### The widget says "permission denied" or similar after install

The permission rule didn't apply. Either:

- Unplug and replug the mouse / receiver, or
- Reboot once and the rule will apply automatically.

### Widget shows old data forever

If the wireless reading is "5h ago" or more, the background service might have stopped. Check it:

```bash
systemctl --user status keychron-m6-battery-listener.service
```

If it says **failed** or **inactive**, restart it:

```bash
systemctl --user restart keychron-m6-battery-listener.service
```

If it crashes immediately, run it manually to see what's wrong:

```bash
python3 dev-scripts/keychron_m6_battery.py --listen --verbose
```

---

## Uninstall

```bash
cd keychron-m6-battery-status
./uninstall.sh
```

This removes the widget and the background service. It will ask whether you also want to remove the system permission rule (`/etc/udev/rules.d/99-keychron-m6-8k.rules`) — say yes unless you're planning to reinstall later.

---

## Advanced

### Command-line use

The same Python script that powers the widget is usable standalone.

```bash
# Single reading (auto-pick wired or wireless)
python3 dev-scripts/keychron_m6_battery.py

# JSON output
python3 dev-scripts/keychron_m6_battery.py --json

# Watch the battery, print whenever it changes
python3 dev-scripts/keychron_m6_battery.py --watch --interval 30
```

Run `python3 dev-scripts/keychron_m6_battery.py --help` for the full list.

Exit codes: `0` = success, `1` = no device connected, `2` = device connected but read failed.

### Manual install (without the install.sh script)

```bash
# 1. The widget itself
kpackagetool6 --type=Plasma/Applet --install plasmoid

# 2. The udev rule (lets your user account read the mouse without sudo)
sudo cp packaging/udev-rules/99-keychron-m6-8k.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
# … then unplug + replug the device …

# 3. The wireless listener service (optional)
mkdir -p ~/.config/systemd/user
cp packaging/systemd-user/keychron-m6-battery-listener.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now keychron-m6-battery-listener.service
```

### Verifying each piece

Useful if something doesn't work and you want to narrow it down:

```bash
# 1. Can the script see the mouse?
python3 dev-scripts/keychron_m6_battery.py --json
# Expect: {"ok": true, "battery_percent": 95, …}

# 2. Is the wireless listener alive?
systemctl --user status keychron-m6-battery-listener.service
# Expect: "Active: active (running)"

# 3. What's in the wireless cache?
cat "${XDG_RUNTIME_DIR:-/tmp}/keychron_m6_state.json"
# Expect: a JSON object with battery_percent
```

---

## How it works (technical)

The widget is a small QML plasmoid that calls a Python helper script on a timer and parses the JSON it returns. The script talks to the mouse over Linux's `hidraw` interface using two transports:

- **Wired** (mouse plugged in, USB-direct, PID `0xD049`): issues an `HIDIOCGFEATURE` ioctl on report `0x2A` of the vendor interface (usage page `0xFF0B`) and reads byte 9 of the response.
- **Wireless** (receiver, PID `0xD028`): a long-running listener daemon (the systemd user service) opens the receiver's vendor interface (usage page `0xFFC1`), passively reads input reports, filters for `B6 E2 ?? 01 ??` frames, and stores byte 5 as the battery percentage in a cache file under `$XDG_RUNTIME_DIR`.

Both wire formats were reverse-engineered from the Keychron Launcher's minified JavaScript (`launcher.keychron.com`) and verified against `usbmon` USB packet captures. **No proprietary tools, datasheets, or NDAs were involved.**

### Reverse-engineering trail

The [`dev-scripts/`](dev-scripts/) directory contains every probe used to discover the protocol — preserved so the same approach can be applied to other Keychron mice. In rough order:

1. `keychron_hid_probe.py` — blind enumeration of receiver interfaces; identified the 5 hidraw nodes and their usage pages.
2. `keychron_m6_interrupt_probe.py` + `keychron_m6_qmk_subcmd_probe.py` — focused writes on the QMK Raw HID interface; found the `0xB2` identify and `0xB3` firmware commands.
3. `keychron_m6_dual_channel_probe.py` + `keychron_m6_property_sweep.py` — full sweep of the vendor channel's properties (and *inadvertently reset DPI* by writing to a write-property — moral: assume any sub-command might be a write).
4. Reading the Launcher's JavaScript directly — decoded the 64-byte command frame, the workMode XOR, the checksum routine, and the response layout.
5. `keychron_m6_battery_query.py` + `keychron_m6_connect_state.py` — attempted to replicate the Launcher protocol via raw `hidraw` writes; the device rejected our frames.
6. `keychron_m6_usb_direct_probe.py` + `keychron_m6_feature_read.py` — wired the mouse via cable; found that `HIDIOCGFEATURE` on report `0x2A` returns a status block with battery at byte 9. **First working reading.**
7. `dev-scripts/capture_usbmon.sh` + analysis — captured the Launcher's actual USB traffic while reading battery wirelessly. Discovered the spontaneous `B6 E2 …` frames the receiver pushes. This made the wireless listener possible.

---

## Repository layout

```
keychron-m6-battery-status/
├── README.md                # this file
├── CHANGELOG.md
├── LICENSE
├── install.sh
├── uninstall.sh
├── dev-scripts/
│   ├── keychron_m6_battery.py        # the production reader
│   ├── capture_usbmon.sh             # USB capture helper
│   └── keychron_m6_*_probe.py        # exploratory probes
├── plasmoid/
│   ├── metadata.json
│   └── contents/
│       ├── config/                   # KConfig schema + dispatcher
│       ├── scripts/                  # symlinked to dev-scripts
│       └── ui/                       # main.qml + configGeneral.qml
├── packaging/
│   ├── udev-rules/99-keychron-m6-8k.rules
│   └── systemd-user/keychron-m6-battery-listener.service
└── profiles/keychron_m6.yaml         # historical (non-functional, see file)
```

---

## Credits

- **[hi-drawbridge](https://github.com/devopyos/hi-drawbridge)** — the inspiration. It supports the Keychron M7 and bridges battery data to KDE's BatteryWatch over D-Bus. The M6's protocol unfortunately doesn't quite fit hi-drawbridge's existing YAML schema, so this widget took a different shape. See [`profiles/keychron_m6.yaml`](profiles/keychron_m6.yaml) for notes on why.
- **Keychron Launcher** at `launcher.keychron.com` — the only publicly-available reference for the M6's protocol.

---

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE) for the full text.

---

## Contributing

Issues and pull requests welcome at [github.com/open-mouse-tools/keychron-m6-battery-status](https://github.com/open-mouse-tools/keychron-m6-battery-status).

Particularly useful contributions:

- **Other Keychron M-series mice** (M3 / M4 / M7) — the reverse-engineering toolkit in `dev-scripts/` should work as a starting point.
- **Screenshots** for the README.
- **Translations** — the QML uses Qt's standard `i18n()` calls, so adding `.po` files is straightforward.
- **A real hi-drawbridge profile** for the M6 (would require extending hi-drawbridge's probe-path model — see `profiles/keychron_m6.yaml` for what's needed).
