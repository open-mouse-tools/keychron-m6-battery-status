#!/usr/bin/env python3
"""
Keychron M6 — FEATURE Report Read (READ-ONLY)
==============================================
hidraw6 (when M6 is wired) declares FEATURE reports on IDs 0x14, 0x2A, 0x2B.
The Launcher's "FeatureTransceiver" class uses sendFeatureReport / receiveFeatureReport,
so the M6 protocol may go through this channel rather than OUTPUT reports.

This script issues HIDIOCGFEATURE on each declared feature report ID and dumps
the response. Purely a read — no state changes.

We also try issuing HIDIOCSFEATURE with the wired battery query frame
([0x01, 0x00, 0x81, 0x01, ...checksum]) prefixed by the report ID, then
HIDIOCGFEATURE to fetch the answer. This mirrors the M7-style prime/query
dance used by hi-drawbridge.
"""

from __future__ import annotations

import ctypes
import fcntl
import os
import struct
import sys
from pathlib import Path

VID = "3434"
PID_USB_DIRECT = "d049"
TARGET_NAMES = ("hidraw6",)  # the vendor-FF0B channel with feature reports

GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


# Linux _IOC encoding
def _IOC(direction: int, type_: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (type_ << 8) | nr

_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2
HIDRAW_TYPE = ord('H')

def HIDIOCSFEATURE(size: int) -> int:
    return _IOC(_IOC_READ | _IOC_WRITE, HIDRAW_TYPE, 0x06, size)

def HIDIOCGFEATURE(size: int) -> int:
    return _IOC(_IOC_READ | _IOC_WRITE, HIDRAW_TYPE, 0x07, size)


def find_hidraws(vid: str, pid: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for entry in sorted(Path("/sys/class/hidraw").iterdir()):
        uevent = entry / "device" / "uevent"
        if uevent.exists() and f"HID_ID=0003:0000{vid.upper()}:0000{pid.upper()}" in uevent.read_text():
            out[entry.name] = Path("/dev") / entry.name
    return out


def hex_str(data: bytes, max_bytes: int = 64) -> str:
    head = " ".join(f"{b:02X}" for b in data[:max_bytes])
    return head + (" …" if len(data) > max_bytes else "")


def annotated(data: bytes) -> str:
    parts = []
    for i, b in enumerate(data):
        s = f"{b:02X}"
        if 1 <= b <= 100 and i > 3:
            s = f"{YELLOW}{BOLD}{s}{RESET}"
        if b == 0x64 and i > 3:
            s = f"{GREEN}{BOLD}{s}{RESET}"
        parts.append(s)
    return " ".join(parts)


def get_feature(fd: int, report_id: int, length: int) -> bytes | None:
    """Issue HIDIOCGFEATURE for `report_id`. Buffer length is `length` bytes total (incl. ID)."""
    buf = bytearray(length)
    buf[0] = report_id
    try:
        n = fcntl.ioctl(fd, HIDIOCGFEATURE(length), buf, True)
    except OSError as exc:
        print(f"  {RED}HIDIOCGFEATURE(0x{report_id:02X}, len={length}) failed: {exc}{RESET}")
        return None
    return bytes(buf[:n if isinstance(n, int) and n > 0 else length])


def set_feature(fd: int, report_id: int, payload: bytes) -> bool:
    """Issue HIDIOCSFEATURE with [report_id] + payload. Returns True on success."""
    buf = bytearray([report_id]) + bytearray(payload)
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), bytes(buf))
        return True
    except OSError as exc:
        print(f"  {RED}HIDIOCSFEATURE(0x{report_id:02X}, len={len(buf)}) failed: {exc}{RESET}")
        return False


def make_battery_payload(work_mode: int = 0) -> bytes:
    """Build the 64-byte battery query frame (matches the Launcher's getPower)."""
    et = bytearray(64)
    et[0] = 0x01 | (0x40 if work_mode else 0)
    et[2] = 0x81
    et[3] = 0x01
    et[63] = (0xA1 - (sum(et[:63]) & 0xFF)) & 0xFF
    return bytes(et)


def main() -> int:
    print(f"{BOLD}Keychron M6 — Feature Report Read (read-only){RESET}\n")
    paths = find_hidraws(VID, PID_USB_DIRECT)
    if not paths:
        print(f"{RED}No M6 USB-direct nodes found (VID={VID} PID={PID_USB_DIRECT}).{RESET}")
        print(f"{YELLOW}Plug in the M6 via USB cable (in wired mode).{RESET}")
        return 1

    target = None
    for name in TARGET_NAMES:
        if name in paths:
            target = paths[name]
            break
    if target is None:
        # Fall back: try every node, the parser might have given different names
        # Pick whichever node has feature reports declared.
        for name, p in paths.items():
            desc_path = Path("/sys/class/hidraw") / name / "device" / "report_descriptor"
            if desc_path.exists():
                desc = desc_path.read_bytes()
                # Quick check: does any "feature" main item (tag 0xB) exist?
                if b"\xb1\x02" in desc or b"\xb2\x02" in desc or b"\xb2\x02\x01" in desc:
                    target = p
                    print(f"{YELLOW}Note: using {name} (auto-detected feature-report-bearing node).{RESET}")
                    break

    if target is None:
        print(f"{RED}No hidraw node with feature reports found on M6 USB-direct.{RESET}")
        return 1

    print(f"Target: {target}")

    try:
        fd = os.open(str(target), os.O_RDWR)
    except OSError as exc:
        print(f"{RED}cannot open {target}: {exc}{RESET}")
        return 1

    try:
        # The descriptor declared feature reports on IDs 0x2A (42), 0x2B (43), and (per our parser) 0x14 (20).
        # Each report is 60 bytes payload, so total length with report ID = 61.
        feature_ids_to_try = [0x2A, 0x2B, 0x14, 0x2C, 0x2D]
        lengths_to_try = [61, 64, 21]  # 60+1 (declared), 63+1 (vendor-channel-like), 20+1 (small)

        # Part 1: Plain GET on each feature report (read whatever the current value is).
        print(f"\n{BOLD}=== Part 1: HIDIOCGFEATURE on each declared ID ==={RESET}")
        for rid in feature_ids_to_try:
            for length in lengths_to_try:
                print(f"\n{BOLD}── GET feature 0x{rid:02X}, length {length} ──{RESET}")
                resp = get_feature(fd, rid, length)
                if resp is not None:
                    print(f"  ← len={len(resp)}")
                    print(f"    {annotated(resp)}")

        # Part 2: SET the battery-query frame as a feature, then GET to retrieve the answer.
        print(f"\n\n{BOLD}=== Part 2: SET battery query, then GET response ==={RESET}")
        frame = make_battery_payload(work_mode=0)
        # The frame is 64 bytes; for a 60-byte feature, we'd drop bytes 60..63
        for rid in (0x2A, 0x2B):
            print(f"\n{BOLD}── SET feature 0x{rid:02X}, full 64B frame (kernel truncates if needed) ──{RESET}")
            print(f"  payload: {hex_str(frame, 16)}")
            ok = set_feature(fd, rid, frame)
            if not ok:
                # Try with the 60-byte truncation
                print(f"  {YELLOW}retry with 60-byte truncated payload (matches declared size){RESET}")
                ok = set_feature(fd, rid, frame[:60])
            if not ok:
                continue
            print(f"  ✓ SET succeeded — now reading response …")
            resp = get_feature(fd, rid, 64)
            if resp is not None:
                print(f"  ← len={len(resp)}")
                print(f"    {annotated(resp)}")
                # Check for battery shape
                if len(resp) >= 12 and resp[0] == 0x01 and resp[3] == 0x01:
                    print(f"\n  {GREEN}{BOLD}🎯 BATTERY READ — interface: {target.name}, report ID 0x{rid:02X}{RESET}")
                    print(f"     byte 11 (BATTERY %): {resp[11]}")
                    print(f"     byte 10 (charge state): {resp[10]}")
                    print(f"     byte  5 (reportRate):   {resp[5]}")
                    print(f"     byte 12 (profile):      {resp[12]}")

    finally:
        os.close(fd)

    return 0


if __name__ == "__main__":
    sys.exit(main())
