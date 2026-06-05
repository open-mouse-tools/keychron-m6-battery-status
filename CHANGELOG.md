# Changelog

All notable changes to this project will be documented here. Format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Plasmoid configuration UI (poll interval, low-battery threshold, hide-when-unavailable) via `contents/config/main.xml` + `configGeneral.qml`.
- `install.sh` / `uninstall.sh` one-shot setup helpers.
- `--version` flag, `--listen` / `--watch` / `--json` argument validation in the reader script.
- ISO-format timestamps in `--verbose` listener logs.
- Documentation: troubleshooting, verify-each-piece, uninstall sections in README; CHANGELOG.

### Changed
- Reader script: stricter cache validation (rejects out-of-range `battery_percent`); atomic cache writes via `tempfile.mkstemp` + `fsync`; clearer error categorisation (`not_found`, `permission_denied`, `read_failed`, `no_cache`, `stale_cache`).
- Reader script: `read_battery()` refactored into smaller helpers; clearer fallback order for `prefer="auto"`.
- Plasmoid QML: removed unused imports (`PlasmaCore`, `PlasmaExtras`); polling interval pulled from `Plasmoid.configuration`; expanded view shows a contextual hint when the wireless cache is empty or udev rules are missing.
- Udev rule renamed from `99-hi-drawbridge-keychron-m6.rules` to `99-keychron-m6.rules` (the file is not actually a hi-drawbridge artifact).
- Plasmoid script source-of-truth: `plasmoid/contents/scripts/keychron_m6_battery.py` is now a symlink to `dev-scripts/keychron_m6_battery.py`. `kpackagetool6` dereferences during install, so the installed plasmoid still has a real file.

### Fixed
- `--wired` returning a wireless cache message in some corner cases.
- Cache file `.tmp` left behind if the listener was killed mid-write.

## [0.1.0] — 2026-05-19

### Added
- Initial KDE Plasma 6 plasmoid showing M6 battery percentage in the panel.
- Python reader script with both transports:
  - **Wired**: `HIDIOCGFEATURE` on report `0x2A` of the M6's vendor interface (usage page `0xFF0B`).
  - **Wireless**: passive listener daemon that captures spontaneous `B6 E2 …` input frames from the receiver's vendor interface (usage page `0xFFC1`).
- Udev rule granting `uaccess` for both transport PIDs (`0xD049` wired, `0xD028` receiver).
- Systemd user service to run the wireless listener.
- README documenting installation and the reverse-engineered protocol.

### Reverse-engineering origin

The protocol was discovered by:
1. Reading the Keychron Launcher's minified JavaScript bundle (`launcher.keychron.com`).
2. Capturing the Launcher's actual USB traffic with `usbmon` during a live battery read.

See [`script-outputs/`](script-outputs/) for the raw probe outputs.
