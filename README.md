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

By default, the shutdown button exits PiDeck without powering off the host.
For an appliance session, add `--power-off-on-shutdown` to request a
noninteractive systemd power-off instead. The active user must be authorized
to power off the system; if the request fails, PiDeck remains open and shows
the error.

Validate a configuration without starting Qt:

```bash
pideck --validate-only --config config/pideck.yaml
```

On Linux, gamepad navigation uses the optional `evdev` backend. The user needs access to `/dev/input/event*`, commonly through the `input` group. The D-pad navigates, the south/A button activates, the east/B button goes Back, and Start maps to Home. Controllers can be connected or removed while PiDeck is running.

Use the arrow keys or Tab to select an application, then Enter or Space to open
it. Close the application to return to PiDeck; Alt+F4 works for most X11
applications. Some applications provide their own exit action. The configured
Home shortcut is not a global shortcut while another application has focus.
In settings dropdowns, use the arrow keys to browse, Enter to confirm, or
Escape to cancel.

## Settings

**Settings > PiDeck** shows the installed PiDeck version, its application
build time when available, and self-update status. Select **Check for updates**
to check again without installing an update. Checks run automatically at startup
and periodically; manual checks are unavailable while checking, preparing an
update, awaiting a restart, or on unsupported installations. **Updates** lists configured
applications separately. Versions and build times that the installation does
not provide are shown as unknown. PiDeck self-updates update the application,
not the operating system or image dependencies.

**Settings > Home screen** offers **YouTube** for installation. This is the
independent [Red](https://flathub.org/en/apps/com.ktechpit.red) Flatpak, not
an official YouTube application. Select it to install; PiDeck then adds it to
the home screen and starts Red in TV mode. The installation needs internet and
disk space. If Flatpak is absent, PiDeck requests an administrator password to
install the Flatpak system package; the account must be permitted to use
`sudo`. Red and its runtimes are otherwise installed for the current user from
Flathub, or an existing installation is reused. Errors are shown in Settings.
An interrupted system-package operation may need to finish before cancellation
takes effect. Red has its own license and settings; video playback, sign-in and
controller support depend on Red, the device and YouTube.

New release images support unsigned PiDeck application updates. Installing a
PiDeck update does not itself install Red. Older signed-update and development
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
- Default audio output and volume through `pactl` and a PulseAudio-compatible
  server, including PipeWire's PulseAudio compatibility service.
- Bluetooth power, eight-second discovery, pairing and connecting or
  disconnecting devices through BlueZ. PiDeck uses the first available adapter
  and asks before authorizing a pairing or confirming a displayed code; it
  does not automatically trust devices.
- NetworkManager connection status and the existing Wi-Fi setup, when
  NetworkManager is available. Advanced IP/DNS settings are not included.
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
