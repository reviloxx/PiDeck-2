# PiDeck Development Roadmap

## Status Overview

| # | Milestone | Status |
|---|---|---|
| 1 | Project setup | Done |
| 2 | Launcher window | Done |
| 3 | Application launching | Done |
| 4 | Themes | Done |
| 5 | Settings | Done |
| 6 | HDMI-CEC and gamepad | Done |
| 7 | Packaging | Done |
| 8 | Stabilization | Open |
| 9 | Feature completion | Open |
| 10 | Optimization and release candidate | Open |
| 11 | Release 1.0 | Open |

---

## Milestone 1 (Done)

Project setup

- repository structure
- build system
- logging
- configuration loader
- dependency injection
- theme loader

---

## Milestone 2 (Done)

Launcher window

- fullscreen
- tile layout
- keyboard navigation
- focus handling

---

## Milestone 3 (Done)

Application launching

- process management
- return to launcher
- error handling

---

## Milestone 4 (Done)

Themes

- dark theme
- icons
- animations

---

## Milestone 5 (Done)

Settings

- settings window
- YAML persistence

---

## Milestone 6 (Done)

HDMI-CEC

- libCEC
- remote navigation

Gamepad navigation

---

## Milestone 7 (Done)

Packaging

- installer
- systemd service
- Raspberry Pi deployment

---

## Milestone 8 (Open)

Stabilization

- fix known bugs from real-world use on the Raspberry Pi 5
- verify the full boot flow on all targets
  (Pi 5 / Pi OS Lite, x86 / Debian minimal, VirtualBox)
- robust handling of application crashes, hangs and unexpected exits
- reliable launcher maximize/minimize and global hotkey in every
  supported application
- input edge cases (CEC reconnects, controller hot-plug, simultaneous devices)
- logging and diagnostics good enough to debug issues in the field

---

## Milestone 9 (Open)

Feature completion

- application installation and removal from the Settings menu
  (only Kodi pre-installed)
- install state shown per application, uninstalled apps hidden on the home screen
- per-application user profiles with user selection popup
- remaining Settings pages: Wi-Fi, Bluetooth, Audio, Display, Updates
- home screen settings (show / hide applications)
- remaining theme options (fonts, wallpaper, tile size)

---

## Milestone 10 (Open)

Optimization and release candidate

- measure and meet the performance goals on the Pi 5
  (startup, idle RAM, idle CPU, 60 FPS animations)
- optimize boot time and resource usage
- UI scaling checks for different screen resolutions
- update mechanism tested end to end
- documentation (installation, configuration, YAML format)
- release candidate testing on all supported platforms

---

## Milestone 11 (Open)

Release 1.0