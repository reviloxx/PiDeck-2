# PiDeck

PiDeck is a TV-first launcher for Raspberry Pi OS.

<img width="2560" height="1440" alt="Bildschirmfoto vom 2026-08-01 18-41-12" src="https://github.com/user-attachments/assets/60937f71-c7ea-4437-a1d3-245540bd1b13" />

For the Raspberry Pi 5 image builder, app inventory and validation status, see
[image preparation](docs/raspberry-pi-image.md). An image has been built, but
has not yet been tested on a Raspberry Pi.


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

On Linux, gamepad navigation uses the optional `evdev` backend. The user needs access to `/dev/input/event*`, commonly through the `input` group. The D-pad navigates, the south/A button activates, the east/B button goes Back, and Start maps to Home. Controllers can be connected or removed while PiDeck is running.
