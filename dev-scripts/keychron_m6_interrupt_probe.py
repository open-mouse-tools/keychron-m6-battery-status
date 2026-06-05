#!/usr/bin/env python3
"""
Keychron M6 — Interrupt-based Battery Probe
============================================
Hypothesis: M6 doesn't expose feature report 0x51 like the M7. Instead it
uses input reports 0xB1 (on the "battery system page" interface, hidraw16)
and 0xB4/0xB6 (on the vendor interface, hidraw17), driven by writing the
wake/query command 0xB2 from the M7 protocol.

This script:
  1. Captures each receiver hidraw's report descriptor (for the record).
  2. Opens every receiver-side hidraw read+write.
  3. For each candidate wake target (hidraw16, hidraw17, hidraw15), sends a
     few candidate wake/query payloads and listens for input frames on ALL
     opened nodes for ~600ms after.
  4. Dumps every frame received with source path, timestamp, and bytes.

Run with:
    sudo python3 keychron_m6_interrupt_probe.py
or, once the udev rule is applied and the receiver replugged:
    python3 keychron_m6_interrupt_probe.py
"""

from __future__ import annotations

import os
import select
import sys
import time
from pathlib import Path

VID = "3434"
PID_RECEIVER = "d028"

GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def find_receiver_hidraws() -> list[Path]:
    """Return /dev/hidrawN paths whose sysfs entry matches the receiver VID/PID."""
    nodes: list[Path] = []
    for entry in sorted(Path("/sys/class/hidraw").iterdir()):
        uevent = entry / "device" / "uevent"
        if not uevent.exists():
            continue
        text = uevent.read_text()
        if f"HID_ID=0003:0000{VID.upper()}:0000{PID_RECEIVER.upper()}" in text:
            nodes.append(Path("/dev") / entry.name)
    return nodes


def dump_report_descriptor(hidraw_name: str) -> bytes | None:
    """Read the HID report descriptor for diagnostic logging."""
    desc_path = Path("/sys/class/hidraw") / hidraw_name / "device" / "report_descriptor"
    if not desc_path.exists():
        return None
    try:
        return desc_path.read_bytes()
    except PermissionError:
        return None


def hex_dump(data: bytes, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        lines.append(f"  {i:04X}  {hex_part}")
    return "\n".join(lines)


def open_all(paths: list[Path]) -> dict[Path, int]:
    fds: dict[Path, int] = {}
    for path in paths:
        try:
            fd = os.open(str(path), os.O_RDWR | os.O_NONBLOCK)
            fds[path] = fd
        except OSError as exc:
            print(f"  {RED}Could not open {path}: {exc}{RESET}")
    return fds


def drain(fds: dict[Path, int], duration_s: float, label: str = "") -> None:
    """Read any pending input reports from all fds for `duration_s` seconds."""
    deadline = time.monotonic() + duration_s
    read_fds = list(fds.values())
    path_by_fd = {fd: path for path, fd in fds.items()}
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select(read_fds, [], [], min(remaining, 0.05))
        for fd in ready:
            try:
                data = os.read(fd, 256)
            except BlockingIOError:
                continue
            except OSError as exc:
                print(f"  {RED}Read error on {path_by_fd[fd]}: {exc}{RESET}")
                continue
            if not data:
                continue
            ts = time.strftime("%H:%M:%S")
            tag = f"{label} " if label else ""
            print(f"  {GREEN}{tag}[{path_by_fd[fd].name}] @ {ts}  len={len(data)}{RESET}")
            print(f"{hex_dump(data)}")


def try_write(fd: int, path: Path, payload: bytes, label: str) -> bool:
    try:
        n = os.write(fd, payload)
        print(f"  {CYAN}→ wrote {n}/{len(payload)} bytes to {path.name}  [{label}]{RESET}")
        return n == len(payload)
    except OSError as exc:
        print(f"  {RED}✗ write failed to {path.name} [{label}]: {exc}{RESET}")
        return False


def main() -> int:
    print(f"{BOLD}Keychron M6 — Interrupt Battery Probe{RESET}\n")

    paths = find_receiver_hidraws()
    if not paths:
        print(f"{RED}No receiver hidraw nodes found for VID={VID} PID={PID_RECEIVER}.{RESET}")
        return 1

    print(f"Found {len(paths)} receiver hidraw nodes:")
    for path in paths:
        print(f"  {path}")
    print()

    # Dump report descriptors for diagnostic reference.
    print(f"{BOLD}Report descriptors:{RESET}")
    for path in paths:
        desc = dump_report_descriptor(path.name)
        if desc is None:
            print(f"  {path.name}: (unreadable)")
            continue
        print(f"  {CYAN}{path.name}{RESET} ({len(desc)} bytes):")
        print(hex_dump(desc))
    print()

    fds = open_all(paths)
    if not fds:
        print(f"{RED}Could not open any hidraw nodes (try sudo).{RESET}")
        return 1

    try:
        # Drain any spontaneous reports first so we have a clean baseline.
        print(f"{BOLD}Baseline drain (500ms, before any writes):{RESET}")
        drain(fds, 0.5, label="BASELINE")
        print()

        # Candidate wake/query payloads.
        # The leading byte is the HID output report ID. Most receivers accept
        # report ID 0 (no ID). Two payload shapes:
        #   - M7-style:        [0x00, 0xB2, 0x00...]  (33 bytes, matches wake_report_hex)
        #   - Variant w/ID B2: [0xB2, 0x00...]        (32 bytes, B2 as report ID)
        wake_payload_m7 = bytes([0x00, 0xB2] + [0x00] * 31)            # 33 bytes
        wake_payload_b2id = bytes([0xB2] + [0x00] * 31)                # 32 bytes
        query_b1 = bytes([0x00, 0xB1] + [0x00] * 31)                   # "request 0xB1"
        query_b4 = bytes([0x00, 0xB4] + [0x00] * 31)                   # vendor read
        query_b6 = bytes([0x00, 0xB6] + [0x00] * 31)                   # vendor read

        # Send each payload to each plausible target; drain after each.
        candidates = [
            ("hidraw16", wake_payload_m7,   "WAKE(M7 shape, 33B)"),
            ("hidraw16", wake_payload_b2id, "WAKE(B2-as-id, 32B)"),
            ("hidraw16", query_b1,          "QUERY 0xB1 to iface3"),
            ("hidraw17", wake_payload_m7,   "WAKE(M7 shape) to iface4"),
            ("hidraw17", query_b4,          "QUERY 0xB4 to iface4"),
            ("hidraw17", query_b6,          "QUERY 0xB6 to iface4"),
            ("hidraw15", wake_payload_m7,   "WAKE(M7 shape) to iface2 (QMK)"),
        ]

        for hidraw_name, payload, label in candidates:
            target_path = next((p for p in paths if p.name == hidraw_name), None)
            if target_path is None or target_path not in fds:
                print(f"  {YELLOW}Skip {hidraw_name}: not opened{RESET}")
                continue
            print(f"{BOLD}--- {label} ---{RESET}")
            try_write(fds[target_path], target_path, payload, label)
            drain(fds, 0.6, label=label)
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
