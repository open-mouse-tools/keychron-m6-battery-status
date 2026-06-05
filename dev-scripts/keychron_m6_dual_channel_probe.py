#!/usr/bin/env python3
"""
Keychron M6 — Dual-Channel Probe
=================================
Probes the M6 receiver assuming a two-channel vendor protocol:

  • hidraw15 (QMK raw HID, usage_page 0xFF60) — single-byte property reads
    of the dongle (0xB2 = identify, 0xB3 = firmware, etc.). Confirmed by
    earlier probe runs.
  • hidraw17 (vendor, usage_page 0xFFC1) — declares OUTPUT report IDs 0xB3
    (63B) and 0xB5 (20B), and INPUT report IDs 0xB4 (63B) and 0xB6 (20B).
    Likely the mouse-data channel; not yet exercised correctly.

This script opens every receiver-side hidraw read+write at once, then:

  Part A: sweep the full 0xB0–0xBF family on hidraw15, listening on ALL fds.
  Part B: write valid 64-byte output reports with leading ID 0xB3 to hidraw17,
          listening for input report 0xB4 in response.
  Part C: write valid 21-byte output reports with leading ID 0xB5 to hidraw17,
          listening for input report 0xB6 in response.

Each frame is decoded with column markers so we can pin down a battery
percentage byte if/when it appears.
"""

from __future__ import annotations

import os
import select
import sys
import time
from pathlib import Path

VID = "3434"
PID_RECEIVER = "d028"
EXPECTED_SIG = bytes.fromhex("343449d0")  # 34 34 49 D0 — M6 USB VID+PID LE

GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def find_receiver_hidraws() -> list[Path]:
    nodes: list[Path] = []
    for entry in sorted(Path("/sys/class/hidraw").iterdir()):
        uevent = entry / "device" / "uevent"
        if uevent.exists() and f"HID_ID=0003:0000{VID.upper()}:0000{PID_RECEIVER.upper()}" in uevent.read_text():
            nodes.append(Path("/dev") / entry.name)
    return nodes


def hex_line(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def annotate(data: bytes) -> str:
    parts = []
    sig_idx = data.find(EXPECTED_SIG)
    sig_range = range(sig_idx, sig_idx + len(EXPECTED_SIG)) if sig_idx >= 0 else range(-1, -1)
    for i, b in enumerate(data):
        s = f"{b:02X}"
        if i in sig_range:
            s = f"{CYAN}{s}{RESET}"
        elif 1 <= b <= 100 and i > 1:
            s = f"{YELLOW}{BOLD}{s}{RESET}"
        parts.append(s)
    return " ".join(parts)


def ruler(width: int) -> str:
    """Print an index ruler so we can pin down byte offsets at a glance."""
    return "  " + " ".join(f"{i:02d}" for i in range(width))


def drain_all(fds: dict[Path, int], duration_s: float, label: str) -> int:
    """Read input reports from all fds for `duration_s` seconds. Return frame count."""
    deadline = time.monotonic() + duration_s
    read_fds = list(fds.values())
    path_by_fd = {fd: path for path, fd in fds.items()}
    frame_count = 0
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select(read_fds, [], [], min(remaining, 0.05))
        for fd in ready:
            try:
                data = os.read(fd, 256)
            except (BlockingIOError, OSError):
                continue
            if not data:
                continue
            frame_count += 1
            src = path_by_fd[fd].name
            print(f"  {GREEN}[{src}] len={len(data)}{RESET}")
            print(f"  {ruler(min(len(data), 32))}")
            # Show up to 32 bytes annotated
            preview = data[:32]
            print(f"  {annotate(preview)}")
            if len(data) > 32:
                print(f"  ...({len(data) - 32} more bytes)")
    return frame_count


def write_to(fds: dict[Path, int], hidraw_name: str, payload: bytes, label: str) -> bool:
    target = next((p for p in fds if p.name == hidraw_name), None)
    if target is None:
        print(f"  {YELLOW}{hidraw_name} not opened, skipping [{label}]{RESET}")
        return False
    try:
        n = os.write(fds[target], payload)
        if n != len(payload):
            print(f"  {RED}short write: {n}/{len(payload)} on {hidraw_name} [{label}]{RESET}")
            return False
        return True
    except OSError as exc:
        print(f"  {RED}write failed on {hidraw_name} [{label}]: {exc}{RESET}")
        return False


def main() -> int:
    print(f"{BOLD}Keychron M6 — Dual-Channel Probe{RESET}")
    print(f"Legend: {CYAN}sig{RESET}=M6 signature, {YELLOW}{BOLD}plaus%{RESET}=byte 1..100 outside header\n")

    paths = find_receiver_hidraws()
    if not paths:
        print(f"{RED}No receiver hidraw nodes found.{RESET}")
        return 1

    fds: dict[Path, int] = {}
    for path in paths:
        try:
            fds[path] = os.open(str(path), os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            print(f"  {RED}Could not open {path}: {exc}{RESET}")

    if not fds:
        return 1

    print(f"Opened: {', '.join(p.name for p in fds)}\n")

    try:
        # ── Part A: full 0xB0–0xBF sweep on hidraw15, listen on all ──────────
        print(f"{BOLD}=== Part A: 0xB0–0xBF on hidraw15, listening on ALL ==={RESET}\n")
        for cmd in range(0xB0, 0xC0):
            payload = bytes([0x00, cmd] + [0x00] * 30)  # 32 bytes; leading 0x00 = no report ID
            label = f"hidraw15 ← 0x{cmd:02X}"
            print(f"{BOLD}--- {label} ---{RESET}")
            if write_to(fds, "hidraw15", payload, label):
                frames = drain_all(fds, 0.4, label)
                if frames == 0:
                    print(f"  (no response)")
            print()

        # ── Part B: write 64-byte output reports to hidraw17 (report ID 0xB3) ─
        print(f"{BOLD}=== Part B: 64B writes with report ID 0xB3 to hidraw17 ==={RESET}\n")
        # Try a few sub-commands in byte 1 of the payload.
        for sub in (0x00, 0x01, 0x02, 0x06, 0x07, 0x10, 0x11, 0x20, 0xB2, 0xE4):
            # 1 byte report ID + 63 bytes payload = 64 bytes
            payload = bytes([0xB3, sub] + [0x00] * 62)
            label = f"hidraw17 ← 0xB3 sub=0x{sub:02X}"
            print(f"{BOLD}--- {label} ---{RESET}")
            if write_to(fds, "hidraw17", payload, label):
                frames = drain_all(fds, 0.5, label)
                if frames == 0:
                    print(f"  (no response)")
            print()

        # ── Part C: write 21-byte output reports to hidraw17 (report ID 0xB5) ─
        print(f"{BOLD}=== Part C: 21B writes with report ID 0xB5 to hidraw17 ==={RESET}\n")
        for sub in (0x00, 0x01, 0x02, 0x06, 0x07, 0x10, 0x11, 0x20, 0xB2, 0xE4):
            # 1 byte report ID + 20 bytes payload = 21 bytes
            payload = bytes([0xB5, sub] + [0x00] * 19)
            label = f"hidraw17 ← 0xB5 sub=0x{sub:02X}"
            print(f"{BOLD}--- {label} ---{RESET}")
            if write_to(fds, "hidraw17", payload, label):
                frames = drain_all(fds, 0.5, label)
                if frames == 0:
                    print(f"  (no response)")
            print()

    finally:
        for fd in fds.values():
            try:
                os.close(fd)
            except OSError:
                pass

    print(f"{BOLD}Done.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
