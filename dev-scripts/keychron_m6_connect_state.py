#!/usr/bin/env python3
"""
Keychron M6 — Receiver "Connect State" Query (READ-ONLY)
=========================================================
The Launcher JS uses this exact sequence to ask the receiver for its
paired-devices state:

  O = new Uint8Array(64)
  O[0] = 188            // 0xBC = receiver "Connect State" query
  ot(O)                 // checksum at O[63] = (0xA1 - sum(O[0..62])) & 0xFF
  U.sendReport(0, O)    // sendReport with reportId=0 (no prefix)

The launcher then parses the response with `0xbc Connect State: f[1], f[2], f[3]`
which means the response carries connect state in bytes 1–3 of the input frame.

This is purely a status read — no device state changes.

We try multiple delivery paths because WebHID's `sendReport(0, …)` doesn't
map cleanly onto Linux hidraw:
  A) Write 64 bytes starting [0xBC, …, checksum] direct to hidraw17 (kernel
     will treat byte 0 = 0xBC as the report ID — undeclared, may fail).
  B) Write 64 bytes [0xB3, then 63 bytes starting 0xBC, …] — wrap inside
     the vendor channel's declared 0xB3 output report.
  C) Write to hidraw16 as [0xB2, 32-byte truncated frame].
  D) Write to hidraw15 with no report ID (32-byte truncated).
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


def find_paths() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for entry in sorted(Path("/sys/class/hidraw").iterdir()):
        uevent = entry / "device" / "uevent"
        if uevent.exists() and f"HID_ID=0003:0000{VID.upper()}:0000{PID_RECEIVER.upper()}" in uevent.read_text():
            out[entry.name] = Path("/dev") / entry.name
    return out


def make_connect_state_frame() -> bytes:
    """64-byte frame: O[0] = 0xBC; ot() checksum at O[63]."""
    et = bytearray(64)
    et[0] = 0xBC
    sum_bytes = sum(et[:63]) & 0xFF
    et[63] = (0xA1 - sum_bytes) & 0xFF
    return bytes(et)


def hex_str(data: bytes, max_bytes: int = 32) -> str:
    head = " ".join(f"{b:02X}" for b in data[:max_bytes])
    return head + (" …" if len(data) > max_bytes else "")


def drain_all(fds: dict[str, int], duration_s: float) -> list[tuple[str, bytes]]:
    deadline = time.monotonic() + duration_s
    frames: list[tuple[str, bytes]] = []
    name_by_fd = {fd: name for name, fd in fds.items()}
    fd_list = list(fds.values())
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select(fd_list, [], [], min(0.03, remaining))
        for fd in ready:
            try:
                data = os.read(fd, 256)
            except (BlockingIOError, OSError):
                continue
            if data:
                frames.append((name_by_fd[fd], bytes(data)))
    return frames


def attempt(label: str, write_name: str, write_payload: bytes, fds: dict[str, int]) -> None:
    print(f"\n{BOLD}{'='*64}{RESET}")
    print(f"{BOLD}{label}{RESET}")
    if write_name not in fds:
        print(f"  {YELLOW}{write_name} not opened — skip{RESET}")
        return
    print(f"  send to {write_name}: {hex_str(write_payload, 16)}")
    # Drain stale frames first.
    drain_all(fds, 0.1)
    try:
        n = os.write(fds[write_name], write_payload)
        if n != len(write_payload):
            print(f"  {RED}short write: {n}/{len(write_payload)}{RESET}")
            return
    except OSError as exc:
        print(f"  {RED}write failed: {exc}{RESET}")
        return
    frames = drain_all(fds, 0.4)
    if not frames:
        print(f"  (no response)")
        return
    seen: set[bytes] = set()
    for src, data in frames:
        if data[:32] in seen:
            continue
        seen.add(data[:32])
        # Highlight bytes 1-3 (connect state) for the receiver-state response.
        parts = []
        for i, b in enumerate(data[:32]):
            s = f"{b:02X}"
            if i in (1, 2, 3) and data[0] == 0xBC:
                s = f"{GREEN}{BOLD}{s}{RESET}"
            elif b == 0x64:
                s = f"{CYAN}{BOLD}{s}{RESET}"
            parts.append(s)
        marker = ""
        if data[0] == 0xBC:
            marker = f"  {GREEN}{BOLD}🎯 0xBC connect-state response!{RESET}"
        print(f"  ← [{src}] len={len(data)}{marker}")
        print(f"    {' '.join(parts)}")


def main() -> int:
    print(f"{BOLD}Keychron M6 — Receiver Connect-State Query (read-only){RESET}\n")
    paths = find_paths()
    if not paths:
        print(f"{RED}No receiver hidraw nodes found.{RESET}")
        return 1

    fds: dict[str, int] = {}
    for name in ("hidraw15", "hidraw16", "hidraw17"):
        if name in paths:
            try:
                fds[name] = os.open(str(paths[name]), os.O_RDWR | os.O_NONBLOCK)
            except OSError as exc:
                print(f"{RED}cannot open {name}: {exc}{RESET}")
    if not fds:
        return 1
    print(f"Listening + writing on: {', '.join(fds.keys())}")

    frame64 = make_connect_state_frame()
    print(f"\nFrame (64B): {hex_str(frame64, 64)}")
    print(f"Checksum at byte 63: 0x{frame64[63]:02X} (sum={sum(frame64[:63]) & 0xFF:02X}, target=0xA1)")

    try:
        # A) Direct 64B write to hidraw17 — kernel treats byte 0 (0xBC) as report ID
        attempt(
            "A) hidraw17 ← 64B starting [0xBC, …] (kernel uses byte 0 as report ID)",
            "hidraw17", frame64, fds,
        )
        # B) Wrapped: B3 + 63 bytes (first 63 of the frame, dropping checksum)
        wrapped = bytes([0xB3]) + frame64[:63]
        attempt(
            "B) hidraw17 ← [0xB3, 0xBC, …] (wrap inside vendor channel)",
            "hidraw17", wrapped, fds,
        )
        # C) hidraw16 with B2 prefix + 32B from start of frame
        h16 = bytes([0xB2]) + frame64[:32]
        attempt(
            "C) hidraw16 ← [0xB2, first 32B of frame]",
            "hidraw16", h16, fds,
        )
        # D) hidraw15 32-byte truncated, no report ID (kernel treats first byte as ID — but FF60 has no ID, so byte 0=0xBC may go straight on wire)
        h15 = frame64[:32]
        attempt(
            "D) hidraw15 ← first 32B of frame (no report ID — QMK)",
            "hidraw15", h15, fds,
        )
        # E) hidraw17, 64B starting [0x00, 0xBC, …] — treat byte 0 as no-ID prefix; payload is what JS sends
        e_payload = bytes([0x00]) + frame64[:63]
        attempt(
            "E) hidraw17 ← [0x00, 0xBC, …] (leading 0 = no report ID)",
            "hidraw17", e_payload, fds,
        )
    finally:
        for fd in fds.values():
            try:
                os.close(fd)
            except OSError:
                pass

    print(f"\n{BOLD}Done.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
