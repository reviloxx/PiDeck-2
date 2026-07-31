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

Validate a configuration without starting the future Qt launcher:

```bash
pideck --config config/pideck.yaml
```

The launcher UI and external application management are intentionally deferred to later milestones.
