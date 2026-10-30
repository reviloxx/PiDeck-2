#!/usr/bin/env python3
"""Add x86-specific application tiles to the appliance YAML."""

import argparse
from pathlib import Path

import yaml


def configure(path: Path, definitions: Path, *, nomachine: bool) -> None:
    """Include configured x86 applications whose packages are installed."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    x86_apps = yaml.safe_load(definitions.read_text(encoding="utf-8"))
    applications = list(x86_apps["always"])
    if nomachine:
        applications.extend(x86_apps["nomachine"])
    data["applications"].extend(applications)
    data["home"]["visible_applications"].extend(item["id"] for item in applications)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main() -> None:
    """Parse the target YAML and optional player flag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("definitions", type=Path)
    parser.add_argument("--nomachine", action="store_true")
    args = parser.parse_args()
    configure(args.config, args.definitions, nomachine=args.nomachine)


if __name__ == "__main__":
    main()
