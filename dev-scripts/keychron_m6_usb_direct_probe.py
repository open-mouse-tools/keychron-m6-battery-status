#!/usr/bin/env python3
"""
Keychron M6 — USB-Direct Battery Probe (READ-ONLY)
===================================================
Plug the M6 in via USB cable. It enumerates as VID 0x3434 / PID 0xD049
("M6 8K"). The Launcher's reverse-engineered protocol targets this device.

Frame (64 bytes), wired mode (workMode = 0):
    Et[0]  = 0x01           # cmd category: power
    Et[2]  = 0x81           # "get"
    Et[3]  = 0x01           # sub-action
    Et[63] = 0xA1 - (sum bytes 0..62) & 0xFF        # checksum
    other bytes = 0

Expected response (64 bytes):
    cn[0]  = 0x01           # echo
    cn[3]  = 0x01
    cn[5]  = reportRateMax
    cn[10] = charge state   # 3 = no info
    cn[11] = BATTERY %      # 0..100
    cn[12] = profile index

Strategy:
  1. Find all hidraw nodes matching VID 0x3434 / PID 0xD049.
  2. Dump each interface's report descriptor (so we can identify the right one).
  3. For each interface, try the wired battery query in three send forms:
     a. No prefix    — 64-byte buffer starting [0x01, ...]
     b. Leading 0    — 65 bytes [0x00, ...64-byte-frame]
     c. Each declared OUT report ID prefix
  4. Listen on ALL of the M6's interfaces for any reply matching byte 0/3.
"""

from __future__ import annotations

import os
import select
import sys
import time
from pathlib import Path

VID = "3434"
PID_USB_DIRECT = "d049"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def find_hidraws(vid: str, pid: str) -> list[Path]:
    out: list[Path] = []
    for entry in sorted(Path("/sys/class/hidraw").iterdir()):
        uevent = entry / "device" / "uevent"
        if uevent.exists() and f"HID_ID=0003:0000{vid.upper()}:0000{pid.upper()}" in uevent.read_text():
            out.append(Path("/dev") / entry.name)
    return out


def read_descriptor(hidraw_name: str) -> bytes | None:
    path = Path("/sys/class/hidraw") / hidraw_name / "device" / "report_descriptor"
    try:
        return path.read_bytes()
    except OSError:
        return None


def parse_descriptor_report_ids(desc: bytes) -> dict[str, list[int]]:
    """Very light parser: return {'input': [ids], 'output': [ids], 'feature': [ids]} based on Main items."""
    out: dict[str, list[int]] = {"input": [], "output": [], "feature": []}
    i = 0
    current_report_id = 0  # 0 means "no report ID"
    while i < len(desc):
        b = desc[i]
        size = b & 0x03
        size = {0: 0, 1: 1, 2: 2, 3: 4}[size]
        tag = (b >> 4) & 0x0F
        type_ = (b >> 2) & 0x03
        data = desc[i + 1:i + 1 + size]
        val = int.from_bytes(data, "little") if size else 0
        # Global item: Report ID (tag 8, type 1)
        if type_ == 1 and tag == 8:
            current_report_id = val
        # Main items: Input (tag 8 type 0), Output (tag 9 type 0), Feature (tag 11 type 0)
        elif type_ == 0:
            if tag == 8:
                out["input"].append(current_report_id)
            elif tag == 9:
                out["output"].append(current_report_id)
            elif tag == 11:
                out["feature"].append(current_report_id)
        i += 1 + size
    # Dedupe while preserving order.
    for k in out:
        seen = set()
        deduped = []
        for x in out[k]:
            if x not in seen:
                seen.add(x)
                deduped.append(x)
        out[k] = deduped
    return out


def make_frame(work_mode: int = 0) -> bytes:
    et = bytearray(64)
    et[0] = 0x01 | (0x40 if work_mode else 0)
    et[2] = 0x81
    et[3] = 0x01
    et[63] = (0xA1 - (sum(et[:63]) & 0xFF)) & 0xFF
    return bytes(et)


def hex_str(data: bytes, max_bytes: int = 32) -> str:
    head = " ".join(f"{b:02X}" for b in data[:max_bytes])
    return head + (" …" if len(data) > max_bytes else "")


def drain(fds: dict[str, int], duration_s: float) -> list[tuple[str, bytes]]:
    deadline = time.monotonic() + duration_s
    out: list[tuple[str, bytes]] = []
    name_by_fd = {fd: name for name, fd in fds.items()}
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select(list(fds.values()), [], [], min(0.03, remaining))
        for fd in ready:
            try:
                data = os.read(fd, 256)
            except (BlockingIOError, OSError):
                continue
            if data:
                out.append((name_by_fd[fd], bytes(data)))
    return out


def annotated_response(data: bytes, expected_byte0: int) -> str:
    parts = []
    for i, b in enumerate(data[:32]):
        s = f"{b:02X}"
        if i == 0 and b == expected_byte0:
            s = f"{CYAN}{s}{RESET}"
        elif i == 10:
            s = f"{YELLOW}{BOLD}{s}{RESET}"
        elif i == 11:
            s = f"{GREEN}{BOLD}{s}{RESET}"
        elif i == 12:
            s = f"{CYAN}{s}{RESET}"
        parts.append(s)
    return " ".join(parts)


def try_send(write_name: str, payload: bytes, fds: dict[str, int], expected_byte0: int, label: str) -> bool:
    print(f"\n{BOLD}── {label} ──{RESET}")
    if write_name not in fds:
        print(f"  {YELLOW}{write_name} not opened — skip{RESET}")
        return False
    print(f"  send → {write_name}: {hex_str(payload, 12)}  (len={len(payload)})")
    drain(fds, 0.08)
    try:
        n = os.write(fds[write_name], payload)
        if n != len(payload):
            print(f"  {RED}short write: {n}/{len(payload)}{RESET}")
            return False
    except OSError as exc:
        print(f"  {RED}write failed: {exc}{RESET}")
        return False

    frames = drain(fds, 0.4)
    if not frames:
        print(f"  (no response)")
        return False
    success = False
    for src, data in frames:
        match = len(data) >= 12 and data[0] == expected_byte0 and data[3] == 0x01
        marker = f"  {GREEN}{BOLD}[MATCH]{RESET}" if match else ""
        print(f"  ← [{src}] len={len(data)}{marker}")
        print(f"    {annotated_response(data, expected_byte0)}")
        if match:
            success = True
            print()
            print(f"  {GREEN}{BOLD}🎯 BATTERY READ ACQUIRED — interface: {src}{RESET}")
            print(f"     byte 0  (echo):        0x{data[0]:02X}")
            print(f"     byte 3  (sub-action):  0x{data[3]:02X}")
            print(f"     byte 5  (reportRate):  0x{data[5]:02X} ({data[5]})")
            print(f"     byte 10 (charge state):0x{data[10]:02X}   ({data[10]})")
            print(f"     {GREEN}{BOLD}byte 11 (BATTERY %):   {data[11]}{RESET}")
            print(f"     byte 12 (profile):     {data[12]}")
    return success


def main() -> int:
    print(f"{BOLD}Keychron M6 — USB-Direct Battery Probe (read-only){RESET}\n")
    paths = find_hidraws(VID, PID_USB_DIRECT)
    if not paths:
        print(f"{RED}No hidraw nodes found for VID={VID} PID={PID_USB_DIRECT} (USB-direct M6).{RESET}")
        print(f"{YELLOW}Make sure the M6 is plugged in via USB cable, and switched to wired mode if it has a slider.{RESET}")
        return 1

    print(f"Found {len(paths)} hidraw nodes for M6 USB-direct:")
    for p in paths:
        desc = read_descriptor(p.name)
        if desc is None:
            print(f"  {p}  (descriptor unreadable — try with sudo or wait for udev)")
            continue
        parsed = parse_descriptor_report_ids(desc)
        print(f"  {p}  (desc {len(desc)}B; "
              f"in={parsed['input']}, out={parsed['output']}, feature={parsed['feature']})")
        print(f"    {hex_str(desc, 64)}")
    print()

    fds: dict[str, int] = {}
    for p in paths:
        try:
            fds[p.name] = os.open(str(p), os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            print(f"  {RED}cannot open {p.name}: {exc}{RESET}")
    if not fds:
        return 1

    frame64_wired = make_frame(work_mode=0)
    print(f"Wired battery query frame (64B):")
    print(f"  {hex_str(frame64_wired, 64)}")

    try:
        # For each hidraw, try several send forms.
        for name in fds:
            desc = read_descriptor(name)
            if desc is None:
                continue
            parsed = parse_descriptor_report_ids(desc)
            print(f"\n{BOLD}{'='*64}{RESET}")
            print(f"{BOLD}Testing {name}  out IDs: {parsed['output']}{RESET}")

            # Form 1: write raw 64 bytes (byte 0 = 0x01 — kernel treats as report ID)
            if try_send(name, frame64_wired, fds, 0x01, f"{name}: raw 64B [byte0=0x01]"):
                return 0
            # Form 2: prepend 0 (no report ID) + full 64B frame = 65 bytes
            if try_send(name, b"\x00" + frame64_wired, fds, 0x01, f"{name}: [0x00] + 64B frame"):
                return 0
            # Form 3: for each declared output report ID, write [ID] + first 63 bytes
            for rid in parsed["output"]:
                if rid == 0:
                    # zero ID = no prefix; covered by form 1 already
                    continue
                wire = bytes([rid]) + frame64_wired[:63]
                if try_send(name, wire, fds, 0x01, f"{name}: [0x{rid:02X}] + 63B frame"):
                    return 0
    finally:
        for fd in fds.values():
            try:
                os.close(fd)
            except OSError:
                pass

    print(f"\n{YELLOW}No interface produced a battery-shaped response.{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
