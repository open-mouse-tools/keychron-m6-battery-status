#!/usr/bin/env python3
"""
Keychron M6 — Vendor-channel property sweep
============================================
The vendor channel (hidraw17) accepts writes with report ID 0xB3 (64-byte
output) and responds with input reports 0xB4 (64-byte) or 0xB6 (21-byte).

The second byte of the write is the **property selector**. Earlier sweep
identified:
    sub=0x02 → receiver identity     (B6 21B response)
    sub=0x06 → mouse DPI table       (B4 64B response)
    most others → B6 E4 07 ... (error / unknown)

This script sweeps sub-commands 0x00..0xFF, records every unique response,
and prints a sorted summary so we can pick out the battery property.

Avoids opening hidraw13/14 so mouse-pointer / keyboard chatter doesn't
flood the output.
"""

from __future__ import annotations

import os
import select
import sys
import time
from pathlib import Path

VID = "3434"
PID_RECEIVER = "d028"
LISTEN_NAMES = ("hidraw15", "hidraw17")  # vendor + QMK only
WRITE_NAME = "hidraw17"
WRITE_REPORT_ID = 0xB3
PAYLOAD_TOTAL_LEN = 64  # 1 byte report ID + 63 bytes payload
ERROR_PREFIX = bytes([0xB6, 0xE4, 0x07])  # "unknown command" shape

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


def hex_str(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def annotate(data: bytes) -> str:
    parts = []
    for i, b in enumerate(data):
        s = f"{b:02X}"
        if 1 <= b <= 100 and i > 2:
            s = f"{YELLOW}{BOLD}{s}{RESET}"
        elif b == 0x64 and i > 0:
            s = f"{CYAN}{BOLD}{s}{RESET}"
        parts.append(s)
    return " ".join(parts)


def drain(fds: list[int], duration_s: float) -> list[bytes]:
    """Collect all frames received within `duration_s`."""
    deadline = time.monotonic() + duration_s
    frames: list[bytes] = []
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select(fds, [], [], min(0.03, remaining))
        for fd in ready:
            try:
                data = os.read(fd, 256)
            except (BlockingIOError, OSError):
                continue
            if data:
                frames.append(bytes(data))
    return frames


def main() -> int:
    print(f"{BOLD}Keychron M6 — Vendor Property Sweep (B3 sub=0x00..0xFF){RESET}")
    print(f"Legend: {YELLOW}{BOLD}plaus%{RESET}=byte 1..100 after header, "
          f"{CYAN}{BOLD}0x64{RESET}=byte equal to 100\n")

    receiver_paths = find_receiver_hidraws()
    by_name = {p.name: p for p in receiver_paths}
    listen_paths = [by_name[n] for n in LISTEN_NAMES if n in by_name]
    write_path = by_name.get(WRITE_NAME)
    if write_path is None or not listen_paths:
        print(f"{RED}Required hidraw nodes not found.{RESET}")
        return 1

    fds: dict[Path, int] = {}
    for path in listen_paths:
        try:
            fds[path] = os.open(str(path), os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            print(f"{RED}Cannot open {path}: {exc}{RESET}")
            return 1

    write_fd = fds[write_path]
    listen_fds = list(fds.values())

    print(f"Listening on: {', '.join(p.name for p in fds)}")
    print(f"Writing to:   {write_path.name} (report ID 0x{WRITE_REPORT_ID:02X})\n")

    # Map sub-command → list of frames received in response.
    by_sub: dict[int, list[bytes]] = {}

    try:
        # Drain anything pending.
        drain(listen_fds, 0.2)

        for sub in range(0x00, 0x100):
            payload = bytes([WRITE_REPORT_ID, sub] + [0x00] * (PAYLOAD_TOTAL_LEN - 2))
            try:
                n = os.write(write_fd, payload)
                if n != PAYLOAD_TOTAL_LEN:
                    print(f"  {RED}sub=0x{sub:02X}: short write{RESET}")
                    continue
            except OSError as exc:
                print(f"  {RED}sub=0x{sub:02X}: write failed: {exc}{RESET}")
                continue

            frames = drain(listen_fds, 0.2)
            by_sub[sub] = frames

        # Summarize: group sub-commands by unique response shape.
        print(f"\n{BOLD}=== Unique responses ==={RESET}\n")

        # Bucket sub-commands by their *first* non-error response, OR by "error-only" if all responses were the error shape.
        buckets: dict[bytes, list[int]] = {}
        for sub, frames in by_sub.items():
            non_error = [f for f in frames if not f.startswith(ERROR_PREFIX)]
            if non_error:
                # Use the first non-error frame as the bucket key (truncated to 32B).
                key = non_error[0][:32]
            elif frames:
                key = ERROR_PREFIX  # treat all-error as a single bucket
            else:
                key = b""  # no response at all
            buckets.setdefault(key, []).append(sub)

        # Print buckets, error/no-response last.
        def bucket_priority(item: tuple[bytes, list[int]]) -> tuple[int, int]:
            key, subs = item
            if key == b"":
                return (2, subs[0])
            if key.startswith(ERROR_PREFIX):
                return (1, subs[0])
            return (0, subs[0])

        for key, subs in sorted(buckets.items(), key=bucket_priority):
            sub_str = ", ".join(f"0x{s:02X}" for s in subs[:20]) + (f", ... ({len(subs)} total)" if len(subs) > 20 else "")
            if key == b"":
                print(f"{YELLOW}NO RESPONSE{RESET} → subs: {sub_str}")
            elif key.startswith(ERROR_PREFIX):
                print(f"{RED}ERROR (B6 E4 07 ...){RESET} → subs: {sub_str}")
            else:
                print(f"{GREEN}RESPONSE{RESET} → subs: {sub_str}")
                print(f"  {annotate(key)}")
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
