#!/usr/bin/env python3
"""Keychron M6 8K — battery reader.

Reads the M6 mouse's battery percentage over one of two transports, plus a
long-running listener daemon for the wireless path.

PROTOCOL (reverse-engineered from launcher.keychron.com and verified with usbmon)

  WIRED (USB-direct, VID 0x3434 / PID 0xD049)
    Interface : usage page 0xFF0B, usage 0x0104 (vendor)
    Operation : HIDIOCGFEATURE on report 0x2A, length 61 bytes
    Decode    : battery_percent = response[9]    (0–100)
    On-demand : yes — call whenever the cable is plugged in.

  WIRELESS (through receiver, VID 0x3434 / PID 0xD028)
    Interface : usage page 0xFFC1, usage 0x01 (vendor)
    Operation : passively read input reports; filter `B6 E2 ?? 01 ??`
    Decode    : battery_percent = report[5]      (0–100)
    On-demand : NO. The receiver only pushes status frames on state changes
                (mouse sleep/wake, battery % change). A long-running listener
                must capture frames opportunistically and persist them.

USAGE

  keychron_m6_battery.py             # single read; auto-pick transport
  keychron_m6_battery.py --json      # machine-readable JSON output
  keychron_m6_battery.py --watch     # poll every --interval seconds, print on change
  keychron_m6_battery.py --listen    # daemon: capture wireless frames into the cache
  keychron_m6_battery.py --raw       # include raw bytes in output
  keychron_m6_battery.py --version
  keychron_m6_battery.py --wired     # force wired-only
  keychron_m6_battery.py --wireless  # force wireless (cache) only

CACHE FILE (wireless mode)
  Path: $XDG_RUNTIME_DIR/keychron_m6_state.json (falls back to /tmp)
  Schema: {"battery_percent": int(0..100), "raw": hex_str, "captured_at": float}

EXIT CODES
  0  success
  1  device not present (no M6 wired or receiver)
  2  device present but the read failed (permission / unexpected response)
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import select
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

__version__ = "0.1.0"


# ── Device constants ─────────────────────────────────────────────────────────

VID = "3434"
PID_WIRED = "d049"
PID_RECEIVER = "d028"

# Wired path
WIRED_USAGE_PAGE_BYTES = b"\x06\x0B\xFF"     # Usage Page 0xFF0B in raw descriptor bytes
FEATURE_REPORT_ID = 0x2A
FEATURE_REPORT_LEN = 61
WIRED_BATTERY_OFFSET = 9

# Wireless path
WIRELESS_USAGE_PAGE_BYTES = b"\x06\xC1\xFF"  # Usage Page 0xFFC1
WIRELESS_REPORT_ID = 0xB6
WIRELESS_EVENT_CLASS = 0xE2
WIRELESS_VALID_FLAG = 0x01
WIRELESS_BATTERY_OFFSET = 5

CACHE_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "keychron_m6_state.json"
CACHE_STALE_SECONDS = 6 * 60 * 60   # 6 hours

DEVICE_NAME = "Keychron M6 8K"


# ── ioctl plumbing for HIDIOCGFEATURE ────────────────────────────────────────

def _ioc(direction: int, type_: int, nr: int, size: int) -> int:
    """Compute a Linux _IOC ioctl request number."""
    return (direction << 30) | (size << 16) | (type_ << 8) | nr


# Direction = READ | WRITE; type = 'H' (hidraw); nr = 0x07 (HIDIOCGFEATURE).
_HIDIOCGFEATURE = _ioc(3, ord("H"), 0x07, FEATURE_REPORT_LEN)


# ── Discovery ────────────────────────────────────────────────────────────────

def _find_hidraw(vid: str, pid: str, usage_page_marker: bytes) -> Path | None:
    """Locate the /dev/hidrawN whose VID/PID matches and whose report descriptor
    contains `usage_page_marker` (the raw bytes of the Usage Page main item).
    Returns None if no match.
    """
    sysfs = Path("/sys/class/hidraw")
    if not sysfs.exists():
        return None
    hid_id_token = f"HID_ID=0003:0000{vid.upper()}:0000{pid.upper()}"
    for entry in sorted(sysfs.iterdir()):
        uevent_path = entry / "device" / "uevent"
        if not uevent_path.exists():
            continue
        try:
            if hid_id_token not in uevent_path.read_text():
                continue
        except OSError:
            continue
        desc_path = entry / "device" / "report_descriptor"
        try:
            if desc_path.exists() and usage_page_marker in desc_path.read_bytes():
                return Path("/dev") / entry.name
        except OSError:
            continue
    return None


def find_wired_hidraw() -> Path | None:
    return _find_hidraw(VID, PID_WIRED, WIRED_USAGE_PAGE_BYTES)


def find_wireless_hidraw() -> Path | None:
    return _find_hidraw(VID, PID_RECEIVER, WIRELESS_USAGE_PAGE_BYTES)


# ── Wired read (HIDIOCGFEATURE on 0x2A) ──────────────────────────────────────

class FeatureReadError(OSError):
    """The HIDIOCGFEATURE ioctl returned but the response was malformed."""


def read_wired(hidraw: Path) -> tuple[int, bytes]:
    """Read the battery percentage from a USB-direct M6 via feature report 0x2A.

    Returns (battery_percent, raw_response_bytes). Raises OSError if the ioctl
    fails or the response is malformed.
    """
    fd = os.open(str(hidraw), os.O_RDWR)
    try:
        buf = bytearray(FEATURE_REPORT_LEN)
        buf[0] = FEATURE_REPORT_ID
        fcntl.ioctl(fd, _HIDIOCGFEATURE, buf, True)
        if buf[0] != FEATURE_REPORT_ID:
            raise FeatureReadError(
                f"feature report 0x{FEATURE_REPORT_ID:02X} returned unexpected "
                f"ID 0x{buf[0]:02X}"
            )
        percent = buf[WIRED_BATTERY_OFFSET]
        if not 0 <= percent <= 100:
            raise FeatureReadError(
                f"battery byte out of range: {percent} (expected 0–100). "
                f"Full response: {bytes(buf).hex()}"
            )
        return percent, bytes(buf)
    finally:
        os.close(fd)


# ── Wireless cache (state file) ──────────────────────────────────────────────

def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically: temp file in same dir, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        # Clean up the temp file on failure so we don't leave junk behind.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_wireless_cache() -> dict[str, Any] | None:
    """Load and validate the cache. Returns None if it's absent or unreadable.
    Returns an invalid dict (without "battery_percent") if the file exists but
    is corrupted — callers should check for the key.
    """
    try:
        text = CACHE_PATH.read_text()
    except (FileNotFoundError, PermissionError):
        return None
    try:
        cache = json.loads(text)
    except json.JSONDecodeError:
        return {}  # treat as empty; caller will see missing keys
    if not isinstance(cache, dict):
        return {}
    percent = cache.get("battery_percent")
    if not isinstance(percent, int) or not 0 <= percent <= 100:
        # Drop the bad value but keep the structure so callers can detect it.
        cache.pop("battery_percent", None)
    if not isinstance(cache.get("captured_at"), (int, float)):
        cache.pop("captured_at", None)
    return cache


def write_wireless_cache(percent: int, raw: bytes) -> None:
    _atomic_write_json(CACHE_PATH, {
        "battery_percent": int(percent),
        "raw": raw.hex(),
        "captured_at": time.time(),
    })


# ── Wireless listener (long-running daemon) ──────────────────────────────────

def _is_battery_frame(data: bytes) -> bool:
    """Return True if `data` is a B6 E2 wireless status frame with the
    mouse-active flag set (so byte 5 is a meaningful battery value)."""
    return (
        len(data) >= WIRELESS_BATTERY_OFFSET + 1
        and data[0] == WIRELESS_REPORT_ID
        and data[1] == WIRELESS_EVENT_CLASS
        and data[3] == WIRELESS_VALID_FLAG
    )


def _log(verbose: bool, message: str, *, to_stderr: bool = False) -> None:
    if not verbose:
        return
    stream = sys.stderr if to_stderr else sys.stdout
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[{ts}] {message}", file=stream, flush=True)


def listen_wireless_forever(verbose: bool = False) -> int:
    """Run until terminated, persisting battery readings to the cache.

    Re-discovers the receiver hidraw if it disappears (unplug) and reappears.
    Returns the process exit code.
    """
    last_path: Path | None = None
    fd: int | None = None

    def close_fd() -> None:
        nonlocal fd
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            fd = None

    def shutdown(*_args: Any) -> None:
        close_fd()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        while True:
            path = find_wireless_hidraw()
            if path is None or not path.exists():
                close_fd()
                last_path = None
                _log(verbose, "waiting for receiver…", to_stderr=True)
                time.sleep(2.0)
                continue
            if path != last_path:
                close_fd()
                try:
                    fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK)
                except OSError as exc:
                    _log(verbose, f"open {path} failed: {exc}", to_stderr=True)
                    time.sleep(2.0)
                    continue
                last_path = path
                _log(verbose, f"listening on {path}", to_stderr=True)

            try:
                ready, _, _ = select.select([fd], [], [], 1.0)
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    close_fd()
                    last_path = None
                    continue
                raise
            if not ready:
                continue
            try:
                data = os.read(fd, 256)
            except OSError:
                close_fd()
                last_path = None
                continue
            if not _is_battery_frame(data):
                continue
            percent = data[WIRELESS_BATTERY_OFFSET]
            try:
                write_wireless_cache(percent, data)
            except OSError as exc:
                _log(verbose, f"cache write failed: {exc}", to_stderr=True)
                continue
            _log(verbose, f"battery: {percent}%  raw={data.hex()}")
    finally:
        close_fd()


# ── High-level read (transport selection) ────────────────────────────────────

def _ok_payload(transport: str, percent: int, raw: bytes, *, hidraw: str | None = None,
                age_seconds: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "device": DEVICE_NAME,
        "transport": transport,
        "battery_percent": percent,
        "raw": raw.hex() if isinstance(raw, (bytes, bytearray)) else raw,
    }
    if hidraw is not None:
        payload["hidraw"] = hidraw
    if age_seconds is not None:
        payload["age_seconds"] = age_seconds
    return payload


def _err(error: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": error, "message": message}


def _read_wired_payload() -> dict[str, Any] | None:
    """Return a payload dict for the wired path, or None if no wired device."""
    path = find_wired_hidraw()
    if path is None:
        return None
    try:
        percent, raw = read_wired(path)
    except FeatureReadError as exc:
        return _err("read_failed", f"wired feature report malformed: {exc}")
    except PermissionError as exc:
        return _err("permission_denied",
                    f"cannot open {path}: {exc} — install the udev rule")
    except OSError as exc:
        return _err("read_failed", f"wired feature read failed: {exc}")
    return _ok_payload("usb_direct", percent, raw, hidraw=path.name)


def _read_wireless_payload() -> dict[str, Any] | None:
    """Return a payload dict for the wireless path, or None if no receiver."""
    if find_wireless_hidraw() is None:
        return None
    cache = read_wireless_cache()
    if cache is None or "battery_percent" not in cache:
        return _err("no_cache",
                    "wireless cache empty — enable the listener daemon "
                    "(systemctl --user start keychron-m6-battery-listener.service) "
                    "and wait for the mouse to wake/sleep at least once")
    captured_at = cache.get("captured_at", 0.0)
    age = max(0, int(time.time() - float(captured_at)))
    if age > CACHE_STALE_SECONDS:
        hours = age / 3600
        threshold_h = CACHE_STALE_SECONDS // 3600
        return _err("stale_cache",
                    f"wireless reading is {hours:.1f}h old "
                    f"(>{threshold_h}h stale threshold)")
    raw = cache.get("raw", "")
    return _ok_payload("wireless", cache["battery_percent"], raw, age_seconds=age)


def read_battery(prefer: str = "auto") -> dict[str, Any]:
    """Read the battery using the preferred transport.

    `prefer` is one of "auto", "wired", "wireless":
      • "wired"    — only try USB-direct
      • "wireless" — only try the receiver cache
      • "auto"     — try wired (live); fall back to wireless cache; if both
                     paths are absent return a single not_found error
    """
    if prefer == "wired":
        return _read_wired_payload() or _err(
            "not_found",
            f"USB-direct M6 not present (VID={VID} PID={PID_WIRED}). "
            "Plug the M6 in via USB cable.",
        )
    if prefer == "wireless":
        return _read_wireless_payload() or _err(
            "not_found",
            f"Receiver not present (VID={VID} PID={PID_RECEIVER})",
        )

    # auto: prefer live wired, then fall through to wireless cache.
    wired = _read_wired_payload()
    if wired is not None and wired.get("ok"):
        return wired
    wireless = _read_wireless_payload()
    if wireless is not None:
        return wireless
    if wired is not None:
        return wired  # wired present but errored
    return _err("not_found", "neither USB-direct M6 nor receiver is present")


# ── CLI ──────────────────────────────────────────────────────────────────────

def _strip_raw(payload: dict[str, Any], keep_raw: bool) -> dict[str, Any]:
    if not keep_raw:
        payload.pop("raw", None)
    return payload


def _format_human(result: dict[str, Any], with_raw: bool) -> str:
    if not result.get("ok"):
        return f"error: {result.get('message', 'unknown')}"
    transport = result["transport"]
    age_note = f"  ({result['age_seconds']}s old)" if "age_seconds" in result else ""
    line = f"Keychron M6 battery: {result['battery_percent']}%  ({transport}){age_note}"
    if with_raw and "raw" in result:
        line += f"\n  raw: {result['raw']}"
    return line


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="keychron_m6_battery",
        description="Read Keychron M6 8K battery (wired or wireless).",
        epilog="See module docstring for the full protocol reference.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--json", action="store_true",
                   help="emit a single JSON object")
    p.add_argument("--watch", action="store_true",
                   help="poll repeatedly, print on change")
    p.add_argument("--listen", action="store_true",
                   help="daemon mode: capture wireless status frames into the cache")
    p.add_argument("--verbose", action="store_true",
                   help="extra logging (mainly with --listen)")
    p.add_argument("--raw", action="store_true",
                   help="include raw response bytes in output")
    p.add_argument("--interval", type=float, default=5.0, metavar="SECONDS",
                   help="poll interval for --watch (default 5)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--wired", action="store_const", const="wired", dest="prefer",
                      help="force USB-direct only")
    mode.add_argument("--wireless", action="store_const", const="wireless", dest="prefer",
                      help="force wireless cache only")
    p.set_defaults(prefer="auto")
    args = p.parse_args(argv)

    if args.listen and (args.watch or args.json):
        p.error("--listen is incompatible with --watch and --json")
    if args.interval <= 0:
        p.error("--interval must be positive")
    return args


def _result_to_exit_code(result: dict[str, Any]) -> int:
    if result.get("ok"):
        return 0
    error = result.get("error", "")
    if error in ("read_failed", "permission_denied"):
        return 2
    return 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.listen:
        return listen_wireless_forever(verbose=args.verbose)

    if args.watch:
        last_percent: int | None = None
        try:
            while True:
                result = read_battery(prefer=args.prefer)
                ts = time.strftime("%H:%M:%S")
                if result.get("ok"):
                    percent = result["battery_percent"]
                    if percent != last_percent:
                        line = f"[{ts}] {result['transport']}: {percent}%"
                        if args.raw:
                            line += f"  raw={result.get('raw', '')}"
                        if "age_seconds" in result:
                            line += f"  ({result['age_seconds']}s old)"
                        print(line, flush=True)
                        last_percent = percent
                else:
                    print(f"[{ts}] {result.get('error', 'unknown')}: "
                          f"{result.get('message', '')}",
                          flush=True, file=sys.stderr)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0

    result = read_battery(prefer=args.prefer)
    if args.json:
        print(json.dumps(_strip_raw(result, args.raw)))
    else:
        print(_format_human(result, args.raw),
              file=sys.stdout if result.get("ok") else sys.stderr)
    return _result_to_exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
