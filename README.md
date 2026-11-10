# PiDeck

PiDeck is a TV-first launcher for Raspberry Pi and x86-64 Linux appliances.

<img width="2560" height="1440" alt="Bildschirmfoto vom 2026-08-01 18-41-12" src="https://github.com/user-attachments/assets/60937f71-c7ea-4437-a1d3-245540bd1b13" />

For the Raspberry Pi 5 image builder, app inventory and validation status, see
[image preparation](docs/raspberry-pi-image.md). An image has been built, but
has not yet been tested on a Raspberry Pi.

For the experimental private x86-64 image builder, see
[x86 image preparation](docs/x86-image.md). It has not yet been built or
boot-tested.


## Development

Create a virtual environment and install the development dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Run the foundation tests:

```bash
pytest
```

Start the fullscreen launcher:

```bash
pideck --config config/pideck.yaml
```

The home screen's shutdown button asks for confirmation first. By default,
confirming exits PiDeck without powering off the host.
For an appliance session, add `--power-off-on-shutdown` to request a
noninteractive systemd power-off instead. The active user must be authorized
to power off the system; if the request fails, PiDeck remains open and shows
the error.

Validate a configuration without starting Qt:

```bash
pideck --validate-only --config config/pideck.yaml
```

On Linux, gamepad navigation uses the optional `evdev` backend. The user needs access to `/dev/input/event*`, commonly through the `input` group. The D-pad navigates, the south/A button activates, the east/B button goes Back, and the Guide (Xbox) button opens the session overlay while an application is running. Controllers can be connected or removed while PiDeck is running.

HDMI-CEC navigation uses the Linux CEC interface: a Pulse-Eight USB adapter on
x86 or built-in CEC on Raspberry Pi. **Settings → Input** offers direct-TV
HDMI port selection, automatic or manual connection, and optional TV input
switching. Auto-connect defaults to on; switching defaults to off. PiDeck
keeps the CEC adapter for every launched application and forwards the remote's
arrows, OK and Return (Samsung Back) as arrow keys, Enter and Escape, plus
Play, Pause, Stop, Rewind and Fast-forward as media keys (X11 XTest; no extra
packages). Pause is sent as the play/pause media key so Chromium-based apps and
Kodi respond. In YouTube, Stop is sent as Escape so it can exit playback.
Exit and Root Menu open the session
overlay. Applications
therefore do not receive native CEC; catalog `cec: release` no longer hands off
the adapter. Before launching Kodi, PiDeck disables Kodi's CEC peripherals so
libCEC does not contend for `/dev/cec0`.

**Settings → Input → Keyboard layout** selects German (QWERTZ, the default)
or English (US). In the appliance session PiDeck applies the choice with
`setxkbmap` at startup, when it changes and whenever a keyboard is connected,
and the controller-operated on-screen keyboard follows the same layout,
including ä, ö, ü and ß for German. The choice is stored in `keyboard.yaml`
next to `pideck.yaml`. When run from a desktop session, PiDeck stores the
choice but leaves the desktop's keyboard layout alone. Images built from this
release also default the X server and, on Raspberry Pi, the console to German.

The adapter, TV, cabling and active local session must support CEC. The Debian
libCEC backend only opens `/dev/cec0`, and some hardware cannot override its
automatically detected HDMI address. Existing images need a one-time system
setup; the application updater cannot install system packages or udev rules.
Images built before v0.3.10 without a release tag cannot self-update. See the
image guides for setup and limitations. Hardware operation is not yet verified.

Use the arrow keys or Tab to select an application, then Enter or Space to open
it. While an application is running, the Windows (Super) key, Xbox Guide button
or TV Exit / Root Menu key opens an overlay that asks whether to return home.
Confirm closes the application; Cancel or the same hotkey again stays in the
app. On images that include the picom compositor, the overlay blurs the current
app behind the dialog; otherwise it uses a dark backdrop. The Samsung Return
button navigates Back inside the app.
Alt+F4 and in-app exits still return to PiDeck as well. In settings dropdowns,
use the arrow keys to browse, Enter to confirm, or Escape to cancel. Escape,
Backspace, controller B and the remote's Return button go back in Settings and
dialogs; Backspace still deletes in text fields and the on-screen keyboard. On
the home screen they do nothing, so PiDeck cannot be shut down by an accidental
Back press. The mouse pointer is hidden while you navigate with the keyboard, a
controller or the TV remote (including inside running apps such as YouTube),
and reappears when the mouse moves or is clicked.
Pointing at a control selects it, so only one item is highlighted regardless of
the input device. If Bluetooth is unavailable,
**Settings > System > Bluetooth** still opens and shows the reason.

Typography can be adjusted per theme in the YAML configuration under
`themes[].fonts`. All sizes are pixels: `heading_size` and `body_size` apply
to the home screen, while `settings_body_size` and `settings_heading_size`
apply to Settings and its dialogs, defaulting to 22 and 30 respectively when
omitted. The `family` applies everywhere. Edit the active configuration and restart PiDeck
to apply the changes. Older releases do not recognize custom settings font
sizes; remove these two keys before rolling back to a release before v0.3.7.

## Settings

**Settings > About** shows the installed PiDeck version, its application
build time when available, and self-update status. Select **Check for updates**
to check again without installing an update. Checks run automatically at startup
and periodically; manual checks are unavailable while checking, preparing an
update, awaiting a restart, or on unsupported installations. When an update is
available, **What's new** lists the release notes of every release since the
installed version; scroll them with the arrow keys, controller or remote.
Release notes are written in English in [`CHANGELOG.md`](CHANGELOG.md) and
published with each release. PiDeck self-updates update the application, not
the operating system or image dependencies.

**Settings > Applications** is the single place to manage applications. It
lists the applications on the home screen in tile order, followed by every
other supported application: Kodi, YouTube, Steam Link, Chromium, Chrome,
Brave, Firefox, NoMachine and RetroArch. Each row shows whether the application
is on the home screen, its installed version and update status, or why it
cannot be installed. Missing build dates are hidden; Flatpak applications
without a version show their commit, and other unavailable versions are shown
as unknown.

Select a row to open its options: **Move** to change the tile order, **Update**,
**Install**, **Remove**, or cancel a running installation or update. **Move**
highlights the row in the list: Up and Down move it, OK saves the new order and
Back restores the previous one. Installed applications are always shown on the
home screen. Images only preinstall Kodi and YouTube. While an application
installs, its row shows the current step, a progress bar and a busy indicator;
installations can be cancelled while downloading, removals cannot. Installing
adds the application to the home screen and removing takes it off. Removing
keeps the application's settings and data, so they come back after
reinstalling. Installation needs internet and disk space; errors are shown in
Settings. The tile order is saved by reordering the existing `applications`
list; an empty `home.visible_applications` list means show all, so older
releases still read the settings.

YouTube is the independent [Red](https://flathub.org/en/apps/com.ktechpit.red)
Flatpak, not an official YouTube application; it starts in TV mode. Red has its
own license and settings; video playback, sign-in and controller support
depend on Red, the device and YouTube.

The applications and how to install them on each architecture are defined in
[`assets/catalog/applications.yaml`](assets/catalog/applications.yaml); adding an
application needs no code changes. Images built from v0.5.0 or later install
Debian packages, the checksum-verified NoMachine Player package and system
Flatpaks through the image's restricted root service. That service accepts only
catalog application IDs, never package names or commands. Packages must come
from repositories the image already configures (Debian, Raspberry Pi OS, Google
Chrome and Brave), and the service downloads `.deb` packages only from
NoMachine's servers, so an altered release cannot make it install software from
elsewhere. On older images and on desktops, only Flatpak applications
can be installed, for the current user; others show that a newer image is
required. If Flatpak itself is missing there, PiDeck requests an administrator
password to install it; the account must be permitted to use `sudo`.

PiDeck, the system locale and time zone default to German (`de_DE.UTF-8`,
Europe/Berlin) on new images and fresh configurations. Applications follow the
system language; Firefox additionally gets its German language pack and policy,
and Kodi gets German first-run settings and its bundled German language add-on.
Existing configurations keep their language, and an application's own settings
are never overwritten.

New release images support unsigned PiDeck application updates. Installing a
PiDeck update does not itself install any application. Older signed-update and development
images cannot switch to the new unsigned updater through an application update;
they need a separate system migration or a new image. Updates use HTTPS and
check the bundle's size and hash, but do not authenticate the publisher: anyone
able to replace both the release and its manifest can distribute altered code.

**Settings > System** provides:

- X11 display output, resolution and refresh-rate selection. A temporary mode
  reverts after 10 seconds unless confirmed, including when PiDeck exits during
  the change. Confirmed modes are stored in `display.yaml` beside the PiDeck
  configuration and restored on later X11 sessions if still available. Wayland
  display configuration is not supported.
- System audio output and volume through `pactl` and a PulseAudio-compatible
  server, including PipeWire's PulseAudio compatibility service. When HDMI is
  available, Settings lists HDMI ports only (including inactive NVIDIA ports
  such as HDMI vs HDMI 2). An **HDMI mode** chooses the path:
  **Surround PCM 5.1 (AVR)** for PC→AVR→TV (Digital Surround 5.1 + Kodi 5.1,
  passthrough off), or **Bitstream (TV ARC)** for PC→TV→ARC (Digital Stereo +
  Kodi passthrough/AC3). Per application, Settings can also force **2.1 PCM**
  or **5.1 PCM** (never bitstream); that override is applied at launch. The
  mode, default output and per-app layouts are stored in `audio.yaml`.
- Bluetooth power, eight-second discovery, pairing and connecting or
  disconnecting devices through BlueZ. PiDeck uses the first available adapter
  and asks before authorizing a pairing or confirming a displayed code; it
  does not automatically trust devices.
- Drives: mount and unmount USB sticks, external disks and additional
  internal partitions through udisks2. Select a drive for options. Use the
  stable path `/media/<user>/by-uuid/<UUID>` in Kodi sources (shown in
  Settings > Drives after mount); udisks may also create a changing
  `/media/<user>/<label>` directory, and a convenience
  `/media/<user>/by-label/<label>` link exists when the label is unique.
  Mounted drives can be shared on the LAN as guest read-write Samba
  exports (`smb://<hostname>/<ShareName>`, for example from Linux Mint
  Files). Disks with swap or any filesystem mounted outside `/media`,
  `/mnt` or `/run/media` (such as root, boot, EFI or home) are never
  listed. Unmounting a removable drive also powers it off so it can be
  removed safely. Mounting requires an image built from v0.4.0 or later
  (udisks2 and a polkit rule for the session user's `plugdev` group).
  Network sharing requires a current image that includes Samba and the
  privileged share actions; older images omit the share options.
- NetworkManager connection status and the existing Wi-Fi setup, when
  NetworkManager is available. Advanced IP/DNS settings are not included.
- Read-only system information: PiDeck, OS and kernel versions, uptime, CPU
  model, cores and temperature, memory and storage usage, GPUs with their
  kernel driver and version, hardware video decoding codecs reported by
  `vdpauinfo`/`vainfo`, ALSA sound cards with their driver, and network
  addresses. Facts a platform or image cannot provide show as unavailable.
- Confirmed restart and shutdown requests to systemd. These system actions
  affect the host even when the launcher's ordinary shutdown button is set to
  exit PiDeck only. Requests are noninteractive and require the active user to
  have the necessary permissions; failures remain visible in Settings.

Select a system row with the mouse, keyboard or controller to open its
settings or confirmation dialog. Directional input also moves between the
controls and action buttons inside each system window.

System controls report unavailable services, unsupported sessions and
permission errors rather than requesting elevated privileges. New appliance
images include the needed Bluetooth, Qt D-Bus and audio tools; a PiDeck
application update does not install system packages. Hardware-specific
behavior, especially display rollback and Bluetooth pairing, should be
verified on the target device.
