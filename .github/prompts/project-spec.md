# PiDeck Project Specification

## Vision

PiDeck is a TV-first launcher for Linux-based living room devices.

It turns a minimal Linux install into a living room appliance.
The Linux desktop must never be visible during normal operation.

---

## Supported Platforms

| Role | Platform | Base system | Image |
|---|---|---|---|
| Primary | Raspberry Pi 5 | Raspberry Pi OS Lite 64-bit | Raspberry Pi image |
| Secondary | x86 (64-bit) hardware | Debian minimal | x86 image (optimized for Intel and NVIDIA hardware) |

Notes:

- All targets are based on a minimal system without a desktop environment.
  PiDeck brings its own display stack (e.g. a minimal Wayland compositor
  or X server, started automatically).
- Platform differences (hardware video decoding, CEC adapter, GPU drivers)
  live in the image builders; the application core stays capability-based
  (arch catalog installs, runtime GPU/CEC probes).
- UI and logic development use the host desktop launcher plus tests.
  Appliance validation uses real hardware. The same x86 image may be
  smoke-booted in a VM optionally; there is no separate VirtualBox image.

---

## Release Model

PiDeck is delivered in two independent ways: platform images and
application updates.

### Platform images

- There are two independent images, one per target platform
  (Raspberry Pi, x86).
- Only platform-specific changes, such as hardware support or boot
  optimizations, require a new image version, and only for the affected
  platform.
- Each image has its own version, independent of the other images and of
  the application version.
- An image ships with a specific application release, which can then be
  updated in place.

### Application updates

- Any change to the PiDeck application itself is shipped via the built-in
  update function and reaches all platforms.
- Every application release must run on all currently supported images.
  Platform features that are unavailable must degrade gracefully.
- Saved configuration must stay readable by older application releases.

### Decision rule

| Change | Delivery |
|---|---|
| Change to the PiDeck application | Application update (all platforms) |
| Platform-specific change (e.g. hardware support, boot optimization) | New image version for the affected platform only |

---

## Boot Flow

Power On → Automatic Login → Launcher → Application → Launcher

---

## Applications

### Supported applications

- Kodi
- Red (YouTube)
- Steam Link
- Chromium
- Chrome
- Brave
- Firefox
- NoMachine
- RetroArch

### Installation model

- Only **Kodi** and **Red** are pre-installed.
- All other applications are optional and can be installed
  (and removed) from the Settings menu.
- Applications that are not installed are not shown on the home screen.
- The Settings menu shows the install state of every supported application.
- Installation uses the system package manager (apt, Flatpak, or a
  per-app install method defined in the app's YAML).
- Installation requires network access. Progress and errors are shown
  in the UI.

### Configuration

- Applications are defined in YAML (one file per application or one
  combined file).
- Each entry defines: name, icon, preferred input device, launch command,
  install method and package, and optional user profiles.
- Per-application user profiles with specific launch parameters are
  optional.
  - If profiles exist, a user selection popup appears on launch.
  - If none exist, the application starts directly.
- Adding a new application requires no code changes.

---

## User Interface

The interface should resemble a modern Smart TV.
It adapts to the actual screen resolution and scales accordingly.

Requirements:

- fullscreen
- large icons
- large text
- dark theme
- smooth animations
- remote friendly

---

## Input Devices

Priority:

1. HDMI-CEC remote
2. Game controller
3. Keyboard
4. Mouse

---

## HDMI-CEC

Support via libCEC.

Desired features:

- navigation
- OK
- Back
- media buttons
- optional power management

Availability depends on the platform (Pi 5 has native CEC; x86 needs a
USB-CEC adapter). Features without hardware support degrade gracefully.

---

## Launcher

The launcher displays configurable tiles.

Tile design:

- modern look with abstract versions of the official application icons
- glowing highlight on the focused tile
- a minimalistic Material icon showing the preferred input device

Behavior:

- Starting an application minimizes the launcher.
- Closing the application maximizes the launcher again.

Example tiles:

| Application | Input icon |
|---|---|
| Kodi | TV remote |
| Red | TV remote |
| Steam Link | Gamepad |
| Browser | Mouse |
| NoMachine | Mouse |
| RetroArch | Gamepad |

Fixed buttons (not application tiles):

- Settings
- Shutdown

---

## Themes

Configurable:

- colors
- fonts
- icons
- wallpaper
- tile size

---

## Settings

- Wi-Fi
- Bluetooth
- Audio
- Display
- Applications (install / remove, see Installation model)
- Updates
- Themes
- Home screen (show / hide applications, tile order)

---

## Performance Goals

| Metric | Target |
|---|---|
| Startup | < 2 s (launcher visible after boot completes) |
| Idle RAM | < 150 MB |
| Idle CPU | < 2 % |
| Animations | 60 FPS |

Targets are measured on the Raspberry Pi 5.

---

## Long-Term Goals

- Plugin system
- Home Assistant integration
- Weather widgets
- Music controls
- Recently used applications