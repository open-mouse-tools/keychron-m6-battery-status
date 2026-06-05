#!/usr/bin/env python3
"""
Keychron Ultra-Link 8K — HID Battery Report Discovery Script
=============================================================
Probes all hidraw interfaces for the Ultra-Link 8K receiver (VID 0x3434, PID 0xD028)
and tries common feature report IDs to locate battery data.

Requirements:
    # Either of these Python bindings works (script auto-detects):
    pip install hid        # apmorton — uses hid.device()
    pip install hidapi     # trezor   — uses hid.Device(...)
    # hidapi system library must also be present:
    # Arch:   sudo pacman -S hidapi
    # Debian: sudo apt install libhidapi-hidraw0
    # Fedora: sudo dnf install hidapi

Usage:
    python3 keychron_hid_probe.py
    # If permission denied on /dev/hidraw*:
    python3 keychron_hid_probe.py   # re-run after: sudo chmod a+r /dev/hidraw*
    # Or prefix with: sudo python3 keychron_hid_probe.py

What it does:
    1. Enumerates all HID devices matching the receiver VID/PID
    2. For each interface, tries GET_FEATURE on report IDs 0x01–0x1F
    3. Also tries sending known Keychron/vendor HID commands via interrupt write
    4. Dumps raw bytes of any non-trivial response
    5. Highlights bytes that look like a battery percentage (0–100)
"""

import hid
import sys
import time


# ── HID binding compatibility shim ─────────────────────────────────────────────
# Two packages register themselves under the import name `hid`:
#   • `hid`    (apmorton)   uses hid.device() + dev.open_path(path)
#   • `hidapi` (trezor)     uses hid.Device(path=path)   (capital D)
# Detect which is present and wrap them in a uniform interface.

_HID_FLAVOR = "device" if hasattr(hid, "device") else ("Device" if hasattr(hid, "Device") else None)
if _HID_FLAVOR is None:
    print("\033[91mError: neither hid.device nor hid.Device is available.\033[0m")
    print("Install one of:  pip install hid     (apmorton)")
    print("             or: pip install hidapi  (trezor)")
    sys.exit(1)


class HidDev:
    """Uniform wrapper over both HID Python bindings."""

    def __init__(self, path):
        if _HID_FLAVOR == "device":
            self._dev = hid.device()
            self._dev.open_path(path)
        else:
            self._dev = hid.Device(path=path)

    def set_nonblocking(self, flag):
        if hasattr(self._dev, "set_nonblocking"):
            self._dev.set_nonblocking(flag)

    def write(self, data):
        return self._dev.write(bytes(data))

    def read(self, size, timeout_ms=None):
        if timeout_ms is None:
            return self._dev.read(size)
        # apmorton: read(size, timeout_ms=...) ; trezor: read(size, timeout=...)
        try:
            return self._dev.read(size, timeout_ms=timeout_ms)
        except TypeError:
            return self._dev.read(size, timeout=timeout_ms)

    def get_feature_report(self, report_id, size):
        return self._dev.get_feature_report(report_id, size)

    def close(self):
        self._dev.close()

# ── Target device ──────────────────────────────────────────────────────────────
RECEIVER_VID = 0x3434
RECEIVER_PID = 0xD028
MOUSE_VID    = 0x3434
MOUSE_PID    = 0xD049

# ── Colours for terminal output ────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def hex_dump(data: bytes, highlight_indices: list[int] = []) -> str:
    parts = []
    for i, b in enumerate(data):
        s = f"{b:02X}"
        if i in highlight_indices:
            s = f"{GREEN}{BOLD}{s}{RESET}"
        parts.append(s)
    return " ".join(parts)

def looks_like_battery(data: bytes) -> list[int]:
    """Return indices of bytes whose value is 0–100 (plausible battery %)."""
    return [i for i, b in enumerate(data) if 0 < b <= 100]

def probe_device(vid: int, pid: int, label: str):
    print(f"\n{'='*64}")
    print(f"{BOLD}{CYAN}Probing: {label}  (VID={vid:04X} PID={pid:04X}){RESET}")
    print(f"{'='*64}")

    devices = hid.enumerate(vid, pid)
    if not devices:
        print(f"  {RED}No devices found. Is the receiver plugged in?{RESET}")
        return

    for dev_info in devices:
        iface = dev_info['interface_number']
        path  = dev_info['path'].decode() if isinstance(dev_info['path'], bytes) else dev_info['path']
        usage = dev_info.get('usage', 0)
        usage_page = dev_info.get('usage_page', 0)

        print(f"\n{BOLD}  Interface {iface}{RESET}  path={path}")
        print(f"  usage_page=0x{usage_page:04X}  usage=0x{usage:04X}")

        try:
            dev = HidDev(dev_info['path'])
        except Exception as e:
            print(f"  {RED}Could not open: {e}{RESET}")
            continue

        dev.set_nonblocking(1)

        # ── 1. Try GET_FEATURE for report IDs 0x01–0x1F ──────────────────────
        print(f"  {CYAN}→ Trying GET_FEATURE report IDs 0x01–0x1F ...{RESET}")
        for report_id in range(0x01, 0x20):
            try:
                # hid.get_feature_report expects [report_id] + buffer
                buf = [report_id] + [0x00] * 63
                result = dev.get_feature_report(report_id, 65)
                if result and len(result) > 1 and any(b != 0 for b in result[1:]):
                    highlights = looks_like_battery(bytes(result))
                    print(f"  {GREEN}  [FEATURE 0x{report_id:02X}] len={len(result)} "
                          f"data: {hex_dump(bytes(result), highlights)}{RESET}")
                    if highlights:
                        print(f"  {YELLOW}  ↑ Bytes at indices {highlights} look like battery % "
                              f"({[result[i] for i in highlights]}){RESET}")
            except Exception:
                pass

        # ── 2. Try common Keychron vendor commands via interrupt write ─────────
        # These are speculative based on patterns seen in other Keychron/similar devices.
        vendor_cmds = [
            # (label, bytes_to_write)
            ("Battery query (0x05 0x01)",     [0x00, 0x05, 0x01] + [0x00]*61),
            ("Battery query (0x08 0x01)",     [0x00, 0x08, 0x01] + [0x00]*61),
            ("Battery query (0x0B 0xAA)",     [0x00, 0x0B, 0xAA] + [0x00]*61),
            ("Status query  (0x01 0x01)",     [0x00, 0x01, 0x01] + [0x00]*61),
            ("Status query  (0x02 0x01)",     [0x00, 0x02, 0x01] + [0x00]*61),
            ("Generic       (0xAA 0x55)",     [0x00, 0xAA, 0x55] + [0x00]*61),
            # Report ID 4 (custom HID) with battery sub-command
            ("Rpt4 battery  (0x04 0x01 0x05)",[0x04, 0x01, 0x05] + [0x00]*61),
            ("Rpt4 battery  (0x04 0xAA 0x55)",[0x04, 0xAA, 0x55] + [0x00]*61),
        ]

        print(f"  {CYAN}→ Trying vendor interrupt commands ...{RESET}")
        for cmd_label, cmd in vendor_cmds:
            try:
                dev.write(cmd)
                time.sleep(0.08)
                response = dev.read(64, timeout_ms=100)
                if response and any(b != 0 for b in response):
                    highlights = looks_like_battery(bytes(response))
                    print(f"  {GREEN}  [{cmd_label}] response: "
                          f"{hex_dump(bytes(response), highlights)}{RESET}")
                    if highlights:
                        print(f"  {YELLOW}  ↑ Indices {highlights} look like battery % "
                              f"({[response[i] for i in highlights]}){RESET}")
            except Exception:
                pass

        # ── 3. Passive read — catch any spontaneous reports ───────────────────
        print(f"  {CYAN}→ Passive read (500ms, catches spontaneous reports) ...{RESET}")
        deadline = time.time() + 0.5
        while time.time() < deadline:
            try:
                data = dev.read(64, timeout_ms=50)
                if data and any(b != 0 for b in data):
                    highlights = looks_like_battery(bytes(data))
                    print(f"  {GREEN}  [PASSIVE] {hex_dump(bytes(data), highlights)}{RESET}")
                    if highlights:
                        print(f"  {YELLOW}  ↑ Indices {highlights} look like battery % "
                              f"({[data[i] for i in highlights]}){RESET}")
            except Exception:
                break

        dev.close()

def main():
    print(f"{BOLD}Keychron HID Battery Discovery{RESET}")
    print("Python hid version:", hid.__version__ if hasattr(hid, '__version__') else "unknown")
    print()
    print("TIP: If you see 'Permission denied', run with sudo or:")
    print("     sudo chmod a+r /dev/hidraw*")
    print()

    # Probe receiver first (wireless mode)
    probe_device(RECEIVER_VID, RECEIVER_PID, "Ultra-Link 8K Receiver")

    # Also probe direct mouse if connected
    probe_device(MOUSE_VID, MOUSE_PID, "M6 8K Mouse (wired/direct)")

    print(f"\n{'='*64}")
    print(f"{BOLD}Discovery complete.{RESET}")
    print()
    print("Next steps:")
    print("  1. Look for GREEN highlighted bytes — those are non-zero responses")
    print("  2. YELLOW highlighted bytes are in range 0–100 (likely battery %)")
    print("  3. Note the Interface number, report type, and byte index")
    print("  4. Share the output and we'll build the plasmoid backend from it")

if __name__ == "__main__":
    try:
        import hid
    except ImportError:
        print(f"{RED}Error: 'hid' module not found.{RESET}")
        print("Install with:  pip install hid  (or pip3 install hid --break-system-packages)")
        print("Also ensure hidapi is installed:")
        print("  Arch:   sudo pacman -S hidapi")
        print("  Debian: sudo apt install libhidapi-hidraw0")
        sys.exit(1)
    main()
