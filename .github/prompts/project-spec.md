# PiDeck Project Specification

## Vision

PiDeck is a TV-first launcher for Raspberry Pi.

It transforms Raspberry Pi OS into a living room appliance.

The Linux desktop should never be visible during normal operation.

---

## Supported Platforms

Primary:

- Raspberry Pi 5
- Raspberry Pi OS 64-bit

Development:

- Linux Mint

---

## Boot Flow

Power On

↓

Automatic Login

↓

Launcher

↓

Application

↓

Launcher

---

## Applications

Initially support:

- Kodi
- Steam Link
- Chromium
- Firefox
- NoMachine
- RetroArch

Applications are configured through YAML.
For each application different user with specific launch parameters can be configured.
This is optional. If there are users there is a user selection pop-up when starting the application.
No code changes should be required to add new applications.

---

## User Interface

The interface should resemble a modern Smart TV.
The user interface should adapt to the actual screen resolution.

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

## HDMI CEC

Support libCEC.

Desired features:

- navigation
- OK
- Back
- optional power management

---

## Launcher

The launcher displays configurable tiles.
The tiles have a modern design and include the official application icons.
Navigation to a tile should highlight it with a glowing effect.
The currently running application should be highlighted differently.
The tiles also show the prefferred input device as a minimalistic material icon.
When an application is started the launcher should minimize.
When the application is closed the launcher should maximize.
There is a hotkey to maximize the launcher from any application.
If an application is running and the user selects a different one there should be a popup to ask if the running application should be closed.

Example:

📺 Kodi - tv remote icon

🎮 Steam - gamepad icon

🌍 Browser - mouse icon

🖥 NoMachine - mouse icon

🎲 RetroArch - gamepad icon

Additional buttons which are not part of the application tiles:

⚙ Settings

⏻ Shutdown

---

## Themes

Support configurable:

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
- Updates
- Themes
- Home screen settings like showing / hiding applications

---

## Performance Goals

Startup: <2 seconds
Idle RAM: <150 MB
Idle CPU: <2%
Animations: 60 FPS

---

## Long-Term Goals

Possible future additions:

- Plugin system
- Home Assistant integration
- Weather widgets
- Music controls
- Recently used applications