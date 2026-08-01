# PiDeck

PiDeck is a TV-first launcher for Raspberry Pi OS. Milestone 1 provides the typed configuration, theme, logging, and dependency-injection foundation.

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

Validate a configuration without starting Qt:

```bash
pideck --validate-only --config config/pideck.yaml
```

Milestone 2 provides the fullscreen tile launcher, keyboard navigation, focus handling, and intent signals. Milestone 3 adds shell-free application launching, process-group supervision, profile selection, replacement confirmation, and return to the launcher after exit. Milestone 4 adds theme-driven wallpaper, bundled SVG icons, animated focus glow, running-tile styling, and reduced-motion support. Milestone 5 adds fullscreen settings, home-screen visibility, theme selection, reduced-motion editing, and atomic YAML persistence.

Bundled visual assets live under `assets/` and are referenced from `config/pideck.yaml`. Replace those paths with custom assets when creating a theme.

Application commands may be a plain executable or a command prefix such as `/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=kodi tv.kodi.Kodi`. PiDeck splits these arguments safely and never invokes a shell.

When an application starts, PiDeck keeps the launcher fullscreen and displays a tile spinner until its X11 window is visible. The launcher is hidden only after that readiness check succeeds.
