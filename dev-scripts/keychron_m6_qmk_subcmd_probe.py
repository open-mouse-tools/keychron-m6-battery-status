#!/usr/bin/env python3
"""
Keychron M6 — QMK Raw-HID Sub-command Sweep
============================================
We've confirmed the M6 responds on hidraw15 (QMK raw HID interface) to a write
beginning with byte 0xB2. The response shape was:

    B2 01 34 34 49 D0 01 00 ... 00

That looked like a handshake: `B2 <ack=01> <VID-LE> <PID-LE> <flag> ...`. The
actual battery byte was zero — so we need a different sub-command.

This script sweeps:
  (a) leading-byte 0xB2 with sub-commands 0x00..0x10, plus a few M7-derived
      guesses (0xE4) and common battery query bytes (0x06, 0x07, 0xA0, 0xA1).
  (b) Two-byte command form `[cmd, sub]` where sub-command varies and we also
      try leading bytes other than 0xB2 (0xB0, 0xB1, 0xB3, 0x06, 0x10).

It also runs each candidate twice with a small delay, since some devices only
populate battery on the *second* query after a primer.

Run:
    python3 keychron_m6_qmk_subcmd_probe.py
or:
    sudo python3 keychron_m6_qmk_subcmd_probe.py
"""

from __future__ import annotations

import os
import select
import sys
import time
from pathlib import Path

VID = "3434"
PID_RECEIVER = "d028"
TARGET_HIDRAW = "hidraw15"  # QMK raw HID interface — the channel that responded
PAYLOAD_LEN = 32            # QMK raw HID uses fixed 32-byte reports
EXPECTED_SIG = bytes.fromhex("343449d0")  # M6 signature in responses

GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def find_target() -> Path | None:
    for entry in sorted(Path("/sys/class/hidraw").iterdir()):
        if entry.name != TARGET_HIDRAW:
            continue
        uevent = entry / "device" / "uevent"
        if uevent.exists() and f"HID_ID=0003:0000{VID.upper()}:0000{PID_RECEIVER.upper()}" in uevent.read_text():
            return Path("/dev") / entry.name
    return None


def hex_line(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def annotate(data: bytes) -> str:
    """Highlight bytes that look like battery percentage (1..100) and the signature."""
    parts = []
    sig_idx = data.find(EXPECTED_SIG)
    sig_range = range(sig_idx, sig_idx + len(EXPECTED_SIG)) if sig_idx >= 0 else range(-1, -1)
    for i, b in enumerate(data):
        s = f"{b:02X}"
        if i in sig_range:
            s = f"{CYAN}{s}{RESET}"
        elif 1 <= b <= 100 and i > 1:  # plausible battery, exclude header bytes
            s = f"{YELLOW}{BOLD}{s}{RESET}"
        parts.append(s)
    return " ".join(parts)


def read_with_timeout(fd: int, timeout_s: float) -> bytes | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], min(0.05, deadline - time.monotonic()))
        if ready:
            try:
                data = os.read(fd, 256)
                if data:
                    return data
            except (BlockingIOError, OSError):
                return None
    return None


def send_and_read(fd: int, payload: bytes, label: str, repeat: int = 2) -> None:
    """Send `payload` (padded to PAYLOAD_LEN+1 with leading 0x00 ID), read responses."""
    # Pad / truncate to 32 bytes of actual data, prepend report ID byte 0x00.
    body = payload[:PAYLOAD_LEN] + bytes(PAYLOAD_LEN - min(len(payload), PAYLOAD_LEN))
    framed = b"\x00" + body  # leading 0x00 = "no report ID" for QMK raw HID

    print(f"\n{BOLD}--- {label} ---{RESET}")
    print(f"  send  : {hex_line(body)}")

    for attempt in range(1, repeat + 1):
        try:
            n = os.write(fd, framed)
            if n != len(framed):
                print(f"  {RED}short write: {n}/{len(framed)}{RESET}")
                return
        except OSError as exc:
            print(f"  {RED}write failed: {exc}{RESET}")
            return

        resp = read_with_timeout(fd, 0.4)
        if resp is None:
            print(f"  attempt {attempt}: (no response within 400ms)")
        else:
            tag = ""
            if EXPECTED_SIG in resp:
                tag = f"  {CYAN}[contains M6 signature]{RESET}"
            print(f"  attempt {attempt}: {annotate(resp)}{tag}")

        time.sleep(0.05)


def main() -> int:
    print(f"{BOLD}Keychron M6 — QMK Raw-HID Sub-command Sweep{RESET}")
    print(f"Legend: {CYAN}cyan{RESET} = M6 signature (34 34 49 D0), "
          f"{YELLOW}{BOLD}yellow{RESET} = byte in 1..100 (plausible battery %)\n")

    path = find_target()
    if path is None:
        print(f"{RED}{TARGET_HIDRAW} (VID={VID} PID={PID_RECEIVER}) not found.{RESET}")
        return 1
    print(f"Target: {path}")

    try:
        fd = os.open(str(path), os.O_RDWR | os.O_NONBLOCK)
    except OSError as exc:
        print(f"{RED}Cannot open {path}: {exc}{RESET}")
        return 1

    try:
        # 1) Confirm handshake still works.
        send_and_read(fd, bytes([0xB2]), "HANDSHAKE (B2)", repeat=1)

        # 2) Sweep sub-commands 0x00..0x10 in byte 1 with leader 0xB2.
        for sub in range(0x00, 0x11):
            send_and_read(fd, bytes([0xB2, sub]), f"B2 {sub:02X}", repeat=2)

        # 3) Try M7's known commands.
        for sub in (0x06, 0x07, 0xE4):
            send_and_read(fd, bytes([0xB2, sub]), f"B2 {sub:02X} (M7-derived)", repeat=2)

        # 4) Other common battery query bytes as sub-command.
        for sub in (0xA0, 0xA1, 0x80, 0xC0):
            send_and_read(fd, bytes([0xB2, sub]), f"B2 {sub:02X}", repeat=2)

        # 5) Try entirely different leading bytes — the 0xB-family.
        for cmd in (0xB0, 0xB1, 0xB3, 0xB4, 0xB5, 0xB6):
            send_and_read(fd, bytes([cmd]), f"{cmd:02X} (bare cmd)", repeat=2)
            send_and_read(fd, bytes([cmd, 0x01]), f"{cmd:02X} 01", repeat=2)

        # 6) M7-style direct battery commands (0x06, 0x51 are M7 query bytes).
        for cmd in (0x06, 0x51, 0x07, 0x10, 0xE4):
            send_and_read(fd, bytes([cmd]), f"{cmd:02X} (bare)", repeat=2)

    finally:
        os.close(fd)

    print(f"\n{BOLD}Done.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
