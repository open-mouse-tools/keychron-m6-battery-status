#!/usr/bin/env python3
"""
Keychron M6 — Battery Query (READ-ONLY)
========================================
Sends the exact battery query reverse-engineered from launcher.keychron.com:

  Frame (64 bytes):
    Et[0]  = 0x01 | (workMode<<6)   # 0x01 USB direct, 0x41 wireless
    Et[2]  = 0x81                    # "get" operation
    Et[3]  = 0x01                    # sub-action
    Et[63] = checksum, 0xA1 - sum(Et[0..62])  (& 0xFF)
    all other bytes = 0

  Response (64 bytes):
    cn[0]  = same as Et[0] (echo)
    cn[3]  = 0x01
    cn[5]  = reportRateMax
    cn[10] = charge state    (3 = "no info", else charge-state code)
    cn[11] = BATTERY %       (0..100)
    cn[12] = profile index

This is a READ-ONLY query (Et[2]=0x81 = "get"). No state-changing commands
are sent. Safe to run.

We attempt the query against each plausible hidraw node, prepending the
appropriate report ID for that node's output report. The first interface
to return a response with byte 3 == 0x01 wins.
"""

from __future__ import annotations

import os
import select
import sys
import time
from pathlib import Path

VID = "3434"
PID_RECEIVER = "d028"
WORK_MODE_WIRELESS = 1  # we're going through the receiver
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def find_receiver_hidraws() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for entry in sorted(Path("/sys/class/hidraw").iterdir()):
        uevent = entry / "device" / "uevent"
        if uevent.exists() and f"HID_ID=0003:0000{VID.upper()}:0000{PID_RECEIVER.upper()}" in uevent.read_text():
            out[entry.name] = Path("/dev") / entry.name
    return out


def build_frame(category: int, op: int, sub: int, work_mode: int) -> bytes:
    et = bytearray(64)
    et[0] = category | (0x40 if work_mode == 1 else 0)
    et[2] = op
    et[3] = sub
    sum_bytes = sum(et[:63]) & 0xFF
    et[63] = (0xA1 - sum_bytes) & 0xFF
    return bytes(et)


def hex_dump(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def annotated(data: bytes, expected_byte0: int) -> str:
    """Highlight the battery byte (offset 11) and charge state (offset 10)."""
    parts = []
    for i, b in enumerate(data):
        s = f"{b:02X}"
        if i == 0 and b == expected_byte0:
            s = f"{CYAN}{s}{RESET}"
        elif i == 10:
            s = f"{YELLOW}{BOLD}{s}{RESET}"  # charge state
        elif i == 11:
            s = f"{GREEN}{BOLD}{s}{RESET}"   # battery %
        elif i == 12:
            s = f"{CYAN}{s}{RESET}"          # profile
        parts.append(s)
    return " ".join(parts)


def try_query(hidraw: Path, report_id: int | None, payload: bytes, listen_paths: list[Path], expected_byte0: int) -> bool:
    """Send `payload` to `hidraw` (optionally with `report_id` prefix) and listen for a response."""
    if report_id is None:
        wire = payload
        rid_desc = "no report ID"
    else:
        # Linux hidraw: first byte of write = report ID; payload follows.
        # Many M-series mice declare a 63-byte output payload with report ID,
        # so we drop the trailing byte to fit. Try both forms.
        wire = bytes([report_id]) + payload[:63]
        rid_desc = f"report ID 0x{report_id:02X}"

    print(f"\n{BOLD}→ Writing {len(wire)}B to {hidraw.name} ({rid_desc}){RESET}")
    print(f"  send: {hex_dump(wire[:32])}{' ...' if len(wire) > 32 else ''}")

    # Open all listen targets read+write.
    fds: dict[Path, int] = {}
    write_path = hidraw
    for p in set(listen_paths) | {write_path}:
        try:
            fds[p] = os.open(str(p), os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            print(f"  {RED}cannot open {p.name}: {exc}{RESET}")
            return False

    try:
        # Drain anything pending first.
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            ready, _, _ = select.select(list(fds.values()), [], [], 0.02)
            for fd in ready:
                try:
                    os.read(fd, 256)
                except OSError:
                    pass

        try:
            n = os.write(fds[write_path], wire)
        except OSError as exc:
            print(f"  {RED}write failed: {exc}{RESET}")
            return False
        if n != len(wire):
            print(f"  {RED}short write: {n}/{len(wire)}{RESET}")
            return False

        # Listen 400ms on all opened fds for a matching response.
        deadline = time.monotonic() + 0.4
        got_match = False
        path_by_fd = {fd: p for p, fd in fds.items()}
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ready, _, _ = select.select(list(fds.values()), [], [], min(0.05, max(remaining, 0)))
            for fd in ready:
                try:
                    data = os.read(fd, 256)
                except OSError:
                    continue
                if not data:
                    continue
                src = path_by_fd[fd].name
                # The expected response has byte 0 = expected_byte0 AND byte 3 = 0x01.
                match = len(data) >= 12 and data[0] == expected_byte0 and data[3] == 0x01
                tag = f"{GREEN}[MATCH]{RESET}" if match else ""
                print(f"  ← [{src}] len={len(data)} {tag}")
                print(f"    {annotated(data[:32], expected_byte0)}")
                if match:
                    got_match = True
                    print(f"\n  {GREEN}{BOLD}🎯 Battery query response detected on {src}!{RESET}")
                    print(f"     workMode (echo):    0x{data[0]:02X}")
                    print(f"     reportRateMax:      0x{data[5]:02X} ({data[5]})")
                    print(f"     charge state:       0x{data[10]:02X} (3 = no info)")
                    print(f"     {GREEN}{BOLD}battery %:         {data[11]}%{RESET}")
                    print(f"     profile:            {data[12]}")
        return got_match
    finally:
        for fd in fds.values():
            try:
                os.close(fd)
            except OSError:
                pass


def main() -> int:
    print(f"{BOLD}Keychron M6 — Battery Query (read-only){RESET}\n")

    paths = find_receiver_hidraws()
    if not paths:
        print(f"{RED}No receiver hidraw nodes found.{RESET}")
        return 1

    print("Receiver hidraw nodes:")
    for name, path in paths.items():
        print(f"  {name} → {path}")

    listen_paths = [paths[n] for n in ("hidraw15", "hidraw16", "hidraw17") if n in paths]

    # Build both wired & wireless frames; we expect the wireless one to work.
    frame_wired   = build_frame(category=0x01, op=0x81, sub=0x01, work_mode=0)
    frame_wireless = build_frame(category=0x01, op=0x81, sub=0x01, work_mode=1)
    print(f"\n{BOLD}Wired frame  (expected byte0 in response = 0x01):{RESET}")
    print(f"  {hex_dump(frame_wired[:16])} ... {hex_dump(frame_wired[60:])}")
    print(f"{BOLD}Wireless frame (expected byte0 in response = 0x41):{RESET}")
    print(f"  {hex_dump(frame_wireless[:16])} ... {hex_dump(frame_wireless[60:])}")

    # Targets to try, in order of plausibility.
    # Each is (hidraw_name, output_report_id, frame, expected_response_byte0, note).
    attempts = [
        ("hidraw17", 0xB3, frame_wireless, 0x41, "vendor (FFC1), 63B output ID B3, wireless"),
        ("hidraw17", 0xB3, frame_wired,    0x01, "vendor (FFC1), 63B output ID B3, wired-style"),
        ("hidraw17", 0xB5, frame_wireless, 0x41, "vendor (FFC1), 20B output ID B5, wireless (will truncate)"),
        ("hidraw16", 0xB2, frame_wireless, 0x41, "battery sys (008C), 32B output ID B2, wireless (will truncate)"),
        ("hidraw15", None, frame_wireless, 0x41, "QMK (FF60), 32B no-ID, wireless (will truncate)"),
    ]

    for hidraw_name, rid, frame, exp_b0, note in attempts:
        if hidraw_name not in paths:
            continue
        print(f"\n{BOLD}{'='*64}{RESET}")
        print(f"{BOLD}Attempt: {note}{RESET}")
        success = try_query(paths[hidraw_name], rid, frame, listen_paths, exp_b0)
        if success:
            print(f"\n{GREEN}{BOLD}=== SUCCESS — protocol confirmed. ==={RESET}")
            return 0

    print(f"\n{YELLOW}No conclusive battery response. The protocol may need a different report ID, or the wireless frame may need tweaking.{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
