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

Milestone 2 provides the fullscreen tile launcher, keyboard navigation, focus handling, and intent signals. External application management is deferred to Milestone 3.
