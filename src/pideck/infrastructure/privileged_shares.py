"""Root-side Samba share management for the restricted updater broker."""

from collections.abc import Callable, Mapping
import json
import logging
import os
from pathlib import Path
import re
import subprocess
from typing import Any


_LOGGER = logging.getLogger(__name__)
_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
_UUID = re.compile(r"[0-9A-Fa-f][0-9A-Fa-f-]{3,63}")
_SHARE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_DATA_MOUNT_ROOTS = ("/media/", "/mnt/", "/run/media/")
_DEFAULT_ROOT = Path("/var/lib/pideck/samba")

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=60, check=False, env={**os.environ, **_ENV},
    )


def _safe_path(path: str) -> Path:
    """Accept only absolute data-drive paths without traversal."""
    if "\x00" in path:
        raise ValueError("Share path must be under a data mount location")
    candidate = Path(path)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Share path must be an absolute data mount path")
    text = str(candidate)
    if text != "/mnt" and not any(text.startswith(root) for root in _DATA_MOUNT_ROOTS):
        raise ValueError("Share path must be under a data mount location")
    return candidate


def _resolved_share_path(path: str) -> Path:
    """Resolve symlinks and require a real directory under a data mount root."""
    candidate = _safe_path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"Share path is missing: {error}") from error
    if not resolved.is_dir():
        raise ValueError("Share path must be a directory")
    return _safe_path(str(resolved))


def _media_user(path: Path) -> str:
    """Derive the appliance username from ``/media/<user>/...`` when present."""
    parts = path.parts
    if len(parts) >= 3 and parts[1] == "media" and parts[2] not in {"", ".", ".."}:
        return parts[2]
    return "pideck"


def _unique_share_name(name: str, taken: set[str]) -> str:
    """Return ``name`` or ``name_2``… that is not in ``taken``."""
    if name not in taken:
        return name
    for index in range(2, 1000):
        suffix = f"_{index}"
        base = name[: max(1, 64 - len(suffix))]
        candidate = f"{base}{suffix}"
        if _SHARE_NAME.fullmatch(candidate) is not None and candidate not in taken:
            return candidate
    raise ValueError("Share name is already in use")


def render_shares_conf(shares: Mapping[str, Mapping[str, str]]) -> str:
    """Render Samba share stanzas for the managed drop-in file."""
    lines = [
        "# Managed by PiDeck. Do not edit; Settings > Drives rewrites this file.",
        "",
    ]
    for uuid in sorted(shares):
        entry = shares[uuid]
        name = entry["name"]
        path = entry["path"]
        user = _media_user(Path(path))
        lines.extend(
            [
                f"[{name}]",
                f"   path = {path}",
                "   browseable = yes",
                "   read only = no",
                "   guest ok = yes",
                f"   force user = {user}",
                f"   force group = {user}",
                "   create mask = 0664",
                "   directory mask = 0775",
                "",
            ]
        )
    return "\n".join(lines)


class ShareOperations:
    """Enable and disable guest Samba exports for mounted data drives."""

    def __init__(
        self,
        root: Path = _DEFAULT_ROOT,
        runner: Runner = _run,
    ) -> None:
        """Create the share store under ``root``; ``runner`` is injectable for tests."""
        self.root = root
        self.state_path = root / "shares.json"
        self.conf_path = root / "shares.conf"
        self._runner = runner

    def enable(self, uuid: str, path: str, name: str) -> dict[str, object]:
        """Add or replace a share and reload Samba."""
        cleaned_uuid = uuid.strip()
        if _UUID.fullmatch(cleaned_uuid) is None:
            raise ValueError("Invalid share UUID")
        if _SHARE_NAME.fullmatch(name) is None:
            raise ValueError("Invalid share name")
        target = _resolved_share_path(path)
        shares = self._load()
        taken = {
            entry["name"]
            for other_uuid, entry in shares.items()
            if other_uuid != cleaned_uuid
        }
        existing = shares.get(cleaned_uuid)
        if name not in taken:
            final_name = name
        elif existing is not None:
            # Keep a stable export name when a sibling still owns the requested one.
            final_name = existing["name"]
        else:
            final_name = _unique_share_name(name, taken)
        shares[cleaned_uuid] = {"name": final_name, "path": str(target)}
        self._write(shares)
        self._reload()
        _LOGGER.info("Enabled Samba share uuid=%s name=%s path=%s", cleaned_uuid, final_name, target)
        return {"uuid": cleaned_uuid, "name": final_name, "path": str(target)}

    def disable(self, uuid: str) -> dict[str, object]:
        """Remove a share when present and reload Samba."""
        cleaned_uuid = uuid.strip()
        if _UUID.fullmatch(cleaned_uuid) is None:
            raise ValueError("Invalid share UUID")
        shares = self._load()
        shares.pop(cleaned_uuid, None)
        self._write(shares)
        self._reload()
        _LOGGER.info("Disabled Samba share uuid=%s", cleaned_uuid)
        return {"uuid": cleaned_uuid}

    def _load(self) -> dict[str, dict[str, str]]:
        try:
            document = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Unable to read share state: {error}") from error
        if not isinstance(document, dict) or document.get("version") != 1:
            raise RuntimeError("Invalid share state")
        values = document.get("shares")
        if not isinstance(values, dict):
            raise RuntimeError("Invalid share state")
        shares: dict[str, dict[str, str]] = {}
        for key, entry in values.items():
            if not isinstance(key, str) or _UUID.fullmatch(key) is None:
                raise RuntimeError("Invalid share state")
            if not isinstance(entry, dict):
                raise RuntimeError("Invalid share state")
            name = entry.get("name")
            path = entry.get("path")
            if not isinstance(name, str) or _SHARE_NAME.fullmatch(name) is None:
                raise RuntimeError("Invalid share state")
            if not isinstance(path, str):
                raise RuntimeError("Invalid share state")
            shares[key] = {"name": name, "path": str(_safe_path(path))}
        return shares

    def _write(self, shares: Mapping[str, Mapping[str, str]]) -> None:
        document: dict[str, Any] = {
            "version": 1,
            "shares": {uuid: {"name": entry["name"], "path": entry["path"]} for uuid, entry in shares.items()},
        }
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            self.conf_path.write_text(render_shares_conf(shares), encoding="utf-8")
            os.replace(temporary, self.state_path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"Unable to write share state: {error}") from error

    def _reload(self) -> None:
        result = self._runner(["systemctl", "reload", "smbd.service"])
        if result.returncode == 0:
            return
        # Fresh images may not have started smbd yet; enable+start once.
        start = self._runner(["systemctl", "start", "smbd.service"])
        if start.returncode:
            message = (result.stderr or result.stdout or start.stderr or start.stdout).strip()
            raise RuntimeError(message or "Unable to reload Samba")
